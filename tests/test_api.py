import json

from fastapi.testclient import TestClient

import app.main as main
from app.db import SessionStore
from app.models import AnswerEvaluation, ModelInterviewTurn


def test_interview_page_has_dual_video_and_avatar():
    page = TestClient(main.app).get("/")
    assert page.status_code == 200
    assert 'id="candidateVideo"' in page.text
    assert 'id="aiTile"' in page.text
    assert "/static/assets/interviewer-lin.png" in page.text
    assert "数据分析师" in page.text
    assert "/static/compact.css" in page.text
    assert 'id="targetCompany"' in page.text
    assert 'id="aiEnabled"' in page.text
    assert "/static/product.css" in page.text
    assert "/static/scroll-fix.css" in page.text
    assert 'id="questionBankFile"' in page.text
    assert 'id="modelConnectionStatus"' in page.text
    assert 'id="transcriptFeed"' in page.text
    assert 'id="muteBtn"' in page.text
    assert 'id="interviewerStyle"' in page.text
    assert 'id="aiRealtimeUrl"' in page.text
    assert 'id="transcriptCheck"' not in page.text
    assert 'id="listenBtn"' not in page.text
    assert "/static/model-status.css" in page.text
    assert 'class="panel bank-picker"' in page.text
    assert 'class="start-gate"' in page.text
    assert 'id="editAiConnection"' in page.text
    device_section = page.text.split('class="grid two device-grid"', 1)[1].split('class="start-gate"', 1)[0]
    assert 'id="durationCheck"' not in device_section
    assert page.text.index('class="start-gate"') < page.text.index('id="durationCheck"')


def test_live_ai_dialogue_is_memory_only(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, base_url, api_key, model, provider="custom", wire_api=None, timeout_seconds=90, stream=False):
                self.model = model
                self.endpoint = f"{base_url}/chat/completions"
                self.wire_api = wire_api or "chat"
                self.model_version = f"openai-compatible:{model}"
        def test(self): pass
        def opening_question(self, session, topic): return "先谈谈你在这个项目中的具体职责。"
        def next_turn(self, session, topic, answer, next_topic, followups_remaining):
            evaluation = AnswerEvaluation(correctness=4, completeness=4, depth=3, relevance=5, clarity=4, evidence=4, confidence=.8, strengths=["表达具体"], gaps=["可补充边界"], evidence_quote=answer[:20])
            return ModelInterviewTurn(interviewer_reply=f"我们转到{next_topic.name}。请说说你的做法。", action="switch_topic", topic_complete=True, evaluation=evaluation)

    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "live.db"))
    monkeypatch.setattr(main, "OpenAICompatibleClient", FakeClient)
    client = TestClient(main.app)
    sid = client.post("/api/sessions").json()["id"]
    connected = client.post(f"/api/sessions/{sid}/ai/test", json={"base_url": "https://example.test/v1", "api_key": "secret-never-store", "model": "demo-model"})
    assert connected.status_code == 200
    assert "secret-never-store" not in client.get(f"/api/sessions/{sid}").text
    resume = "软件工程本科生，掌握 Python、RAG 和 Docker。项目：课程资料问答系统，我负责接口和检索评估。"
    parsed = client.post(f"/api/sessions/{sid}/profiles", json={"resume_text": resume, "use_builtin_job": True}).json()
    client.put(f"/api/sessions/{sid}/profiles", json={"candidate": parsed["candidate"], "job": parsed["job"]})
    opening = client.post(f"/api/sessions/{sid}/start").json()
    assert opening["ai_mode"] == "live"
    assert opening["question"] == client.get(f"/api/sessions/{sid}").json()["plan"]["topics"][0]["question"]
    reply = client.post(f"/api/sessions/{sid}/answer", json={"answer": "我负责接口和检索评估。"}).json()
    assert reply["ai_mode"] == "live"
    assert reply["question"] == client.get(f"/api/sessions/{sid}").json()["plan"]["topics"][1]["question"]
    assert reply["evaluation"]["correctness"] is None


