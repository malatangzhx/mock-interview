from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStatus(str, Enum):
    DRAFT = "DRAFT"
    CONFIRMING = "CONFIRMING"
    READY = "READY"
    DEVICE_CHECK = "DEVICE_CHECK"
    ASKING = "ASKING"
    TRANSCRIPT_CONFIRM = "TRANSCRIPT_CONFIRM"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class CandidateProfile(BaseModel):
    id: str = Field(default_factory=lambda: uid("candidate"))
    target_role: str = "AI应用开发实习生"
    education: list[str] = []
    skills: list[str] = []
    projects: list[str] = []
    evidence_refs: list[str] = []
    uncertain_fields: list[str] = []
    source_document_id: str | None = None
    source_text_hash: str
    schema_version: str = "v1"


class Requirement(BaseModel):
    name: str
    evidence: str
    priority: int = Field(ge=1, le=5)


class JobProfile(BaseModel):
    id: str = Field(default_factory=lambda: uid("job"))
    title: str
    target_company: str = "通用"
    use_user_question_bank: bool = False
    selected_bank_ids: list[str] = []
    practice_mode: Literal["normal", "specialized"] = "normal"
    allow_repeats: bool = False
    mode: Literal["validated", "experimental"]
    requirements: list[Requirement]
    source_text_hash: str
    schema_version: str = "v1"


class Topic(BaseModel):
    id: str = Field(default_factory=lambda: uid("topic"))
    name: str
    evidence: list[str]
    objective: str
    weight: float = Field(gt=0)
    difficulty: int = Field(default=3, ge=1, le=5)
    question: str
    alternate_question: str
    follow_up_questions: list[str]
    completion_condition: str
    estimated_minutes: int
    source_level: Literal["A", "B", "C"] = "A"
    source_note: str
    question_bank_id: str | None = None
    source_url: str | None = None
    source_license: str | None = None
    question_id: str | None = None
    topic_key: str = ""
    question_version: int = 1
    selection_kind: Literal["user", "supplement"] = "supplement"
    selected_bank_id: str | None = None
    selection_reason: str = ""
    content_type: str = "original"
    sources: list[dict] = []
    answer_verification: str = "unverified"


class InterviewPlan(BaseModel):
    id: str = Field(default_factory=lambda: uid("plan"))
    estimated_minutes: int
    hard_limit_minutes: int = 60
    max_follow_ups_per_topic: int = 2
    topics: list[Topic]
    required_coverage: float = 1.0
    strategy_version: str = "v1"
    practice_mode: Literal["normal", "specialized"] = "normal"
    selected_bank_ids: list[str] = []
    allow_repeats: bool = False
    selection_notes: list[str] = []
    user_question_count: int = 0
    supplement_question_count: int = 0
    main_question_ids: list[str] = []


class AnswerEvaluation(BaseModel):
    correctness: int | None = Field(default=None, ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    depth: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    evidence: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0, le=1)
    strengths: list[str]
    gaps: list[str]
    evidence_quote: str
    rubric_version: str = "v1"


class Turn(BaseModel):
    sequence: int
    topic_id: str
    topic_name: str
    question: str
    confirmed_answer: str
    evaluation: AnswerEvaluation | None
    next_action: str
    decision_reason: str
    created_at: str = Field(default_factory=now_iso)
    question_id: str | None = None
    question_kind: Literal["main", "generated_followup"] = "main"


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uid("session"))
    status: SessionStatus = SessionStatus.DRAFT
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    privacy_accepted: bool = True
    resume_text: str = ""
    document: dict | None = None
    candidate: CandidateProfile | None = None
    job: JobProfile | None = None
    plan: InterviewPlan | None = None
    turns: list[Turn] = []
    current_topic_index: int = 0
    topic_follow_ups: dict[str, int] = {}
    degraded: bool = False
    incomplete: bool = False
    report: dict | None = None
    phase: Literal["training", "post_test"] = "training"
    baseline_scores: dict[str, float] = {}
    extension_asked: bool = False
    counter_question: str | None = None
    prompt_version: str = "offline-v1"
    model_version: str = "deterministic-offline"
    started_at: str | None = None
    parent_session_id: str | None = None
    preflight: dict = {}


class ProfileRequest(BaseModel):
    resume_text: str | None = None
    jd_text: str | None = None
    use_builtin_job: bool = True
    builtin_job: Literal["ai", "data"] | None = None
    target_company: str = Field(default="通用", min_length=1, max_length=40)
    use_user_question_bank: bool = False
    selected_bank_ids: list[str] = []
    practice_mode: Literal["normal", "specialized"] = "normal"
    allow_repeats: bool = False


class CandidateAnalysisRequest(BaseModel):
    text: str = Field(default="", max_length=100_000)
    skip: bool = False


class JobAnalysisRequest(BaseModel):
    text: str = Field(default="", max_length=100_000)
    use_builtin_job: bool = True
    builtin_job: Literal["ai", "data"] | None = None
    target_company: str = Field(default="通用", min_length=1, max_length=40)
    skip: bool = False


class UserQuestionImportRequest(BaseModel):
    questions_text: str = Field(min_length=3, max_length=50_000)


class BankRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    company: str = Field(default="", max_length=100)
    role: str = Field(default="", max_length=100)


class DraftEntry(BaseModel):
    question: str = Field(min_length=3, max_length=10000)
    topic: str = Field(default="", max_length=100)
    role: str = Field(default="", max_length=100)
    company: str = Field(default="", max_length=100)
    answer: str = Field(default="", max_length=20000)
    source: str = Field(default="", max_length=2000)
    url: str = Field(default="", max_length=2000)
    interview_date: str = Field(default="", max_length=100)
    content_type: Literal["original", "recalled", "generated_variant"] = "original"
    modification_note: str = Field(default="", max_length=2000)
    origin_ids: list[str] = []


class ConfirmImportRequest(BaseModel):
    bank_id: str
    entries: list[DraftEntry] = Field(min_length=1, max_length=100)
    company: str = ""
    role: str = ""


class ConfirmRequest(BaseModel):
    candidate: CandidateProfile
    job: JobProfile


class AnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)


class FinishRequest(BaseModel):
    reason: Literal["user", "complete", "time_limit", "declined_extension"] = "user"


class CounterQuestionRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class AIConnectionRequest(BaseModel):
    provider: Literal["openai", "openai_responses", "deepseek", "qwen", "anthropic", "gemini", "moonshot", "zhipu", "doubao", "siliconflow", "minimax", "apiznyl", "custom"] = "openai"
    base_url: str | None = Field(default=None, max_length=500)
    realtime_url: str | None = Field(default=None, max_length=500)
    api_key: str = Field(min_length=1, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    wire_api: Literal["auto", "chat", "responses", "anthropic"] | None = None
    timeout_seconds: int = Field(default=90, ge=10, le=180)
    stream: bool = False


class ModelInterviewTurn(BaseModel):
    interviewer_reply: str = Field(min_length=2, max_length=1000)
    action: Literal["clarify", "request_evidence", "probe_depth", "challenge", "give_hint", "switch_topic"]
    topic_complete: bool = False
    evaluation: AnswerEvaluation
