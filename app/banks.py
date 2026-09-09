from __future__ import annotations

import hashlib
import json
import sqlite3
from difflib import SequenceMatcher

from .models import DraftEntry, now_iso, uid


def dump(value):
    return json.dumps(value, ensure_ascii=False)


class QuestionBankStore:
    def migrate_banks(self):
        with self._connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS user_banks (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, company TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS bank_memberships (
                    bank_id TEXT NOT NULL, question_id TEXT NOT NULL, metadata TEXT NOT NULL,
                    PRIMARY KEY(bank_id, question_id));
                CREATE TABLE IF NOT EXISTS question_provenance (
                    id TEXT PRIMARY KEY, question_id TEXT NOT NULL, bank_id TEXT NOT NULL,
                    batch_id TEXT, origin_id TEXT, payload TEXT NOT NULL,
                    UNIQUE(batch_id, origin_id));
                CREATE TABLE IF NOT EXISTS reference_answers (
                    id TEXT PRIMARY KEY, question_id TEXT NOT NULL, bank_id TEXT NOT NULL,
                    version INTEGER NOT NULL, answer TEXT NOT NULL, verification_status TEXT NOT NULL,
                    verification_basis TEXT NOT NULL DEFAULT '[]', reviewed_at TEXT);
                CREATE TABLE IF NOT EXISTS import_drafts (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
                    result TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS question_practice (
                    question_id TEXT NOT NULL, session_id TEXT NOT NULL, actual_question TEXT NOT NULL,
                    displayed_at TEXT NOT NULL, answered_at TEXT, PRIMARY KEY(question_id, session_id));
            ''')
            columns = {r[1] for r in con.execute('PRAGMA table_info(interview_questions)')}
            if 'version' not in columns:
                con.execute('ALTER TABLE interview_questions ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
            # Only reliable legacy IDs are migrated; historical sessions are never fuzzy matched.
            con.execute('CREATE TABLE IF NOT EXISTS bank_migrations (name TEXT PRIMARY KEY)')
            if con.execute("SELECT 1 FROM bank_migrations WHERE name='legacy-v1'").fetchone():
                return
            con.execute("INSERT INTO bank_migrations VALUES('legacy-v1')")
            old = con.execute("SELECT id,question,topic_key FROM interview_questions WHERE source_claim='user_provided'").fetchall()
            if old:
                con.execute("INSERT OR IGNORE INTO user_banks(id,name,created_at) VALUES('legacy-user-bank','历史导入题库',?)", (now_iso(),))
                for qid, question, topic in old:
                    if con.execute('SELECT 1 FROM bank_memberships WHERE question_id=?', (qid,)).fetchone():
                        continue
                    metadata = {"topic": topic, "role": "", "company": "", "content_type": "original"}
                    con.execute('INSERT OR IGNORE INTO bank_memberships VALUES(?,?,?)', ('legacy-user-bank', qid, dump(metadata)))
                    con.execute('INSERT OR IGNORE INTO question_provenance VALUES(?,?,?,?,?,?)',
                                ('legacy-'+qid, qid, 'legacy-user-bank', 'legacy', qid,
                                 dump({"original_question": question, "source": "历史本地导入；未知元数据留空", "company": "", "role": "", "interview_date": "", "filename": "", "position": ""})))

    def create_bank(self, name, company='', role=''):
        if not name.strip():
            raise ValueError('题库名称不能为空')
        bank_id = uid('bank')
        with self._connect() as con:
            con.execute('INSERT INTO user_banks VALUES(?,?,?,?,?,?)', (bank_id, name.strip(), company, role, now_iso(), 0))
        return next(b for b in self.list_banks() if b['id'] == bank_id)

    def list_banks(self):
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute('''SELECT b.*, COUNT(m.question_id) AS questions FROM user_banks b
                LEFT JOIN bank_memberships m ON m.bank_id=b.id GROUP BY b.id ORDER BY b.sort_order,b.created_at,b.id''')]

    def update_bank(self, bank_id, name, company='', role=''):
        if not name.strip():
            raise ValueError('题库名称不能为空')
        with self._connect() as con:
            if not con.execute('UPDATE user_banks SET name=?,company=?,role=? WHERE id=?', (name.strip(), company, role, bank_id)).rowcount:
                raise ValueError('题库不存在')
        return {'updated': True}

    def create_draft(self, entries):
        if not entries or len(entries) > 100:
            raise ValueError('每批须包含 1～100 个预览条目')
        existing = [(q['id'], q['question']) for group in self.questions_by_topic().values() for q in group]
        for entry in entries:
            entry['id'] = uid('origin')
            entry['origin_ids'] = [entry['id']]
            entry.setdefault('issues', [])
            entry['similar_question_ids'] = [qid for qid, text in existing if text != entry['question'] and SequenceMatcher(None, text, entry['question']).ratio() > .85][:5]
            if entry['similar_question_ids']:
                entry['issues'].append('存在相似题，仅提示；不会自动合并')
        draft = {'id': uid('draft'), 'entries': entries, 'confirmed': False}
        with self._connect() as con:
            con.execute('INSERT INTO import_drafts(id,payload,created_at) VALUES(?,?,?)', (draft['id'], dump(draft), now_iso()))
        return draft

    def get_draft(self, draft_id):
        with self._connect() as con:
            row = con.execute('SELECT payload,confirmed,result FROM import_drafts WHERE id=?', (draft_id,)).fetchone()
        if not row:
            raise ValueError('导入草稿不存在')
        return {**json.loads(row[0]), 'confirmed': bool(row[1]), 'result': json.loads(row[2]) if row[2] else None}

    def _insert_content(self, con, question, topic):
        digest = hashlib.sha256(question.encode('utf-8')).hexdigest()
        row = con.execute('SELECT id FROM interview_questions WHERE content_hash=?', (digest,)).fetchone()
        if row:
            return row[0], False
        con.execute('''INSERT OR IGNORE INTO question_sources
            (id,name,repository_url,content_url,license_name,license_url,attribution,retrieved_at,source_type,authority_level,region)
            VALUES('user-provided-local','用户自行提供的本地题库','','','User provided','','用户确认导入',?,'user_provided','C','local')''', (now_iso(),))
        qid = 'user-'+digest[:24]
        con.execute('''INSERT INTO interview_questions
            (id,source_id,topic_key,roles,question,alternate_question,follow_up_questions,difficulty,content_hash,source_claim,authority_grade)
            VALUES(?,'user-provided-local',?,'[]',?,?,'["请结合具体经历说明做法、依据和结果。"]',3,?,'user_provided','C')''',
                    (qid, topic, question, question, digest))
        return qid, True

    def confirm_draft(self, draft_id, bank_id, entries, company='', role=''):
        # BEGIN IMMEDIATE serializes concurrent confirmations; the whole batch commits once.
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT payload,confirmed,result FROM import_drafts WHERE id=?', (draft_id,)).fetchone()
            if not row:
                raise ValueError('导入草稿不存在')
            if row[1]:
                result = json.loads(row[2])
                if result['bank_id'] != bank_id:
                    raise ValueError('该草稿已确认至其他题库')
                return result
            bank = con.execute('SELECT company,role FROM user_banks WHERE id=?', (bank_id,)).fetchone()
            if not bank:
                raise ValueError('请先新建或选择题库')
            originals = {e['id']: e for e in json.loads(row[0])['entries']}
            if not 1 <= len(entries) <= 100:
                raise ValueError('请保留 1～100 道题')
            added = duplicates = 0
            used_origins = set()
            for raw in entries:
                entry = DraftEntry.model_validate(raw).model_dump()
                if not entry['question'].strip():
                    raise ValueError('题干不能为空')
                if not entry['origin_ids'] or any(o not in originals or o in used_origins for o in entry['origin_ids']):
                    raise ValueError('条目的原始来源无效或重复，请重新预览')
                used_origins.update(entry['origin_ids'])
                entry['topic'] = entry['topic'] or self._topic_for_user_question(entry['question'])
                entry['company'] = entry['company'] or company or bank[0]
                entry['role'] = entry['role'] or role or bank[1]
                qid, created = self._insert_content(con, entry['question'], entry['topic'])
                added += int(created)
                duplicates += int(not created)
                con.execute('INSERT INTO bank_memberships VALUES(?,?,?) ON CONFLICT(bank_id,question_id) DO UPDATE SET metadata=excluded.metadata',
                            (bank_id, qid, dump({k: v for k, v in entry.items() if k not in {'answer', 'question', 'origin_ids'}})))
                for origin_id in entry['origin_ids']:
                    original = originals[origin_id]
                    provenance = {**entry, 'original_question': original['question'], 'original_metadata': original,
                                  'filename': original.get('filename', ''), 'position': original.get('position', ''),
                                  'context': original.get('context', ''), 'confirmed_at': now_iso()}
                    if len(entry['origin_ids']) > 1:
                        for field in ('source', 'url', 'company', 'role', 'interview_date'):
                            provenance[field] = original.get(field) or entry.get(field, '')
                    provenance.pop('answer', None)
                    con.execute('INSERT INTO question_provenance VALUES(?,?,?,?,?,?)', (uid('source'), qid, bank_id, draft_id, origin_id, dump(provenance)))
                    answer = entry['answer'] or original.get('answer', '')
                    if answer:
                        version = con.execute('SELECT COALESCE(MAX(version),0)+1 FROM reference_answers WHERE question_id=? AND bank_id=?', (qid, bank_id)).fetchone()[0]
                        con.execute('INSERT INTO reference_answers(id,question_id,bank_id,version,answer,verification_status) VALUES(?,?,?,?,?,?)',
                                    (uid('answer'), qid, bank_id, version, answer, 'unverified'))
            count = con.execute('SELECT COUNT(*) FROM bank_memberships WHERE bank_id=?', (bank_id,)).fetchone()[0]
            result = {'bank_id': bank_id, 'added': added, 'duplicates': duplicates, 'questions': count, 'confirmed': True}
            con.execute('UPDATE import_drafts SET confirmed=1,result=? WHERE id=?', (dump(result), draft_id))
        return result

    def bank_questions(self, bank_id=None):
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute('''SELECT m.*, b.name AS bank_name, q.question, q.version FROM bank_memberships m
                JOIN user_banks b ON b.id=m.bank_id JOIN interview_questions q ON q.id=m.question_id''').fetchall()
            result = []
            for r in rows:
                if bank_id and r['bank_id'] != bank_id:
                    continue
                item = {**dict(r), **json.loads(r['metadata'])}
                item.pop('metadata')
                item['sources'] = [json.loads(s[0]) for s in con.execute('SELECT payload FROM question_provenance WHERE question_id=? AND bank_id=?', (r['question_id'], r['bank_id']))]
                item['answers'] = [dict(a) for a in con.execute('SELECT * FROM reference_answers WHERE question_id=? AND bank_id=? ORDER BY version', (r['question_id'], r['bank_id']))]
                result.append(item)
        return result

    def edit_bank_question(self, bank_id, question_id, entry):
        # Editing creates/reuses a content ID, while old session snapshots remain untouched.
        item = next((x for x in self.bank_questions(bank_id) if x['question_id'] == question_id), None)
        if not item:
            raise ValueError('题库条目不存在')
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            new_id, created = self._insert_content(con, entry['question'], entry['topic'] or self._topic_for_user_question(entry['question']))
            if created:
                con.execute('UPDATE interview_questions SET version=? WHERE id=?', (item['version']+1, new_id))
            con.execute('DELETE FROM bank_memberships WHERE bank_id=? AND question_id=?', (bank_id, question_id))
            con.execute('INSERT INTO bank_memberships VALUES(?,?,?) ON CONFLICT(bank_id,question_id) DO UPDATE SET metadata=excluded.metadata',
                        (bank_id, new_id, dump({k: v for k, v in entry.items() if k not in {'question', 'answer', 'origin_ids'}})))
            con.execute('UPDATE question_provenance SET question_id=? WHERE bank_id=? AND question_id=?', (new_id, bank_id, question_id))
            con.execute('UPDATE reference_answers SET question_id=? WHERE bank_id=? AND question_id=?', (new_id, bank_id, question_id))
            if entry.get('answer'):
                version = con.execute('SELECT COALESCE(MAX(version),0)+1 FROM reference_answers WHERE question_id=? AND bank_id=?', (new_id, bank_id)).fetchone()[0]
                con.execute('INSERT INTO reference_answers(id,question_id,bank_id,version,answer,verification_status) VALUES(?,?,?,?,?,?)',
                            (uid('answer'), new_id, bank_id, version, entry['answer'], 'unverified'))
            con.execute('INSERT INTO question_provenance VALUES(?,?,?,?,?,?)',
                        (uid('source'), new_id, bank_id, None, None, dump({**entry, 'original_question': item['question'], 'modification_note': entry.get('modification_note') or '用户编辑题库条目', 'confirmed_at': now_iso()})))
        return {'question_id': new_id, 'updated': True}

    def remove_bank_question(self, bank_id, question_id):
        with self._connect() as con:
            con.execute('DELETE FROM bank_memberships WHERE bank_id=? AND question_id=?', (bank_id, question_id))
        return {'removed': True}

    def practiced_ids(self):
        with self._connect() as con:
            return {r[0] for r in con.execute('SELECT DISTINCT question_id FROM question_practice')}

    def save(self, session):
        # Persist practice atomically with the displayed question / submitted answer.
        session.updated_at = now_iso()
        with self._connect() as con:
            con.execute('INSERT INTO sessions VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,payload=excluded.payload',
                        (session.id, session.created_at, session.updated_at, session.model_dump_json()))
            if session.status == 'ASKING' and session.plan and session.report and session.report.get('pending_kind', 'main') == 'main':
                topic = session.plan.topics[session.current_topic_index]
                if topic.question_id:
                    con.execute('INSERT OR IGNORE INTO question_practice(question_id,session_id,actual_question,displayed_at) VALUES(?,?,?,?)',
                                (topic.question_id, session.id, session.report['pending_question'], now_iso()))
            for turn in session.turns:
                if turn.question_id and turn.question_kind == 'main':
                    con.execute('UPDATE question_practice SET answered_at=COALESCE(answered_at,?) WHERE question_id=? AND session_id=?',
                                (turn.created_at, turn.question_id, session.id))
        return session
