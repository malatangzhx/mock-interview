"""OpenAI-compatible runtime client plus the local fallback boundary."""
from __future__ import annotations

import json
import re
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import AnswerEvaluation, CandidateProfile, InterviewPlan, JobProfile, ModelInterviewTurn, Session, Topic
from .services import create_plan, evaluate, parse_candidate, parse_job
from .providers import PROVIDER_PRESETS


class AIConnectionError(RuntimeError):
    def __init__(self, message: str, *, category: str = "response", status: int | None = None):
        super().__init__(message)
        self.category = category
        self.status = status


class LLMClient(Protocol):
    model_version: str
    def parse_candidate(self, text: str, document_id: str | None = None) -> CandidateProfile: ...
    def parse_job(self, text: str, builtin: bool) -> JobProfile: ...
    def create_plan(self, candidate: CandidateProfile, job: JobProfile) -> InterviewPlan: ...
    def evaluate_answer(self, answer: str, topic: Topic) -> AnswerEvaluation: ...


class OfflineLLMClient:
    model_version = "deterministic-offline"
    parse_candidate = staticmethod(parse_candidate)
    parse_job = staticmethod(parse_job)
    create_plan = staticmethod(create_plan)
    evaluate_answer = staticmethod(evaluate)


class OpenAICompatibleClient:
    """Small OpenAI-compatible client. Credentials only live in memory."""
    def __init__(self, base_url: str | None, api_key: str, model: str | None, provider: str = "custom", wire_api: str | None = None, timeout_seconds: int = 90, stream: bool = False):
        preset = PROVIDER_PRESETS.get(provider)
        base_url = base_url or (preset["base_url"] if preset else None)
        model = model or (preset["model"] if preset else None)
        if not base_url or not model:
            raise AIConnectionError("请选择服务商，或填写自定义兼容接口的 URL 和模型")
        parsed = urlparse(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AIConnectionError("URL 必须是完整的 http:// 或 https:// 地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in base_url.strip()):
            raise AIConnectionError("Base URL 不应包含密钥、查询参数、空格或 # 片段，请复制 API 基础地址")
        cleaned = base_url.strip().rstrip("/")
        protocol = wire_api or (preset or {}).get("wire_api", "auto")
        suffixes = {"chat": "/chat/completions", "responses": "/responses", "anthropic": "/messages"}
        if protocol not in {*suffixes, "auto"}:
            raise AIConnectionError("协议必须为 auto、chat、responses 或 anthropic")
        explicit = next((p for p, suffix in suffixes.items() if cleaned.endswith(suffix)), None)
        if explicit:
            if wire_api not in {None, "auto", explicit}:
                raise AIConnectionError("完整接口 URL 与所选协议不一致，请修改协议或只填写 Base URL")
            self._connection_candidates = [(cleaned, explicit)]
        else:
            bases = [cleaned]
            # Only infer /v1 for a bare host. Never overwrite vendor paths like /api/v3.
            if not parsed.path.strip("/"):
                bases = [f"{cleaned}/v1", cleaned]
            protocols = ["chat", "responses", "anthropic"] if protocol == "auto" else [protocol]
            self._connection_candidates = [(base + suffixes[p], p) for p in protocols for base in bases]
        self.endpoint, self.wire_api = self._connection_candidates[0]
        self.api_key, self.model = api_key.strip(), model.strip()
        if not self.api_key or any(c.isspace() for c in self.api_key) or not self.api_key.isascii():
            raise AIConnectionError("API Key 不能为空，也不能包含空格、换行或中文；只粘贴密钥本身")
        if not self.model:
            raise AIConnectionError("请填写服务商控制台的准确模型 ID")
        self.timeout_seconds, self.stream = timeout_seconds, stream
        self.provider = provider
        self.model_version = f"{provider}:{self.model}"

    def _safe_detail(self, value: Any) -> str:
        text = str(value).replace(self.api_key, "[已隐藏密钥]")
        text = re.sub(r"(?i)Bearer\s+[^\s\"'<>]+|sk-[A-Za-z0-9_-]+", "[已隐藏密钥]", text)
        return text[:500]

    def _chat(self, messages: list[dict[str, str]], temperature: float = 0.35) -> str:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if self.stream else "application/json", "User-Agent": "MockInterview/1.1"}
        if self.wire_api == "responses":
            payload_data = {"model": self.model, "input": messages, "store": False, "stream": self.stream}
        elif self.wire_api == "anthropic":
            payload_data = {"model": self.model, "messages": [m for m in messages if m["role"] != "system"], "max_tokens": 4096, "stream": self.stream}
            system = "\n".join(m["content"] for m in messages if m["role"] == "system")
            if system:
                payload_data["system"] = system
            headers.update({"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        else:
            # Omit optional sampling parameters: several reasoning models reject them.
            payload_data = {"model": self.model, "messages": messages, "stream": self.stream}
            if self.provider == "minimax":
                payload_data["reasoning_split"] = True
        if self.wire_api != "anthropic":
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = json.dumps(payload_data).encode("utf-8")
        request = Request(self.endpoint, data=payload, method="POST", headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8-sig")
                if "text/event-stream" in response.headers.get("Content-Type", "") or raw.lstrip().startswith(("event:", "data:")):
                    return self._stream_text(raw)
                data = json.loads(raw)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                error = json.loads(raw)
                error = error.get("error", error) if isinstance(error, dict) else error
                detail = self._safe_detail(error.get("message", error) if isinstance(error, dict) else error)
            except ValueError:
                detail = "服务返回 HTML 页面，可能是地址错误、代理登录页或网关拦截" if "<html" in raw.lower() or "<!doctype" in raw.lower() else self._safe_detail(raw)
            hints = {400: "请求参数或模型协议不匹配；核对模型 ID、协议，若提示 stream 则启用流式请求", 401: "密钥无效或与地址不匹配；使用当前服务商及地域签发的 API Key", 402: "账户余额或额度不足；到服务商控制台检查", 403: "访问被拒绝；检查模型权限、地域限制或网关拦截", 404: "接口路径或模型不存在；核对 Base URL、协议和模型 ID", 405: "接口路径不支持 POST；核对 API 地址", 422: "请求参数不受支持；核对模型和协议", 429: "请求限流或额度不足；检查控制台额度并稍后重试"}
            hint = hints.get(exc.code, "上游服务异常；稍后重试或检查服务商状态")
            raise AIConnectionError(f"HTTP {exc.code}：{hint}。服务信息：{detail}", category="http", status=exc.code) from exc
        except URLError as exc:
            raise AIConnectionError(f"网络连接失败：{self._safe_detail(exc.reason)}。请检查 DNS、代理、证书及这台电脑到服务商的网络", category="network") from exc
        except TimeoutError as exc:
            raise AIConnectionError(f"模型服务超过 {self.timeout_seconds} 秒未响应；可增加超时秒数、检查代理或选响应更快的模型", category="timeout") from exc
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise AIConnectionError("服务返回非 JSON 内容，可能填了网站首页、网关返回 HTML 或协议不匹配；请核对 API 地址", category="format") from exc
        except OSError as exc:
            raise AIConnectionError(f"网络中断：{self._safe_detail(exc)}。请检查代理或稍后重试", category="network") from exc
        return self._response_text(data)

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(p["text"] for p in content if isinstance(p, dict) and p.get("type") in {"text", "output_text"} and isinstance(p.get("text"), str))
        return ""

    def _response_text(self, data: Any) -> str:
        if not isinstance(data, dict):
            raise AIConnectionError("服务响应不是 JSON 对象；请检查接口协议", category="format")
        if data.get("error") or data.get("status") in {"failed", "incomplete"}:
            raise AIConnectionError(f"服务已响应但生成未完成：{self._safe_detail(data.get('error') or data.get('incomplete_details') or data.get('status'))}")
        text = ""
        if self.wire_api == "responses":
            if isinstance(data.get("output_text"), str):
                text = data["output_text"]
            elif isinstance(data.get("output"), list):
                text = "".join(self._content_text(item.get("content")) for item in data["output"] if isinstance(item, dict) and item.get("type") == "message")
        elif self.wire_api == "anthropic":
            text = self._content_text(data.get("content"))
        else:
            try:
                text = self._content_text(data["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError):
                pass
        if not text.strip():
            raise AIConnectionError(f"服务已响应，但 {self.wire_api} 接口未返回可用文本；检查协议、模型是否为文本对话模型，或是否只返回了推理内容", category="format")
        return text

    def _stream_text(self, raw: str) -> str:
        chunks: list[str] = []
        final = None
        completed = False
        for block in raw.replace("\r\n", "\n").split("\n\n"):
            value = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
            if value == "[DONE]":
                completed = True
                continue
            if not value:
                continue
            event = json.loads(value)
            if not isinstance(event, dict):
                raise AIConnectionError("流式响应事件格式错误", category="format")
            kind = event.get("type")
            if kind == "message_stop":
                completed = True
            if event.get("error") or kind in {"error", "response.failed", "response.incomplete"}:
                raise AIConnectionError(f"流式生成失败：{self._safe_detail(event.get('error') or event.get('response', {}).get('error') or kind)}")
            if kind == "response.completed":
                final = event.get("response")
            elif kind == "response.output_text.delta":
                chunks.append(self._content_text(event.get("delta")))
            elif kind == "content_block_delta":
                delta = event.get("delta", {})
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    chunks.append(self._content_text(delta.get("text")))
            elif isinstance(event.get("choices"), list):
                for choice in event["choices"]:
                    if isinstance(choice, dict) and isinstance(choice.get("delta"), dict):
                        chunks.append(self._content_text(choice["delta"].get("content")))
                    if isinstance(choice, dict) and choice.get("finish_reason"):
                        if choice["finish_reason"] not in {"stop"}:
                            raise AIConnectionError("流式输出被截断或未正常结束，请换用可完成文本回答的模型")
                        completed = True
        if final is not None:
            return self._response_text(final)
        if not completed:
            raise AIConnectionError("流式连接提前结束，未收到完成事件；请检查网络或中转服务")
        text = "".join(chunks)
        if not text.strip():
            raise AIConnectionError("流式响应未包含可用文本；请检查协议和模型", category="format")
        return text

    @staticmethod
    def _json(text: str) -> dict[str, Any]:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
        match = re.search(r"\{.*\}", clean, flags=re.S)
        if not match:
            raise AIConnectionError("模型未返回可解析的 JSON")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AIConnectionError("模型返回了无效 JSON") from exc

    def test(self) -> None:
        failures: list[str] = []
        for endpoint, wire_api in self._connection_candidates:
            self.endpoint, self.wire_api = endpoint, wire_api
            try:
                text = self._chat([{"role": "user", "content": "Reply with exactly: {\"ok\":true}"}], temperature=0)
                try:
                    valid = self._json(text).get("ok") is True
                except AIConnectionError:
                    valid = False
                if valid:
                    self.model_version = f"{self.provider}:{self.model}:{self.wire_api}"
                    return
                failures.append(f"{urlparse(endpoint).path}：服务已连通，但模型未通过 JSON 输出验证；请使用能遵循 JSON 指令的文本对话模型")
                break
            except AIConnectionError as exc:
                reason = str(exc).replace("模型服务返回 ", "").replace("无法连接模型服务：", "")
                failures.append(f"{urlparse(endpoint).path} [{wire_api}]：{reason}")
                if exc.status not in {404, 405} and exc.category != "format":
                    break
        attempted = "；".join(failures)
        raise AIConnectionError(f"连接验证未通过（{urlparse(self.endpoint).netloc} / {self.model}）。{attempted}")

    def analyze_candidate(self, text: str, document_id: str | None = None) -> CandidateProfile:
        baseline = parse_candidate(text, document_id)
        prompt = f'''你是简历信息提取助手。只提取原文明确出现的信息，不推断、不美化。
简历原文：
{text}
只输出 JSON：{{"target_role":"目标岗位或空字符串","education":["教育经历"],"skills":["技能"],"projects":["项目及候选人贡献证据"],"evidence_refs":["支持上述结论的原文短句"],"uncertain_fields":["需要用户确认的字段"]}}'''
        data = self._json(self._chat([{"role": "user", "content": prompt}], temperature=0.1))
        clean_lists = {}
        for key in ("education", "skills", "projects", "evidence_refs", "uncertain_fields"):
            values = data.get(key)
            if isinstance(values, list):
                clean_lists[key] = [str(value).strip()[:240] for value in values if str(value).strip()][:12]
        role = str(data.get("target_role") or baseline.target_role).strip()[:80]
        return baseline.model_copy(update={"target_role": role, **clean_lists})

    def analyze_job(self, text: str, builtin: bool, target_company: str = "通用",
                    use_user_question_bank: bool = False) -> JobProfile:
        baseline = parse_job(text, builtin, target_company, use_user_question_bank)
        prompt = f'''你是岗位描述信息提取助手。只根据原文提取职责、必备技能、加分项和面试重点，不补写不存在的要求。
岗位描述：
{text}
只输出 JSON：{{"title":"岗位名称","requirements":[{{"name":"要求或面试重点","evidence":"对应原文依据","priority":1到5}}]}}'''
        data = self._json(self._chat([{"role": "user", "content": prompt}], temperature=0.1))
        requirements = []
        for item in data.get("requirements", []) if isinstance(data.get("requirements"), list) else []:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue
            try:
                priority = int(item.get("priority", 3))
            except (TypeError, ValueError):
                priority = 3
            requirements.append({"name": str(item["name"]).strip()[:120],
                                 "evidence": str(item.get("evidence", "岗位原文")).strip()[:240],
                                 "priority": max(1, min(5, priority))})
        update = {"title": str(data.get("title") or baseline.title).strip()[:60]}
        if requirements:
            update["requirements"] = requirements[:10]
        return JobProfile.model_validate({**baseline.model_dump(), **update})

    def recommend_banks(self, candidate: CandidateProfile, job: JobProfile, banks: list[dict]) -> list[dict]:
        if not banks:
            return []
        compact = [{"id": bank["id"], "name": bank["name"], "company": bank.get("company", ""),
                    "role": bank.get("role", ""), "questions": bank.get("questions", 0)} for bank in banks]
        prompt = f'''根据候选人画像与岗位画像为本场练习推荐题库顺序。只推荐给出的题库，不能创建题库；每项给一句具体理由。
候选人技能：{candidate.skills}
候选人项目：{candidate.projects}
岗位：{job.title}；公司：{job.target_company}；要求：{[r.name for r in job.requirements]}
可选题库：{json.dumps(compact, ensure_ascii=False)}
只输出 JSON：{{"recommendations":[{{"bank_id":"给定 id","reason":"推荐理由"}}]}}'''
        data = self._json(self._chat([{"role": "user", "content": prompt}], temperature=0.2))
        allowed = {bank["id"] for bank in compact}
        result, seen = [], set()
        for item in data.get("recommendations", []) if isinstance(data.get("recommendations"), list) else []:
            bank_id = str(item.get("bank_id", "")) if isinstance(item, dict) else ""
            if bank_id in allowed and bank_id not in seen:
                result.append({"bank_id": bank_id, "reason": str(item.get("reason", "与当前岗位相关"))[:300]})
                seen.add(bank_id)
        return result

    def advise_plan(self, session: Session) -> str:
        topics = [{"name": topic.name, "kind": topic.selection_kind,
                   "reason": topic.selection_reason} for topic in (session.plan.topics if session.plan else [])]
        prompt = f'''阅读已经由硬规则生成的面试计划，只给一句覆盖建议和一句时间建议。不能新增、删除、替换或改写题目。
岗位：{session.job.title if session.job else ''}
计划：{json.dumps(topics, ensure_ascii=False)}
只输出 JSON：{{"coverage":"一句覆盖建议","timing":"一句时间建议"}}'''
        data = self._json(self._chat([{"role": "user", "content": prompt}], temperature=0.2))
        return " ".join(str(data.get(key, "")).strip() for key in ("coverage", "timing") if str(data.get(key, "")).strip())[:600]

    def diagnose(self) -> dict:
        prompt = '''这是连接诊断，不含真实用户数据。请按以下结构输出一条模拟面试追问与评价字段。
只输出 JSON：{"follow_up":"请举一个具体例子。","evaluation":{"clarity":4,"evidence":3}}'''
        data = self._json(self._chat([{"role": "user", "content": prompt}], temperature=0))
        evaluation = data.get("evaluation")
        if not isinstance(data.get("follow_up"), str) or not data["follow_up"].strip() or not isinstance(evaluation, dict):
            raise AIConnectionError("模型响应缺少追问或评价字段，请更换支持结构化输出的模型")
        if not {"clarity", "evidence"}.issubset(evaluation):
            raise AIConnectionError("模型响应缺少 clarity 或 evidence 评价字段")
        return {"follow_up": data["follow_up"][:300], "evaluation_fields": sorted(evaluation.keys())}

    def opening_question(self, session: Session, topic: Topic) -> str:
        return topic.question

    def next_turn(self, session: Session, topic: Topic, answer: str, next_topic: Topic | None, followups_remaining: int) -> ModelInterviewTurn:
        history = "\n".join(f"Q: {t.question}\nA: {t.confirmed_answer}" for t in session.turns[-4:]) or "（这是第一轮回答）"
        prompt = f'''你是中文模拟技术面试官“林老师”。进行自然、连贯的一问一答，不要说评分、维度、动作或“根据你的回答”。
岗位：{session.job.title if session.job else ''}
当前主题：{topic.name}；目标：{topic.objective}
固定主问题：{topic.question}
评分边界：未提供已核验答案和核验依据。correctness 必须为 null；优缺点仅评价表达、结构和证据，不能判定技术对错。
当前候选人回答：{answer}
最近对话：{history}
本主题剩余可追问次数：{followups_remaining}
下一个主题：{next_topic.name if next_topic else '无'}；目标：{next_topic.objective if next_topic else '无'}
规则：若当前回答缺少关键细节，围绕当前主题问一个具体追问；若已完成或没有追问次数，输出 switch_topic；系统将使用已保存的下一道主问题，不能自行替换。不要一次问多个问题；不要给参考答案。
只输出 JSON：{{"interviewer_reply":"下一句自然口语化提问","action":"clarify|request_evidence|probe_depth|challenge|give_hint|switch_topic","topic_complete":true或false,"evaluation":{{"correctness":1到5或null,"completeness":1到5,"depth":1到5,"relevance":1到5,"clarity":1到5,"evidence":1到5,"confidence":0到1,"strengths":["简短优点"],"gaps":["简短缺失点"],"evidence_quote":"回答中的简短原话"}}}}'''
        return ModelInterviewTurn.model_validate(self._json(self._chat([{"role": "user", "content": prompt}])))
