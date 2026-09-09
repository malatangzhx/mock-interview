from io import BytesIO

import pytest
from docx import Document

from app.models import Session, SessionStatus
from app.db import SessionStore
from app.services import BUILTIN_JD, create_plan, extract_resume, make_report, next_turn, parse_candidate, parse_job
from app.question_import import parse_question_file, parse_question_text


RESUME = """张同学 软件工程本科
技能：Python、FastAPI、RAG、Docker、SQLite
项目：课程资料智能问答系统
我负责 RAG 检索、接口开发和离线评估，召回率提升 18%。
"""


def test_txt_extract_and_reject_binary():
    text, kind = extract_resume("resume.txt", "text/plain", RESUME.encode())
    assert kind == "txt" and "FastAPI" in text
    with pytest.raises(ValueError):
        extract_resume("bad.txt", "text/plain", b"abc\x00" * 20)


def test_docx_extract():
    doc = Document()
    doc.add_paragraph(RESUME)
    out = BytesIO()
    doc.save(out)
    text, kind = extract_resume("resume.docx", None, out.getvalue())
    assert kind == "docx" and "RAG" in text


def test_profile_plan_dynamic_turn_and_report():
    candidate = parse_candidate(RESUME)
    job = parse_job(BUILTIN_JD, True)
    plan = create_plan(candidate, job)
    assert 4 <= len(plan.topics) <= 6
    assert abs(sum(x.weight for x in plan.topics) - 1) < .01
    session = Session(candidate=candidate, job=job, plan=plan, current_topic_index=0, report={"pending_question": plan.topics[0].question})
    turn, question = next_turn(session, "我做过这个项目。")
    assert turn.next_action in {"request_evidence", "probe_depth", "clarify"}
    assert question
    session.turns.append(turn)
    report = make_report(session)
    assert report["coverage"] > 0
    assert any(x["status"] == "未考察" for x in report["topics"])


def test_experimental_job_has_c_level_topics_without_correctness():
    candidate = parse_candidate(RESUME)
    job = parse_job("数据分析实习生\n熟悉 SQL 和可视化，能够完成业务分析。", False)
    plan = create_plan(candidate, job)
    assert job.mode == "experimental"
    assert all(x.source_level == "C" for x in plan.topics)
    session = Session(candidate=candidate, job=job, plan=plan, report={"pending_question": plan.topics[0].question})
    turn, _ = next_turn(session, "首先我会分析业务目标，其次用项目数据进行验证，最后复盘结果和风险。")
    assert turn.evaluation.correctness is None


def test_question_bank_file_formats():
    assert "RAG" in parse_question_file("q.txt", "请说明 RAG 的召回评估方法".encode())[0]["question"]
    assert "SQL" in parse_question_file("q.md", "- 请说明 SQL 窗口函数".encode())[0]["question"]
    assert "留存" in parse_question_file("q.csv", "question,topic\n请如何计算留存,SQL".encode())[0]["question"]
    assert "A/B" in parse_question_file("q.json", '{"questions":[{"question":"如何设计 A/B 测试","topic":"统计"}]}'.encode())[0]["question"]


def test_multiline_question_keeps_numbers_conditions_and_code():
    text = """1. 请分析下面代码，并说明 1000 条输入时的复杂度：
```python
for i in range(10):
    print(i)
```
答案：O(1)
来源：个人整理

2. 请说明另一种实现？"""
    entries = parse_question_text(text)
    assert len(entries) == 2
    assert "1000" in entries[0]["question"]
    assert "range(10)" in entries[0]["question"]
    assert entries[0]["answer"] == "O(1)"
    assert entries[0]["source"] == "个人整理"


def _confirmed_bank(store, name, questions):
    bank = store.create_bank(name)
    entries = parse_question_text("\n".join(f"{i}. {question}" for i, question in enumerate(questions, 1)))
    draft = store.create_draft(entries)
    store.confirm_draft(draft["id"], bank["id"], [
        {
            "question": entry["question"],
            "topic": "rag",
            "role": "AI应用开发",
            "source": name,
            "origin_ids": entry["origin_ids"],
        }
        for entry in draft["entries"]
    ])
    return bank


def test_normal_plan_uses_four_user_questions_even_in_same_topic(tmp_path):
    store = SessionStore(tmp_path / "four-plus-two.db")
    bank = _confirmed_bank(store, "RAG 专题", [
        "请说明 RAG 切分策略一？",
        "请说明 RAG 召回策略二？",
        "请说明 RAG 重排策略三？",
        "请说明 RAG 评估策略四？",
    ])
    candidate = parse_candidate(RESUME)
    job = parse_job(BUILTIN_JD, True)
    job.selected_bank_ids = [bank["id"]]
    plan = create_plan(candidate, job, store.questions_by_topic(), store.bank_questions(), set())
    assert len(plan.topics) == 6
    assert plan.user_question_count == 4
    assert plan.supplement_question_count == 2
    assert sum(t.topic_key == "rag" and t.selection_kind == "user" for t in plan.topics) == 4
    assert len(set(plan.main_question_ids)) == 6


def test_specialized_plan_shortens_and_never_adds_external_questions(tmp_path):
    store = SessionStore(tmp_path / "specialized.db")
    bank = _confirmed_bank(store, "两道专项题", ["请说明 RAG 问题一？", "请说明 RAG 问题二？"])
    candidate = parse_candidate(RESUME)
    job = parse_job(BUILTIN_JD, True)
    job.selected_bank_ids = [bank["id"]]
    job.practice_mode = "specialized"
    plan = create_plan(candidate, job, store.questions_by_topic(), store.bank_questions(), set())
    assert len(plan.topics) == 2
    assert plan.user_question_count == 2
    assert plan.supplement_question_count == 0
    assert all(t.selected_bank_id == bank["id"] for t in plan.topics)


