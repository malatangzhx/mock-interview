from fastapi.testclient import TestClient

from app import main
from app.db import SessionStore


def test_page_exposes_seven_step_preflight_and_debug_gates():
    page = TestClient(main.app).get("/")
    assert page.status_code == 200
    for element_id in ("aiSetup", "profileSetup", "jobSetup", "bankSetup", "planSetup", "ready", "interview"):
        assert f'id="{element_id}"' in page.text
    for element_id in ("runAllChecks", "cameraState", "micState", "speechState", "modelState", "startBlockers"):
        assert f'id="{element_id}"' in page.text
    assert page.text.index('id="aiSetup"') < page.text.index('id="profileSetup"')
    assert page.text.index('id="profileSetup"') < page.text.index('id="jobSetup"')
    assert page.text.index('id="jobSetup"') < page.text.index('id="bankSetup"')


def test_profile_steps_can_be_skipped_and_still_generate_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "preflight.db"))
    monkeypatch.setattr(main, "ai_clients", {})
    client = TestClient(main.app)
    session_id = client.post("/api/sessions").json()["id"]

    candidate_response = client.post(
        f"/api/sessions/{session_id}/profiles/candidate", json={"text": "", "skip": True}
    )
    assert candidate_response.status_code == 200
    assert candidate_response.json()["analysis_mode"] == "local"

    job_response = client.post(
        f"/api/sessions/{session_id}/profiles/job", json={"text": "", "skip": True, "target_company": "通用"}
    )
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["job"]["title"] == "AI应用开发实习生"

    planned = client.put(
        f"/api/sessions/{session_id}/profiles",
        json={"candidate": body["candidate"], "job": body["job"]},
    )
    assert planned.status_code == 200
    assert planned.json()["plan"]["topics"]
    advice = client.get(f"/api/sessions/{session_id}/plan/advice")
    assert advice.status_code == 200
    assert advice.json()["mode"] == "local"


def test_model_diagnostic_uses_memory_only_client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "diagnostic.db"))
    monkeypatch.setattr(main, "ai_clients", {})
    client = TestClient(main.app)
    session_id = client.post("/api/sessions").json()["id"]

    class DiagnosticClient:
        model = "synthetic-model"

        @staticmethod
        def diagnose():
            return {"follow_up": "请举一个具体例子。", "evaluation_fields": ["clarity", "evidence"]}

    main.ai_clients[session_id] = DiagnosticClient()
    response = client.post(f"/api/sessions/{session_id}/ai/diagnostics")
    assert response.status_code == 200
    assert response.json()["model"] == "synthetic-model"
    assert response.json()["evaluation_fields"] == ["clarity", "evidence"]
    assert "synthetic-model" not in client.get(f"/api/sessions/{session_id}").text


def test_system_ai_has_actionable_fallback_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "system-ai.db"))
    monkeypatch.delenv("MOCKLAB_SYSTEM_AI_API_KEY", raising=False)
    client = TestClient(main.app)
    session_id = client.post("/api/sessions").json()["id"]
    response = client.post(f"/api/sessions/{session_id}/ai/system")
    assert response.status_code == 503
    assert "个人 API" in response.json()["detail"]
    assert "本地模式" in response.json()["detail"]
