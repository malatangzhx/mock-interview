from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import SessionStore
from .llm import AIConnectionError, OpenAICompatibleClient, PROVIDER_PRESETS
from .models import AIConnectionRequest, AnswerRequest, CandidateAnalysisRequest, ConfirmRequest, CounterQuestionRequest, FinishRequest, JobAnalysisRequest, ProfileRequest, Session, SessionStatus, Turn, UserQuestionImportRequest, BankRequest, DraftEntry, ConfirmImportRequest, now_iso
from .question_import import parse_question_file, parse_question_text
from .services import BUILTIN_JD, BUILTIN_JOBS, create_plan, extract_resume, make_report, next_turn, parse_candidate, parse_job

BASE = Path(__file__).resolve().parent.parent
app = FastAPI(title="AI 模拟面试", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
store = SessionStore(Path(os.environ.get("MOCKLAB_DB_PATH", str(BASE / "data" / "interviews.db"))))
# Credentials never enter Session/SQLite. They disappear when this process stops.
ai_clients: dict[str, OpenAICompatibleClient] = {}

REALTIME_MODEL = "gpt-realtime-2.1"
QWEN_REALTIME_MODEL = "qwen3.5-omni-flash-realtime"
REALTIME_STYLES = {
    "professional": "专业、沉稳、简洁，像经验丰富的技术面试官。语速自然，不要朗读腔。",
    "warm": "温和、耐心、鼓励但不教学，像亲切而专业的 HR。保持真实面试节奏。",
    "pressure": "直接、克制、追问有力度，像压力面试官，但始终尊重候选人，不讽刺。",
}


def require_session(session_id: str) -> Session:
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "会话不存在或已删除")
    return session


def qwen_realtime_url(client: OpenAICompatibleClient) -> str | None:
    explicit = (getattr(client, "realtime_url", None) or "").strip().rstrip("/")
    if explicit:
        return explicit
    parsed = urlparse(getattr(client, "endpoint", ""))
    if parsed.hostname and parsed.hostname.endswith(".maas.aliyuncs.com"):
        return f"https://{parsed.netloc}/api/v1/webrtc/realtime"
    return None


def validate_qwen_realtime_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".maas.aliyuncs.com"):
        raise HTTPException(422, "千问 Realtime URL 必须是百炼业务空间的 HTTPS 地址（*.maas.aliyuncs.com）")
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise HTTPException(422, "千问 Realtime URL 不能包含账号、查询参数或 # 片段")
    if not parsed.path.rstrip("/").endswith("/api/v1/webrtc/realtime"):
        raise HTTPException(422, "千问 Realtime URL 路径应以 /api/v1/webrtc/realtime 结尾")
    return value.rstrip("/")


def voice_capability(client: OpenAICompatibleClient) -> dict:
    endpoint = getattr(client, "endpoint", "")
    provider = getattr(client, "provider", "")
    if provider in {"openai", "openai_responses"} and endpoint.startswith("https://api.openai.com/"):
        return {"mode": "native", "provider": "openai", "model": REALTIME_MODEL,
                "label": f"OpenAI {REALTIME_MODEL} 原生语音"}
    if provider == "qwen":
        if qwen_realtime_url(client):
            return {"mode": "native", "provider": "qwen", "model": QWEN_REALTIME_MODEL,
                    "label": f"千问 {QWEN_REALTIME_MODEL} 原生语音"}
        return {"mode": "cascade", "provider": "qwen", "model": getattr(client, "model", "qwen"),
                "label": "千问自动级联语音（填写业务空间 Realtime URL 可启用原生语音）"}
    if provider in {"deepseek", "doubao"}:
        return {"mode": "cascade", "provider": provider, "model": getattr(client, "model", provider),
                "label": f"{provider} 自动级联语音"}
    return {"mode": "text", "provider": provider, "model": getattr(client, "model", ""),
            "label": "文字 AI 对话"}


def realtime_supported(client: OpenAICompatibleClient) -> bool:
    return voice_capability(client)["mode"] == "native"