def test_realtime_webrtc_call_is_server_authenticated_and_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "realtime.db"))
    monkeypatch.setattr(main, "ai_clients", {})
    client = TestClient(main.app)
    sid = client.post("/api/sessions").json()["id"]
    resume = "软件工程本科生，掌握 Python、FastAPI、RAG 和 Docker。项目中负责检索评估和接口开发，并完成了延迟优化。"
    parsed = client.post(f"/api/sessions/{sid}/profiles", json={"resume_text": resume, "use_builtin_job": True}).json()
    client.put(f"/api/sessions/{sid}/profiles", json={"candidate": parsed["candidate"], "job": parsed["job"]})

    class RealtimeClient:
        provider = "openai"
        endpoint = "https://api.openai.com/v1/chat/completions"
        api_key = "sk-server-only-secret"

    main.ai_clients[sid] = RealtimeClient()
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"v=0\r\na=answer"

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, url, headers, files):
            captured.update(url=url, headers=headers, files=files)
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        f"/api/sessions/{sid}/realtime/call?style=professional",
        content="v=0\r\na=offer",
        headers={"Content-Type": "application/sdp"},
    )
    assert response.status_code == 200
    assert response.text.startswith("v=0")
    assert "sk-server-only-secret" not in response.text
    assert captured["url"] == "https://api.openai.com/v1/realtime/calls"
    assert captured["headers"]["Authorization"] == "Bearer sk-server-only-secret"
    config = json.loads(captured["files"]["session"][1])
    assert config["model"] == main.REALTIME_MODEL
    assert config["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad", "eagerness": "auto", "create_response": True, "interrupt_response": True,
    }
    assert config["audio"]["output"]["voice"] == "marin"
    assert config["tools"][0]["name"] == "record_interview_answer"


def test_voice_capabilities_distinguish_native_and_cascade():
    class Client:
        endpoint = "https://api.deepseek.com/v1/chat/completions"
        provider = "deepseek"
        model = "deepseek-v4-flash"

    deepseek = main.voice_capability(Client())
    assert deepseek["mode"] == "cascade"
    Client.provider = "doubao"
    Client.endpoint = "https://ark.cn-beijing.volces.com/api/v3/responses"
    assert main.voice_capability(Client())["mode"] == "cascade"
    Client.provider = "qwen"
    Client.endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert main.voice_capability(Client())["mode"] == "cascade"
    Client.realtime_url = "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/webrtc/realtime"
    qwen = main.voice_capability(Client())
    assert qwen["mode"] == "native"
    assert qwen["model"] == main.QWEN_REALTIME_MODEL


def test_qwen_realtime_webrtc_proxy_and_session_config(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "qwen-realtime.db"))
    monkeypatch.setattr(main, "ai_clients", {})
    client = TestClient(main.app)
    sid = client.post("/api/sessions").json()["id"]
    resume = "软件工程本科生，掌握 Python、FastAPI、RAG 和 Docker。项目中负责检索评估、接口开发与线上监控。"
    parsed = client.post(f"/api/sessions/{sid}/profiles", json={"resume_text": resume, "use_builtin_job": True}).json()
    client.put(f"/api/sessions/{sid}/profiles", json={"candidate": parsed["candidate"], "job": parsed["job"]})

    class QwenClient:
        provider = "qwen"
        endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        api_key = "sk-qwen-server-secret"
        model = "qwen-plus"
        realtime_url = "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/webrtc/realtime"

    main.ai_clients[sid] = QwenClient()
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"v=0\r\na=answer"

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        f"/api/sessions/{sid}/realtime/call?style=warm",
        content="v=0\r\na=offer",
        headers={"Content-Type": "application/sdp"},
    )
    assert response.status_code == 200
    assert captured["url"].endswith("/api/v1/webrtc/realtime")
    assert captured["params"] == {"model": main.QWEN_REALTIME_MODEL}
    assert captured["headers"]["Authorization"] == "Bearer sk-qwen-server-secret"
    assert captured["content"].startswith(b"v=0")
    config = client.get(f"/api/sessions/{sid}/realtime/config?style=warm").json()
    assert config["provider"] == "qwen"
    session = config["session_event"]["session"]
    assert session["turn_detection"]["type"] == "semantic_vad"
    assert session["voice"] == "Cherry"
    assert session["tools"][0]["name"] == "record_interview_answer"


def test_qwen_realtime_url_rejects_non_aliyun_hosts():
    try:
        main.validate_qwen_realtime_url("https://example.test/api/v1/webrtc/realtime")
    except main.HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("non-Aliyun realtime URL should be rejected")


