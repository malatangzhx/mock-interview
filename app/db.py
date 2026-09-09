from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path

from .models import Session, now_iso
from .banks import QuestionBankStore


class SessionStore(QuestionBankStore):
    def __init__(self, path: Path, seed_path: Path | None = None):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL)")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS question_sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    repository_url TEXT NOT NULL,
                    content_url TEXT NOT NULL,
                    license_name TEXT NOT NULL,
                    license_url TEXT NOT NULL,
                    attribution TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS interview_questions (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES question_sources(id),
                    topic_key TEXT NOT NULL,
                    roles TEXT NOT NULL,
                    question TEXT NOT NULL,
                    alternate_question TEXT NOT NULL,
                    follow_up_questions TEXT NOT NULL,
                    difficulty INTEGER NOT NULL CHECK(difficulty BETWEEN 1 AND 5),
                    language TEXT NOT NULL DEFAULT 'zh-CN',
                    active INTEGER NOT NULL DEFAULT 1,
                    content_hash TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_questions_topic_active
                ON interview_questions(topic_key, active);
            """)
            self._migrate_question_bank(con)
        if seed_path:
            self.seed_questions(seed_path)
        else:
            data_dir = Path(__file__).resolve().parent.parent / "data"
            for question_file in sorted(data_dir.glob("*_questions.json")):
                self.seed_questions(question_file)

        self.migrate_banks()

    @staticmethod
    def _migrate_question_bank(con: sqlite3.Connection) -> None:
        """Additive migrations keep existing local databases usable."""
        source_columns = {
            "source_type": "TEXT NOT NULL DEFAULT 'open_source'",
            "authority_level": "TEXT NOT NULL DEFAULT 'B'",
            "region": "TEXT NOT NULL DEFAULT 'global'",
        }
        question_columns = {
            "companies": "TEXT NOT NULL DEFAULT '[]'",
            "stages": "TEXT NOT NULL DEFAULT '[]'",
            "employment_types": "TEXT NOT NULL DEFAULT '[]'",
            "question_type": "TEXT NOT NULL DEFAULT '知识问答'",
            "frequency_band": "TEXT NOT NULL DEFAULT 'unknown'",
            "confidence": "REAL NOT NULL DEFAULT 0.5",
            "answer_outline": "TEXT NOT NULL DEFAULT '[]'",
            "scoring_points": "TEXT NOT NULL DEFAULT '[]'",
            "common_mistakes": "TEXT NOT NULL DEFAULT '[]'",
            "verification_urls": "TEXT NOT NULL DEFAULT '[]'",
            "verified_at": "TEXT",
            "tags": "TEXT NOT NULL DEFAULT '[]'",
            "authority_grade": "TEXT NOT NULL DEFAULT 'B'",
            "reported_at": "TEXT",
            "source_claim": "TEXT NOT NULL DEFAULT 'curated'",
        }
        existing_sources = {row[1] for row in con.execute("PRAGMA table_info(question_sources)")}
        existing_questions = {row[1] for row in con.execute("PRAGMA table_info(interview_questions)")}
        for name, definition in source_columns.items():
            if name not in existing_sources:
                con.execute(f"ALTER TABLE question_sources ADD COLUMN {name} {definition}")
        for name, definition in question_columns.items():
            if name not in existing_questions:
                con.execute(f"ALTER TABLE interview_questions ADD COLUMN {name} {definition}")

    def _connect(self):
        return sqlite3.connect(self.path)

    def get(self, session_id: str) -> Session | None:
        with self._connect() as con:
            row = con.execute("SELECT payload FROM sessions WHERE id=?", (session_id,)).fetchone()
        return Session.model_validate_json(row[0]) if row else None

    def list(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT payload FROM sessions ORDER BY updated_at DESC").fetchall()
        result = []
        for (payload,) in rows:
            s = json.loads(payload)
            result.append({"id": s["id"], "created_at": s["created_at"], "updated_at": s["updated_at"], "status": s["status"], "job_title": (s.get("job") or {}).get("title"), "mode": (s.get("job") or {}).get("mode"), "has_report": bool(s.get("report"))})
        return result

    def delete(self, session_id: str) -> bool:
        with self._connect() as con:
            cur = con.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return cur.rowcount > 0

    def seed_questions(self, seed_path: Path) -> None:
        """Idempotently load the reviewed, license-tracked question snapshot."""
        if not seed_path.exists():
            return
        data = json.loads(seed_path.read_text(encoding="utf-8"))
        with self._connect() as con:
            for source in data.get("sources", []):
                source = {
                    "source_type": "open_source",
                    "authority_level": "B",
                    "region": "global",
                    **source,
                }
                con.execute(
                    """INSERT INTO question_sources
                    (id,name,repository_url,content_url,license_name,license_url,attribution,retrieved_at,
                     source_type,authority_level,region)
                    VALUES(:id,:name,:repository_url,:content_url,:license_name,:license_url,:attribution,:retrieved_at,
                           :source_type,:authority_level,:region)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                    repository_url=excluded.repository_url,content_url=excluded.content_url,
                    license_name=excluded.license_name,license_url=excluded.license_url,
                    attribution=excluded.attribution,retrieved_at=excluded.retrieved_at,
                    source_type=excluded.source_type,authority_level=excluded.authority_level,
                    region=excluded.region""",
                    source,
                )
            for question in data.get("questions", []):
                digest = hashlib.sha256(question["question"].encode("utf-8")).hexdigest()
                json_fields = (
                    "roles", "follow_up_questions", "companies", "stages", "employment_types",
                    "answer_outline", "scoring_points", "common_mistakes", "verification_urls", "tags",
                )
                defaults = {
                    "companies": ["国内互联网通用"], "stages": ["技术面"],
                    "employment_types": ["实习", "校招", "社招"], "question_type": "知识问答",
                    "frequency_band": "unknown", "confidence": 0.5, "verified_at": None,
                    "authority_grade": "B", "reported_at": None, "source_claim": "curated",
                    "answer_outline": [], "scoring_points": [],
                    "common_mistakes": [], "verification_urls": [], "tags": [],
                }
                row = {**defaults, **question, "content_hash": digest}
                for field in json_fields:
                    row[field] = json.dumps(row.get(field, []), ensure_ascii=False)
                con.execute(
                    """INSERT INTO interview_questions
                    (id,source_id,topic_key,roles,question,alternate_question,follow_up_questions,difficulty,language,active,content_hash,
                     companies,stages,employment_types,question_type,frequency_band,confidence,answer_outline,scoring_points,
                     common_mistakes,verification_urls,verified_at,tags,authority_grade,reported_at,source_claim)
                    VALUES(:id,:source_id,:topic_key,:roles,:question,:alternate_question,:follow_up_questions,:difficulty,:language,1,:content_hash,
                           :companies,:stages,:employment_types,:question_type,:frequency_band,:confidence,:answer_outline,:scoring_points,
                           :common_mistakes,:verification_urls,:verified_at,:tags,:authority_grade,:reported_at,:source_claim)
                    ON CONFLICT(id) DO UPDATE SET source_id=excluded.source_id,topic_key=excluded.topic_key,
                    roles=excluded.roles,question=excluded.question,alternate_question=excluded.alternate_question,
                    follow_up_questions=excluded.follow_up_questions,difficulty=excluded.difficulty,
                    language=excluded.language,active=1,content_hash=excluded.content_hash,
                    companies=excluded.companies,stages=excluded.stages,employment_types=excluded.employment_types,
                    question_type=excluded.question_type,frequency_band=excluded.frequency_band,confidence=excluded.confidence,
                    answer_outline=excluded.answer_outline,scoring_points=excluded.scoring_points,
                    common_mistakes=excluded.common_mistakes,verification_urls=excluded.verification_urls,
                    verified_at=excluded.verified_at,tags=excluded.tags,authority_grade=excluded.authority_grade,
                    reported_at=excluded.reported_at,source_claim=excluded.source_claim""",
                    row,
                )

    def questions_by_topic(self) -> dict[str, list[dict]]:
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT q.*, s.name AS source_name, s.content_url, s.license_name, s.attribution, s.source_type
                FROM interview_questions q JOIN question_sources s ON s.id=q.source_id
                WHERE q.active=1 ORDER BY q.topic_key, q.id
            """).fetchall()
        result: dict[str, list[dict]] = {}
        for raw in rows:
            item = dict(raw)
            for field in ("roles", "follow_up_questions", "companies", "stages", "employment_types",
                          "answer_outline", "scoring_points", "common_mistakes", "verification_urls", "tags"):
                item[field] = json.loads(item[field])
            result.setdefault(item["topic_key"], []).append(item)
        return result

    @staticmethod
    def _topic_for_user_question(question: str) -> str:
        text = question.lower()
        rules = {
            "sql": ("sql", "join", "窗口函数", "窗口", "索引", "查询"),
            "statistics": ("ab", "a/b", "假设检验", "置信区间", "p值", "统计"),
            "quality": ("数据质量", "脏数据", "缺失", "异常值", "口径"),
            "visualization": ("可视化", "仪表盘", "报表", "图表", "bi"),
            "business": ("业务", "指标", "增长", "转化", "留存", "漏斗"),
            "rag": ("rag", "检索", "向量", "embedding", "召回"),
            "agent": ("agent", "智能体", "工具调用", "function call", "mcp"),
            "llm": ("llm", "大模型", "prompt", "提示词", "结构化输出"),
            "python": ("python", "异步", "并发", "装饰器", "生成器"),
            "backend": ("fastapi", "接口", "后端", "缓存", "数据库", "部署"),
        }
        for topic, keywords in rules.items():
            if any(keyword in text for keyword in keywords):
                return topic
        return "project"

    def user_question_bank_stats(self) -> dict:
        with self._connect() as con:
            count = con.execute("SELECT COUNT(DISTINCT question_id) FROM bank_memberships").fetchone()[0]
        return {"questions": count, "banks": self.list_banks()}

    def question_bank_stats(self) -> dict:
        with self._connect() as con:
            question_count = con.execute("SELECT COUNT(*) FROM interview_questions WHERE active=1").fetchone()[0]
            source_count = con.execute("SELECT COUNT(*) FROM question_sources").fetchone()[0]
            topics = [row[0] for row in con.execute(
                "SELECT DISTINCT topic_key FROM interview_questions WHERE active=1 ORDER BY topic_key"
            ).fetchall()]
            by_type = dict(con.execute(
                "SELECT question_type, COUNT(*) FROM interview_questions WHERE active=1 GROUP BY question_type ORDER BY COUNT(*) DESC"
            ).fetchall())
            by_authority = dict(con.execute(
                "SELECT authority_grade, COUNT(*) FROM interview_questions WHERE active=1 GROUP BY authority_grade ORDER BY authority_grade"
            ).fetchall())
        return {"questions": question_count, "sources": source_count, "topics": topics,
                "question_types": by_type, "authority_grades": by_authority}

    def list_questions(self, topic: str | None = None, role: str | None = None) -> list[dict]:
        query = """SELECT q.id,q.topic_key,q.roles,q.question,q.difficulty,q.companies,q.stages,
                   q.employment_types,q.question_type,q.frequency_band,q.confidence,q.tags,
                   q.authority_grade,q.verified_at,q.reported_at,q.source_claim,q.answer_outline,q.scoring_points,q.common_mistakes,
                   q.verification_urls,s.name AS source_name,s.content_url,s.license_name
                   FROM interview_questions q JOIN question_sources s ON s.id=q.source_id WHERE q.active=1"""
        params: list[str] = []
        if topic:
            query += " AND q.topic_key=?"
            params.append(topic)
        query += " ORDER BY q.topic_key,q.difficulty,q.id"
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = [dict(row) for row in con.execute(query, params).fetchall()]
        json_fields = ("roles", "companies", "stages", "employment_types", "tags", "answer_outline",
                       "scoring_points", "common_mistakes", "verification_urls")
        result = []
        for row in rows:
            for field in json_fields:
                row[field] = json.loads(row[field])
            if role and role not in row["roles"]:
                continue
            result.append(row)
        return result

    def company_stats(self) -> list[dict]:
        counts: dict[str, int] = {}
        with self._connect() as con:
            rows = con.execute("SELECT companies FROM interview_questions WHERE active=1").fetchall()
        for (raw_companies,) in rows:
            for company in json.loads(raw_companies):
                if company not in {"通用", "国内互联网通用"}:
                    counts[company] = counts.get(company, 0) + 1
        return [{"name": "通用", "question_count": 0}] + [
            {"name": name, "question_count": count}
            for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    def list_question_sources(self) -> list[dict]:
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT s.*, COUNT(q.id) AS question_count
                FROM question_sources s LEFT JOIN interview_questions q
                ON q.source_id=s.id AND q.active=1
                GROUP BY s.id ORDER BY s.name
            """).fetchall()
        return [dict(row) for row in rows]