def realtime_instructions(session: Session, style: str) -> str:
    candidate = session.candidate
    job = session.job
    topics = session.plan.topics if session.plan else []
    plan_text = "\n".join(f"{i + 1}. {topic.question}" for i, topic in enumerate(topics))
    skills = "、".join((candidate.skills if candidate else [])[:12]) or "未提供"
    projects = "；".join((candidate.projects if candidate else [])[:6]) or "未提供"
    return f"""你是“林老师”，正在进行一对一中文模拟面试。你的声音风格：{REALTIME_STYLES[style]}

岗位：{job.title if job else '未指定'}；目标公司：{job.target_company if job else '通用'}。
候选人技能：{skills[:1200]}
候选人项目：{projects[:1800]}

本场主问题计划：
{plan_text[:7000]}

必须遵守：
1. 这是实时口语面试。每次只说一到两句，先自然承接，再问一个问题；不要长篇解释。
2. 候选人每次回答结束后，在开口前必须调用 record_interview_answer，answer 参数应尽量忠实保留候选人的完整回答，不要只写摘要。
3. 工具会返回 next_question。未结束时必须以它为本轮唯一问题，可以加一句很短的自然过渡，但不得改写或提前询问其他主问题。
4. 同一主题最多追问两次。候选人说“跳过”时照常调用工具并进入下一题。
5. 面试中不公布分数、不纠错、不提供标准答案。需要深挖时用简短、具体的追问。
6. 候选人可以随时打断你；被打断后立即停止，并认真听完再继续。
7. 使用自然普通话，允许自然停顿和轻微语气变化，避免播音腔、列表腔和机械复述。
8. 工具返回 finished=true 时，用不超过两句话自然结束面试，不再提新问题。"""


def realtime_session_config(session: Session, style: str) -> dict:
    return {
        "type": "realtime",
        "model": REALTIME_MODEL,
        "instructions": realtime_instructions(session, style),
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "noise_reduction": {"type": "far_field"},
                "transcription": {"model": "gpt-4o-mini-transcribe", "language": "zh"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "auto",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {"voice": "marin", "speed": 1.0},
        },
        "reasoning": {"effort": "low"},
        "max_output_tokens": 384,
        "tools": [{
            "type": "function",
            "name": "record_interview_answer",
            "description": "每次候选人回答结束后、面试官继续说话前必须调用。记录完整回答并取得下一道受控问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "候选人刚才的完整回答，尽量逐字忠实记录；不要评价或概括。",
                    }
                },
                "required": ["answer"],
                "additionalProperties": False,
            },
        }],
        "tool_choice": "auto",
        "truncation": "auto",
    }


def qwen_session_config(session: Session, style: str) -> dict:
    tool = realtime_session_config(session, style)["tools"][0]
    return {
        "modalities": ["text", "audio"],
        "voice": "Cherry",
        "instructions": realtime_instructions(session, style),
        "turn_detection": {
            "type": "semantic_vad",
            "threshold": 0.5,
            "silence_duration_ms": 800,
        },
        "enable_input_audio_transcription": True,
        "tools": [tool],
        "tool_choice": "auto",
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"builtin_jd": BUILTIN_JD, "ai_providers": PROVIDER_PRESETS})


@app.get("/health")
def health():
    return {"ok": True, "mode": "offline-ready", "question_bank": store.question_bank_stats()}


@app.get("/api/question-bank")
def question_bank_stats():
    return store.question_bank_stats()


@app.get("/api/question-bank/sources")
def question_bank_sources():
    return store.list_question_sources()


@app.get("/api/question-bank/questions")
def question_bank_questions(topic: str | None = None, role: str | None = None):
    return store.list_questions(topic=topic, role=role)


@app.get("/api/question-bank/companies")
def question_bank_companies():
    return store.company_stats()


@app.get("/api/user-question-bank")
def user_question_bank():
    return store.user_question_bank_stats()


@app.post("/api/user-question-bank/import")
def import_user_question_bank(body: UserQuestionImportRequest):
    try:
        return store.create_draft(parse_question_text(body.questions_text))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/user-question-bank/import-file")
async def import_user_question_file(file: UploadFile = File(...)):
    try:
        data = await file.read(5 * 1024 * 1024 + 1)
        entries = parse_question_file(file.filename or "questions.txt", data)
        return store.create_draft(entries)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def bank_operation(operation):
    try:
        return operation()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/user-question-banks")
def list_user_banks():
    return store.list_banks()


@app.post("/api/user-question-banks")
def create_user_bank(body: BankRequest):
    return bank_operation(lambda: store.create_bank(**body.model_dump()))


@app.put("/api/user-question-banks/{bank_id}")
def update_user_bank(bank_id: str, body: BankRequest):
    return bank_operation(lambda: store.update_bank(bank_id, **body.model_dump()))


@app.get("/api/user-question-banks/{bank_id}/questions")
def bank_contents(bank_id: str):
    return store.bank_questions(bank_id)