def test_realtime_turn_updates_report_without_waiting_for_text_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "realtime-turn.db"))
    monkeypatch.setattr(main, "ai_clients", {})
    client = TestClient(main.app)
    sid = client.post("/api/sessions").json()["id"]
    resume = "软件工程本科生，掌握 Python、FastAPI、RAG 和 Docker。项目：知识库问答系统，我负责接口、检索评估和监控。"
    parsed = client.post(f"/api/sessions/{sid}/profiles", json={"resume_text": resume, "use_builtin_job": True}).json()
    client.put(f"/api/sessions/{sid}/profiles", json={"candidate": parsed["candidate"], "job": parsed["job"]})
    client.post(f"/api/sessions/{sid}/start")
    strong = "我先明确目标和边界，再实现接口与异常处理。针对超时增加有限重试和监控，并用离线集评估，最终召回率提升 18%。"
    for _ in range(30):
        result = client.post(f"/api/sessions/{sid}/realtime/turn", json={"answer": strong})
        assert result.status_code == 200
        body = result.json()
        if body["finished"]:
            break
        assert body["ai_mode"] == "realtime"
        assert body["next_question"]
    assert body["finished"] is True
    saved = client.get(f"/api/sessions/{sid}").json()
    assert saved["turns"]
    assert saved["report"]["turn_count"] == len(saved["turns"])


def test_data_analyst_builtin_job(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "data-job.db"))
    client = TestClient(main.app)
    sid = client.post("/api/sessions").json()["id"]
    resume = "统计学本科生，熟悉 Python、SQL 和 Excel。项目：电商经营分析，负责数据清洗、指标体系和可视化报表，帮助团队发现转化问题。"
    parsed = client.post(f"/api/sessions/{sid}/profiles", json={"resume_text": resume, "use_builtin_job": True, "builtin_job": "data"}).json()
    assert parsed["job"]["title"] == "数据分析师"
    assert parsed["job"]["mode"] == "experimental"
    confirmed = client.put(f"/api/sessions/{sid}/profiles", json={"candidate": parsed["candidate"], "job": parsed["job"]}).json()
    names = {x["name"] for x in confirmed["plan"]["topics"]}
    assert "SQL 与数据提取" in names
    assert "业务分析与行动建议" in names


def test_complete_api_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "test.db"))
    client = TestClient(main.app)
    sid = client.post("/api/sessions").json()["id"]
    resume = "软件工程本科生，掌握 Python、FastAPI、RAG 和 Docker。项目：课程资料问答系统，我负责检索评估与接口开发，召回率提升 18%。"
    parsed = client.post(f"/api/sessions/{sid}/profiles", json={"resume_text": resume, "use_builtin_job": True}).json()
    assert parsed["job"]["mode"] == "validated"
    confirmed = client.put(f"/api/sessions/{sid}/profiles", json={"candidate": parsed["candidate"], "job": parsed["job"]}).json()
    assert 4 <= len(confirmed["plan"]["topics"]) <= 6
    current = client.post(f"/api/sessions/{sid}/start").json()
    assert current["question"]
    strong = "首先我会明确目标和边界。项目中我负责具体实现与异常处理，因为外部接口会超时，所以加入有限重试、Schema 校验和监控。其次我用离线数据评估，最终召回率提升 18%，同时记录失败率和延迟；取舍是增加少量复杂度来换取可靠性。"
    for _ in range(30):
        current = client.post(f"/api/sessions/{sid}/answer", json={"answer": strong}).json()
        if current["finished"]:
            break
    assert current["finished"] is True
    assert current["report"]["coverage"] == 1
    post = client.post(f"/api/sessions/{sid}/post-test")
    assert post.status_code == 200 and post.json()["phase"] == "post_test"


def test_delete_session(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "test.db"))
    client = TestClient(main.app)
    sid = client.post("/api/sessions").json()["id"]
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_question_bank_metadata_api(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "bank.db"))
    client = TestClient(main.app)
    stats = client.get("/api/question-bank")
    assert stats.status_code == 200
    assert stats.json()["questions"] == 64
    sources = client.get("/api/question-bank/sources")
    assert sources.status_code == 200
    assert {item["license_name"] for item in sources.json()} == {
        "MIT",
        "Apache-2.0",
        "CC BY-SA 4.0",
        "CC BY 4.0",
        "Reference only; paraphrased",
    }
    questions = client.get("/api/question-bank/questions", params={"role": "ai"})
    assert questions.status_code == 200
    assert any(item["id"].startswith("cn-") for item in questions.json())
    domestic = next(item for item in questions.json() if item["id"].startswith("cn-"))
    assert domestic["answer_outline"] and domestic["scoring_points"] and domestic["verification_urls"]