def test_displayed_question_is_practiced_and_repeat_is_explicit(tmp_path):
    store = SessionStore(tmp_path / "practice.db")
    bank = _confirmed_bank(store, "单题专项", ["请说明 RAG 唯一问题？"])
    candidate = parse_candidate(RESUME)
    job = parse_job(BUILTIN_JD, True)
    job.selected_bank_ids = [bank["id"]]
    job.practice_mode = "specialized"
    plan = create_plan(candidate, job, store.questions_by_topic(), store.bank_questions(), set())
    session = Session(candidate=candidate, job=job, plan=plan, status=SessionStatus.ASKING,
                      report={"pending_question": plan.topics[0].question, "pending_kind": "main"})
    store.save(session)
    assert store.practiced_ids() == {plan.topics[0].question_id}
    no_repeat = create_plan(candidate, job, store.questions_by_topic(), store.bank_questions(), store.practiced_ids())
    assert no_repeat.topics == []
    job.allow_repeats = True
    repeated = create_plan(candidate, job, store.questions_by_topic(), store.bank_questions(), store.practiced_ids())
    assert [t.question_id for t in repeated.topics] == [plan.topics[0].question_id]


def test_same_content_keeps_two_bank_memberships_and_sources(tmp_path):
    store = SessionStore(tmp_path / "membership.db")
    first = _confirmed_bank(store, "题库甲", ["请说明 RAG 如何评估？"])
    second = _confirmed_bank(store, "题库乙", ["请说明 RAG 如何评估？"])
    first_item = store.bank_questions(first["id"])[0]
    second_item = store.bank_questions(second["id"])[0]
    assert first_item["question_id"] == second_item["question_id"]
    assert first_item["sources"][0]["source"] == "题库甲"
    assert second_item["sources"][0]["source"] == "题库乙"
    assert len(store.list_questions()) == 65


def test_role_mismatch_is_explained_in_normal_mode_but_allowed_in_specialized(tmp_path):
    store = SessionStore(tmp_path / "role-filter.db")
    bank = store.create_bank("Java 题库")
    raw = store.create_draft(parse_question_text("1. 请解释 Java JVM 的内存模型？"))
    store.confirm_draft(raw["id"], bank["id"], [{
        "question": raw["entries"][0]["question"],
        "topic": "backend",
        "role": "Java 后端",
        "source": "用户整理",
        "origin_ids": raw["entries"][0]["origin_ids"],
    }])
    candidate = parse_candidate(RESUME)
    job = parse_job("数据分析师\n熟悉 SQL、统计与业务分析。", False)
    job.selected_bank_ids = [bank["id"]]
    normal = create_plan(candidate, job, store.questions_by_topic(), store.bank_questions(), set())
    assert all("Java JVM" not in topic.question for topic in normal.topics)
    assert any("与目标岗位不匹配" in note for note in normal.selection_notes)
    job.practice_mode = "specialized"
    specialized = create_plan(candidate, job, store.questions_by_topic(), store.bank_questions(), set())
    assert [topic.question for topic in specialized.topics] == ["请解释 Java JVM 的内存模型？"]


def test_open_question_bank_is_seeded_and_used(tmp_path):
    question_store = SessionStore(tmp_path / "questions.db")
    stats = question_store.question_bank_stats()
    assert stats["questions"] == 64
    assert stats["sources"] == 10
    assert stats["authority_grades"] == {"A": 33, "B": 27, "C": 4}
    candidate = parse_candidate(RESUME)
    job = parse_job(BUILTIN_JD, True)
    plan = create_plan(candidate, job, question_store.questions_by_topic())
    sourced = [topic for topic in plan.topics if topic.question_bank_id]
    assert sourced
    assert all(topic.source_url and topic.source_license for topic in sourced)


def test_domestic_question_metadata_is_complete(tmp_path):
    question_store = SessionStore(tmp_path / "domestic.db")
    questions = question_store.questions_by_topic()
    domestic = [item for items in questions.values() for item in items if item["id"].startswith("cn-")]
    assert len(domestic) == 33
    assert all(item["answer_outline"] and item["scoring_points"] for item in domestic)
    assert all(item["common_mistakes"] and item["verification_urls"] for item in domestic)
    assert all(item["verified_at"] == "2026-09-07" and item["confidence"] >= 0.88 for item in domestic)


def test_company_questions_are_preferred_when_available(tmp_path):
    question_store = SessionStore(tmp_path / "company.db")
    candidate = parse_candidate(RESUME)
    job = parse_job(BUILTIN_JD, True, "字节跳动")
    plan = create_plan(candidate, job, question_store.questions_by_topic())

    company_topics = [
        topic for topic in plan.topics
        if topic.source_note.startswith("字节跳动公开面经回忆题")
    ]
    assert company_topics
    assert all(topic.source_url.startswith("https://www.nowcoder.com/") for topic in company_topics)


def test_unknown_company_falls_back_to_general_question_bank(tmp_path):
    question_store = SessionStore(tmp_path / "fallback.db")
    candidate = parse_candidate(RESUME)
    job = parse_job(BUILTIN_JD, True, "不存在的公司")
    plan = create_plan(candidate, job, question_store.questions_by_topic())

    assert plan.topics
    assert all("不存在的公司公开面经回忆题" not in topic.source_note for topic in plan.topics)