@app.put("/api/user-question-banks/{bank_id}/questions/{question_id}")
def edit_bank_question(bank_id: str, question_id: str, body: DraftEntry):
    return bank_operation(lambda: store.edit_bank_question(bank_id, question_id, body.model_dump()))


@app.delete("/api/user-question-banks/{bank_id}/questions/{question_id}")
def remove_bank_question(bank_id: str, question_id: str):
    return store.remove_bank_question(bank_id, question_id)


@app.get("/api/question-imports/{draft_id}")
def get_import_draft(draft_id: str):
    return bank_operation(lambda: store.get_draft(draft_id))


@app.post("/api/question-imports/{draft_id}/confirm")
def confirm_import_draft(draft_id: str, body: ConfirmImportRequest):
    return bank_operation(lambda: store.confirm_draft(draft_id, body.bank_id,
        [e.model_dump() for e in body.entries], body.company, body.role))


@app.post("/api/sessions")
def create_session():
    return store.save(Session())


@app.get("/api/sessions")
def list_sessions():
    return store.list()


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    return require_session(session_id)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    if not store.delete(session_id):
        raise HTTPException(404, "会话不存在")
    ai_clients.pop(session_id, None)
    return {"deleted": True}


@app.post("/api/sessions/{session_id}/ai/test")
def test_ai_connection(session_id: str, body: AIConnectionRequest):
    require_session(session_id)
    ai_clients.pop(session_id, None)
    started = time.perf_counter()
    try:
        client = OpenAICompatibleClient(body.base_url, body.api_key, body.model, body.provider, body.wire_api, body.timeout_seconds, body.stream)
        client.test()
    except AIConnectionError as exc:
        raise HTTPException(422, str(exc)) from exc
    client.realtime_url = (body.realtime_url or "").strip()
    if body.provider == "qwen" and client.realtime_url:
        client.realtime_url = validate_qwen_realtime_url(client.realtime_url)
    ai_clients[session_id] = client
    capability = voice_capability(client)
    return {"connected": True, "provider": body.provider, "model": client.model, "endpoint": client.endpoint,
            "wire_api": client.wire_api, "realtime_supported": realtime_supported(client),
            "voice_mode": capability["mode"], "voice_provider": capability["provider"],
            "realtime_model": capability["model"], "voice_label": capability["label"],
            "latency_ms": round((time.perf_counter() - started) * 1000)}


@app.delete("/api/sessions/{session_id}/ai")
def disconnect_ai(session_id: str):
    require_session(session_id)
    ai_clients.pop(session_id, None)
    return {"connected": False}


@app.post("/api/sessions/{session_id}/ai/system")
def connect_system_ai(session_id: str):
    require_session(session_id)
    api_key = os.environ.get("MOCKLAB_SYSTEM_AI_API_KEY", "").strip()
    base_url = os.environ.get("MOCKLAB_SYSTEM_AI_BASE_URL", "https://api.openai.com/v1").strip()
    model = os.environ.get("MOCKLAB_SYSTEM_AI_MODEL", "gpt-4.1-mini").strip()
    provider = os.environ.get("MOCKLAB_SYSTEM_AI_PROVIDER", "openai").strip()
    if not api_key:
        raise HTTPException(503, "此本地版本未配置系统 AI；请连接个人 API，或选择本地模式")
    started = time.perf_counter()
    try:
        client = OpenAICompatibleClient(base_url, api_key, model, provider)
        client.test()
    except AIConnectionError as exc:
        raise HTTPException(502, f"系统 AI 暂不可用：{exc}") from exc
    ai_clients[session_id] = client
    capability = voice_capability(client)
    return {"connected": True, "provider": provider, "model": client.model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "voice_mode": capability["mode"], "voice_provider": capability["provider"],
            "voice_label": capability["label"], "realtime_supported": realtime_supported(client)}


@app.post("/api/sessions/{session_id}/ai/diagnostics")
def diagnose_ai(session_id: str):
    require_session(session_id)
    client = ai_clients.get(session_id)
    if not client:
        raise HTTPException(409, "当前未连接 AI，请修改配置或切换本地模式")
    started = time.perf_counter()
    try:
        result = client.diagnose()
    except (AIConnectionError, AttributeError) as exc:
        raise HTTPException(422, str(exc) or "当前模型不支持完整诊断") from exc
    return {**result, "model": client.model, "latency_ms": round((time.perf_counter() - started) * 1000)}