def test_company_question_bank_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "companies.db"))
    client = TestClient(main.app)
    response = client.get("/api/question-bank/companies")
    assert response.status_code == 200
    counts = {
        item["name"]: item["question_count"]
        for item in response.json()
        if item["name"] != "通用"
    }
    assert counts == {
        "字节跳动": 5,
        "阿里巴巴": 5,
        "美团": 3,
        "腾讯": 2,
        "网易": 2,
    }


def test_user_question_bank_import_and_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "user-bank.db"))
    client = TestClient(main.app)
    imported = client.post("/api/user-question-bank/import", json={
        "questions_text": "请说明 RAG 系统发生召回下降时，你会如何定位原因？\n请介绍一次你排查线上接口延迟的经历。"
    })
    assert imported.status_code == 200
    draft = imported.json()
    assert len(draft["entries"]) == 2
    assert client.get("/api/user-question-bank").json()["questions"] == 0
    bank = client.post("/api/user-question-banks", json={"name": "我的题库"}).json()
    confirmed_import = client.post(f"/api/question-imports/{draft['id']}/confirm", json={"bank_id": bank["id"], "entries": draft["entries"]})
    assert confirmed_import.json()["added"] == 2
    assert client.get("/api/user-question-bank").json()["questions"] == 2

    sid = client.post("/api/sessions").json()["id"]
    resume = "软件工程本科，掌握 Python、FastAPI、RAG 和 Docker。项目中负责检索评估和接口开发。"
    parsed = client.post(f"/api/sessions/{sid}/profiles", json={
        "resume_text": resume, "use_builtin_job": True, "builtin_job": "ai",
        "use_user_question_bank": True,
    }).json()
    confirmed = client.put(f"/api/sessions/{sid}/profiles", json={
        "candidate": parsed["candidate"], "job": parsed["job"],
    }).json()
    custom_topics = [topic for topic in confirmed["plan"]["topics"] if topic["question_bank_id"].startswith("user-")]
    assert custom_topics
    assert all(topic["source_level"] == "C" for topic in custom_topics)
    assert all("用户自行提供" in topic["source_note"] for topic in custom_topics)


def test_user_question_bank_file_import(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "file-bank.db"))
    client = TestClient(main.app)
    response = client.post("/api/user-question-bank/import-file", files={"file": ("questions.csv", "question,topic\n请说明 SQL 窗口函数,sql\n请解释 A/B 测试,statistics", "text/csv")})
    assert response.status_code == 200
    assert len(response.json()["entries"]) == 2
    assert client.get("/api/user-question-bank").json()["questions"] == 0


def test_ai_failure_disconnect_and_validation(tmp_path, monkeypatch):
    from app.llm import AIConnectionError
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "connection.db"))
    monkeypatch.setattr(main, "ai_clients", {})
    class FailingClient:
        def __init__(self, *args): pass
        def test(self): raise AIConnectionError("HTTP 401：密钥与服务商不匹配")
    monkeypatch.setattr(main, "OpenAICompatibleClient", FailingClient)
    client = TestClient(main.app)
    sid = client.post("/api/sessions").json()["id"]
    main.ai_clients[sid] = object()
    failure = client.post(f"/api/sessions/{sid}/ai/test", json={"provider": "anthropic", "api_key": "test-secret-key", "wire_api": "anthropic", "stream": True})
    assert failure.status_code == 422
    assert "401" in failure.json()["detail"]
    assert "test-secret-key" not in failure.text
    assert sid not in main.ai_clients
    main.ai_clients[sid] = object()
    assert client.delete(f"/api/sessions/{sid}/ai").json() == {"connected": False}
    assert sid not in main.ai_clients
    assert client.post(f"/api/sessions/{sid}/ai/test", json={"api_key": "test-key", "timeout_seconds": 999}).status_code == 422


def test_provider_ui_uses_one_config_and_exposes_diagnostics():
    import json
    import re
    page = TestClient(main.app).get("/").text
    presets = json.loads(re.search(r'<script id="aiPresets" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert presets == main.PROVIDER_PRESETS
    for provider in presets:
        assert f'<option value="{provider}"' in page
    assert 'id="aiWireApi"' in page
    assert 'id="aiStream"' in page
    assert 'id="aiTimeout"' in page