@app.post("/api/sessions/{session_id}/realtime/call")
async def create_realtime_call(session_id: str, request: Request, style: str = "professional"):
    session = require_session(session_id)
    if session.status not in {SessionStatus.READY, SessionStatus.ASKING} or not session.plan:
        raise HTTPException(409, "请先确认面试计划")
    client = ai_clients.get(session_id)
    if not client or not realtime_supported(client):
        raise HTTPException(409, "当前连接没有可用的原生实时语音通道；可使用自动级联或文字模式")
    if style not in REALTIME_STYLES:
        raise HTTPException(422, "未知的面试官风格")
    raw = await request.body()
    if not raw or len(raw) > 100_000:
        raise HTTPException(422, "无效的 WebRTC 会话描述")
    try:
        sdp = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "WebRTC 会话描述编码无效") from exc
    if not sdp.lstrip().startswith("v=0"):
        raise HTTPException(422, "WebRTC 会话描述格式无效")

    capability = voice_capability(client)
    provider = capability["provider"]
    config = realtime_session_config(session, style)
    safety_id = hashlib.sha256(session.id.encode("utf-8")).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=35) as upstream:
            if provider == "qwen":
                signaling_url = validate_qwen_realtime_url(qwen_realtime_url(client) or "")
                result = await upstream.post(
                    signaling_url,
                    params={"model": QWEN_REALTIME_MODEL},
                    headers={"Authorization": f"Bearer {client.api_key}", "Content-Type": "application/sdp",
                             "User-Agent": "MockLab/2.0"},
                    content=sdp.encode("utf-8"),
                )
            else:
                result = await upstream.post(
                    "https://api.openai.com/v1/realtime/calls",
                    headers={
                        "Authorization": f"Bearer {client.api_key}",
                        "OpenAI-Safety-Identifier": safety_id,
                        "User-Agent": "MockLab/2.0",
                    },
                    files={"sdp": (None, sdp), "session": (None, json.dumps(config, ensure_ascii=False))},
                )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "实时语音服务连接超时，请检查网络后重试") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "无法连接实时语音服务，请检查网络、代理或证书") from exc
    if result.status_code >= 400:
        hints = {
            401: "API Key 无效，或不属于当前实时语音服务及地域",
            403: "当前账户、项目、业务空间或地区没有 Realtime 权限",
            429: "Realtime 额度不足或请求频率受限",
        }
        raise HTTPException(result.status_code, hints.get(result.status_code, f"Realtime 服务拒绝连接（HTTP {result.status_code}）"))
    session.model_version = f"{provider}:{capability['model']}+local-rubric"
    session.prompt_version = "realtime-interviewer-v1"
    store.save(session)
    return Response(content=result.content, media_type="application/sdp")


@app.get("/api/sessions/{session_id}/realtime/config")
def get_realtime_config(session_id: str, style: str = "professional"):
    session = require_session(session_id)
    client = ai_clients.get(session_id)
    if not client or not realtime_supported(client):
        raise HTTPException(409, "当前连接没有原生实时语音配置")
    if style not in REALTIME_STYLES:
        raise HTTPException(422, "未知的面试官风格")
    capability = voice_capability(client)
    event = {"type": "session.update", "session": qwen_session_config(session, style)} if capability["provider"] == "qwen" else None
    return {"provider": capability["provider"], "model": capability["model"], "session_event": event}


@app.post("/api/sessions/{session_id}/realtime/turn")
def record_realtime_turn(session_id: str, body: AnswerRequest):
    """Fast local tool invoked by the speech-to-speech model between audio turns."""
    session = require_session(session_id)
    if session.status != SessionStatus.ASKING or not session.plan:
        raise HTTPException(409, "当前状态不能记录回答")
    answer = body.answer.strip()
    if not answer:
        raise HTTPException(422, "回答不能为空")
    if session.started_at and (datetime.now(timezone.utc) - datetime.fromisoformat(session.started_at)).total_seconds() >= 3600:
        session.status = SessionStatus.FINISHED
        session.incomplete = True
        session.report = make_report(session)
        store.save(session)
        return {"finished": True, "reason": "已到 60 分钟硬上限", "report": session.report}

    turn, follow_question = next_turn(session, answer)
    session.turns.append(turn)
    if follow_question:
        next_question = follow_question
        session.report = {"pending_question": next_question, "pending_kind": "generated_followup"}
    else:
        session.current_topic_index += 1
        if session.current_topic_index >= len(session.plan.topics):
            session.status = SessionStatus.FINISHED
            session.incomplete = False
            session.report = make_report(session)
            store.save(session)
            return {"finished": True, "evaluation": turn.evaluation, "action": "end_interview",
                    "reason": "计划主题已全部覆盖", "report": session.report, "ai_mode": "realtime"}
        topic = session.plan.topics[session.current_topic_index]
        next_question = topic.question
        session.report = {"pending_question": next_question, "pending_kind": "main"}
    store.save(session)
    next_topic = session.plan.topics[session.current_topic_index]
    return {"finished": False, "evaluation": turn.evaluation, "action": turn.next_action,
            "reason": turn.decision_reason, "question": next_question, "next_question": next_question,
            "topic": next_topic.name, "progress": session.current_topic_index / len(session.plan.topics),
            "question_kind": session.report.get("pending_kind", "main"), "question_id": next_topic.question_id,
            "content_type": next_topic.content_type, "source_level": next_topic.source_level,
            "source_note": next_topic.source_note, "source_url": next_topic.source_url,
            "target_company": session.job.target_company, "ai_mode": "realtime"}


@app.post("/api/sessions/{session_id}/profiles/candidate")
def analyze_candidate_profile(session_id: str, body: CandidateAnalysisRequest):
    session = require_session(session_id)
    text = body.text.strip()
    if body.skip:
        text = "候选人选择跳过简历资料，本场使用通用候选人画像，并在回答中由候选人自行补充经历。"
    elif len(text) < 30:
        raise HTTPException(422, "请上传或粘贴至少 30 个字符的简历文本，也可以选择跳过")
    mode, warning = "local", None
    try:
        client = ai_clients.get(session_id)
        if client and hasattr(client, "analyze_candidate") and not body.skip:
            candidate = client.analyze_candidate(text, (session.document or {}).get("id"))
            mode = "ai"
        else:
            candidate = parse_candidate(text, (session.document or {}).get("id"))
    except AIConnectionError as exc:
        candidate = parse_candidate(text, (session.document or {}).get("id"))
        mode, warning = "local_fallback", f"AI 分析失败，已使用本地提取：{exc}"
    session.candidate = candidate
    session.resume_text = ""
    session.plan = None
    session.preflight = {**session.preflight, "candidate_analysis": mode, "candidate_skipped": body.skip}
    session.status = SessionStatus.CONFIRMING
    store.save(session)
    return {"candidate": candidate, "analysis_mode": mode, "warning": warning}


@app.post("/api/sessions/{session_id}/profiles/job")
def analyze_job_profile(session_id: str, body: JobAnalysisRequest):
    session = require_session(session_id)
    builtin_key = body.builtin_job or "ai"
    if body.skip:
        text, builtin = BUILTIN_JD, True
    elif body.use_builtin_job:
        text, builtin = BUILTIN_JOBS.get(builtin_key, BUILTIN_JD), builtin_key == "ai"
    else:
        text, builtin = body.text.strip(), False
        if len(text) < 20:
            raise HTTPException(422, "请粘贴至少 20 个字符的岗位描述，也可以使用通用岗位")
    mode, warning = "local", None
    try:
        client = ai_clients.get(session_id)
        if client and hasattr(client, "analyze_job") and not body.skip:
            job = client.analyze_job(text, builtin, body.target_company)
            mode = "ai"
        else:
            job = parse_job(text, builtin, body.target_company)
    except AIConnectionError as exc:
        job = parse_job(text, builtin, body.target_company)
        mode, warning = "local_fallback", f"AI 分析失败，已使用本地提取：{exc}"
    session.job = job
    if session.candidate:
        session.candidate.target_role = job.title
    session.plan = None
    session.preflight = {**session.preflight, "job_analysis": mode, "job_skipped": body.skip}
    session.status = SessionStatus.CONFIRMING
    store.save(session)
    return {"job": job, "candidate": session.candidate, "analysis_mode": mode, "warning": warning}


@app.get("/api/sessions/{session_id}/bank-recommendations")
def recommend_question_banks(session_id: str):
    session = require_session(session_id)
    if not session.candidate or not session.job:
        raise HTTPException(409, "请先确认个人资料和目标岗位")
    banks = store.list_banks()
    client = ai_clients.get(session_id)
    if client and hasattr(client, "recommend_banks"):
        try:
            return {"mode": "ai", "recommendations": client.recommend_banks(session.candidate, session.job, banks)}
        except AIConnectionError:
            pass
    role_text = (session.job.title + " " + " ".join(r.name for r in session.job.requirements)).lower()
    scored = []
    for bank in banks:
        score = (3 if bank.get("company") and bank["company"] == session.job.target_company else 0)
        terms = [term for term in str(bank.get("role", "")).lower().replace("/", " ").split() if len(term) > 1]
        score += sum(1 for term in terms if term in role_text)
        reason = "公司与目标一致" if score >= 3 else "岗位标签与当前目标相关" if score else "可由你决定是否纳入本场"
        scored.append((score, bank["name"], {"bank_id": bank["id"], "reason": reason}))
    return {"mode": "local", "recommendations": [item for _, _, item in sorted(scored, key=lambda x: (-x[0], x[1]))]}


@app.post("/api/sessions/{session_id}/resume")
async def upload_resume(session_id: str, file: UploadFile = File(...)):
    session = require_session(session_id)
    data = await file.read(5 * 1024 * 1024 + 1)
    try:
        text, file_type = extract_resume(file.filename or "resume", file.content_type, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    doc_id = f"document_{uuid4().hex[:16]}"
    session.resume_text = text
    session.document = {"id": doc_id, "original_name": Path(file.filename or "resume").name[:120], "file_type": file_type, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "extraction_status": "success", "temporary_file_deleted": True, "created_at": now_iso()}
    store.save(session)
    return {"text": text, "document": session.document}


@app.post("/api/sessions/{session_id}/profiles")
def build_profiles(session_id: str, body: ProfileRequest):
    session = require_session(session_id)
    resume_text = (body.resume_text or session.resume_text).strip()
    if len(resume_text) < 30:
        raise HTTPException(422, "请上传简历或粘贴至少 30 个字符的简历文本")
    if not body.use_builtin_job and len((body.jd_text or "").strip()) < 20:
        raise HTTPException(422, "自定义 JD 至少需要 20 个字符")
    session.resume_text = resume_text[:100_000]
    session.candidate = parse_candidate(session.resume_text, (session.document or {}).get("id"))
    builtin_key = body.builtin_job or "ai"
    selected_jd = BUILTIN_JOBS.get(builtin_key, BUILTIN_JD) if body.use_builtin_job else (body.jd_text or "")
    # Per MVP scope, only the AI application role has a formally validated rubric.
    session.job = parse_job(selected_jd, body.use_builtin_job and builtin_key == "ai", body.target_company, body.use_user_question_bank)
    session.job.selected_bank_ids = list(dict.fromkeys(body.selected_bank_ids))
    session.job.practice_mode = body.practice_mode
    session.job.allow_repeats = body.allow_repeats
    session.candidate.target_role = session.job.title
    # The raw resume is no longer needed after structured extraction.
    session.resume_text = ""
    session.status = SessionStatus.CONFIRMING
    return store.save(session)


@app.put("/api/sessions/{session_id}/profiles")
def confirm_profiles(session_id: str, body: ConfirmRequest):
    session = require_session(session_id)
    session.candidate = body.candidate
    session.job = body.job
    valid_banks = {b["id"] for b in store.list_banks()}
    if any(b not in valid_banks for b in body.job.selected_bank_ids):
        raise HTTPException(422, "所选题库不存在")
    if session.status not in {SessionStatus.DRAFT, SessionStatus.CONFIRMING, SessionStatus.READY}:
        raise HTTPException(409, "面试开始后不能修改已保存计划")
    session.plan = create_plan(body.candidate, body.job, store.questions_by_topic(), store.bank_questions(), store.practiced_ids())
    session.status = SessionStatus.READY
    return store.save(session)


@app.get("/api/sessions/{session_id}/plan/advice")
def get_plan_advice(session_id: str):
    session = require_session(session_id)
    if not session.plan or not session.job:
        raise HTTPException(409, "请先生成面试计划")
    client = ai_clients.get(session_id)
    if client and hasattr(client, "advise_plan"):
        try:
            advice = client.advise_plan(session)
            if advice:
                return {"mode": "ai", "advice": advice}
        except AIConnectionError:
            pass
    user_count = session.plan.user_question_count
    supplement_count = session.plan.supplement_question_count
    return {"mode": "local", "advice": f"计划覆盖 {len(session.plan.topics)} 道主问题，其中用户题 {user_count} 道、补充题 {supplement_count} 道；建议为每道主问题预留一次举证和一次追问时间。"}


@app.post("/api/sessions/{session_id}/start")
def start_interview(session_id: str):
    session = require_session(session_id)
    if not session.plan:
        raise HTTPException(409, "请先确认画像并生成面试计划")
    if session.status == SessionStatus.FINISHED:
        raise HTTPException(409, "该场面试已结束")
    if not session.plan.topics:
        raise HTTPException(409, "无可用新题，请选择其他题库、确认新题或主动开启重复练习")
    session.started_at = session.started_at or now_iso()
    session.status = SessionStatus.ASKING
    topic = session.plan.topics[session.current_topic_index]
    question = (session.report or {}).get("pending_question", topic.question)
    kind = (session.report or {}).get("pending_kind", "main")
    ai_mode, warning = "offline", None
    if client := ai_clients.get(session_id):
        session.model_version = client.model_version
        session.prompt_version = "fixed-main-generated-followup-v2"
        ai_mode = "live"
    session.report = {"pending_question": question, "pending_kind": kind}
    store.save(session)
    return {"question": question, "topic": topic.name, "progress": session.current_topic_index / len(session.plan.topics),
            "question_kind": kind, "question_id": topic.question_id, "content_type": topic.content_type, "source_level": topic.source_level, "source_note": topic.source_note, "source_url": topic.source_url,
            "target_company": session.job.target_company, "ai_mode": ai_mode, "warning": warning}


@app.post("/api/sessions/{session_id}/answer")
def submit_answer(session_id: str, body: AnswerRequest):
    session = require_session(session_id)
    if session.status != SessionStatus.ASKING or not session.plan:
        raise HTTPException(409, "当前状态不能提交回答")
    if session.started_at and (datetime.now(timezone.utc) - datetime.fromisoformat(session.started_at)).total_seconds() >= 3600:
        session.status = SessionStatus.FINISHED
        session.incomplete = True
        session.report = make_report(session)
        store.save(session)
        return {"finished": True, "action": "end_interview", "reason": "已到 60 分钟硬上限", "report": session.report}
    answer = body.answer.strip()
    if not answer:
        raise HTTPException(422, "回答不能为空")
    client = ai_clients.get(session_id)
    if client:
        try:
            current = session.plan.topics[session.current_topic_index]
            upcoming = session.plan.topics[session.current_topic_index + 1] if session.current_topic_index + 1 < len(session.plan.topics) else None
            remaining = max(0, session.plan.max_follow_ups_per_topic - session.topic_follow_ups.get(current.id, 0))
            model_turn = client.next_turn(session, current, answer, upcoming, remaining)
            model_turn.evaluation.correctness = None  # No verified basis was supplied to this evaluator.
            must_switch = model_turn.topic_complete or model_turn.action == "switch_topic" or remaining == 0
            question = (session.report or {}).pop("pending_question", current.question)
            action = "switch_topic" if must_switch else model_turn.action
            turn = Turn(question_id=current.question_id, question_kind=(session.report or {}).get("pending_kind", "main"), sequence=len(session.turns) + 1, topic_id=current.id, topic_name=current.name, question=question,
                        confirmed_answer=answer, evaluation=model_turn.evaluation, next_action=action,
                        decision_reason="模型驱动的自然追问" if not must_switch else "主题已完成或达到追问上限")
            session.turns.append(turn)
            if must_switch:
                session.current_topic_index += 1
                if session.current_topic_index >= len(session.plan.topics):
                    session.status = SessionStatus.FINISHED
                    session.incomplete = False
                    session.report = make_report(session)
                    store.save(session)
                    return {"finished": True, "evaluation": turn.evaluation, "action": "end_interview", "reason": "计划主题已全部覆盖", "report": session.report, "ai_mode": "live"}
                next_topic = session.plan.topics[session.current_topic_index]
            else:
                session.topic_follow_ups[current.id] = session.topic_follow_ups.get(current.id, 0) + 1
                next_topic = current
            next_question = next_topic.question if must_switch else model_turn.interviewer_reply
            session.report = {"pending_question": next_question, "pending_kind": "main" if must_switch else "generated_followup"}
            store.save(session)
            return {"finished": False, "evaluation": turn.evaluation, "action": action, "reason": turn.decision_reason,
                    "question": next_question, "topic": next_topic.name, "progress": session.current_topic_index / len(session.plan.topics),
                    "question_kind": session.report.get("pending_kind", "main"), "question_id": next_topic.question_id, "content_type": next_topic.content_type, "source_level": next_topic.source_level, "source_note": next_topic.source_note,
                    "source_url": next_topic.source_url, "target_company": session.job.target_company, "ai_mode": "live"}
        except (AIConnectionError, ValueError) as exc:
            session.degraded = True
            warning = f"AI 本轮响应异常，已切换本地规则：{exc}"
    else:
        warning = None
    turn, follow_question = next_turn(session, answer)
    session.turns.append(turn)
    if follow_question:
        next_question = follow_question
        session.report = {"pending_question": next_question, "pending_kind": "generated_followup"}
    else:
        session.current_topic_index += 1
        if session.current_topic_index >= len(session.plan.topics):
            session.status = SessionStatus.FINISHED
            session.incomplete = False
            session.report = make_report(session)
            store.save(session)
            return {"finished": True, "evaluation": turn.evaluation, "action": "end_interview", "reason": "计划主题已全部覆盖", "report": session.report, "ai_mode": "fallback", "warning": warning}
        topic = session.plan.topics[session.current_topic_index]
        next_question = topic.question
        session.report = {"pending_question": next_question, "pending_kind": "main"}
    store.save(session)
    next_topic = session.plan.topics[session.current_topic_index]
    return {"finished": False, "evaluation": turn.evaluation, "action": turn.next_action, "reason": turn.decision_reason,
            "question": next_question, "topic": next_topic.name, "progress": session.current_topic_index / len(session.plan.topics),
            "question_kind": session.report.get("pending_kind", "main"), "question_id": next_topic.question_id, "content_type": next_topic.content_type, "source_level": next_topic.source_level, "source_note": next_topic.source_note,
            "source_url": next_topic.source_url, "target_company": session.job.target_company, "ai_mode": "fallback", "warning": warning}


@app.post("/api/sessions/{session_id}/finish")
def finish_interview(session_id: str, body: FinishRequest):
    session = require_session(session_id)
    session.incomplete = body.reason != "complete" and (not session.plan or session.current_topic_index < len(session.plan.topics))
    session.status = SessionStatus.FINISHED
    session.report = make_report(session) if session.plan and session.job else {"complete": False, "error": "资料不完整，无法生成报告"}
    return store.save(session).report


@app.get("/api/sessions/{session_id}/report")
def get_report(session_id: str):
    session = require_session(session_id)
    if not session.report or session.status != SessionStatus.FINISHED:
        raise HTTPException(404, "报告尚未生成")
    return session.report


@app.post("/api/sessions/{session_id}/counter-question")
def counter_question(session_id: str, body: CounterQuestionRequest):
    session = require_session(session_id)
    if session.counter_question is not None:
        raise HTTPException(409, "每场面试只能反问一次")
    session.counter_question = body.question.strip()
    store.save(session)
    return {"answer": "这是一个很好的反问。在真实面试中，建议继续追问团队当前最重要的目标、该岗位前三个月的成功标准，以及你将与哪些角色协作。本原型不掌握具体公司的内部信息，因此不会编造答案。"}


@app.post("/api/sessions/{session_id}/post-test")
def start_post_test(session_id: str):
    session = require_session(session_id)
    if session.status != SessionStatus.FINISHED or not session.report or session.phase == "post_test":
        raise HTTPException(409, "需要先完成一次训练面试")
    job = session.job.model_copy(deep=True)
    job.allow_repeats = False
    excluded = {t.question_id for t in session.plan.topics if t.question_id}
    plan = create_plan(session.candidate, job, store.questions_by_topic(), store.bank_questions(), store.practiced_ids(), excluded)
    # A post-test needs distinct questions covering the same assessed topic keys.
    original_keys = {t.topic_key for t in session.plan.topics}
    plan.topics = [t for t in plan.topics if t.topic_key in original_keys]
    if not plan.topics or {t.topic_key for t in plan.topics} != original_keys:
        raise HTTPException(409, "后测题不足：需要相同考点的不同题目；可导入新题后再试，原题重练请开始新练习")
    plan.main_question_ids = [t.question_id for t in plan.topics]
    plan.user_question_count = sum(t.selection_kind == "user" for t in plan.topics)
    plan.supplement_question_count = len(plan.topics) - plan.user_question_count
    plan.estimated_minutes = 6 * len(plan.topics)
    for topic in plan.topics:
        topic.weight = 1 / len(plan.topics)
    post = Session(candidate=session.candidate, job=job, plan=plan, phase="post_test", parent_session_id=session.id,
                   status=SessionStatus.READY,
                   baseline_scores={x["name"]: x["score"] for x in session.report.get("topics", []) if x.get("score") is not None})
    if session.id in ai_clients:
        ai_clients[post.id] = ai_clients[session.id]
    return store.save(post)
