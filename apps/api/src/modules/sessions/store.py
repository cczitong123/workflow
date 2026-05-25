from __future__ import annotations

import itertools
import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from modules.shared.models import (
    DraftVersionRecord,
    EvidenceItem,
    ImplementationActionGuide,
    RetrievalIntent,
    RetrievalVersionRecord,
    Session,
    WhatToDoDraft,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self._counter = itertools.count(1)
        self._sessions: dict[str, Session] = {}
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    source_epic_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    input_description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_phase TEXT NOT NULL,
                    current_message TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'iis_mode',
                    action_guide_outdated INTEGER NOT NULL DEFAULT 0,
                    confirmed_iis_version_id INTEGER,
                    current_retrieval_version_id INTEGER,
                    current_draft_version_id INTEGER,
                    current_action_guide_version_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS retrieval_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    trigger_source TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    technical_intent TEXT NOT NULL,
                    query TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    suspected_areas_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    retrieval_version_id INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    chunk_type TEXT NOT NULL,
                    symbol TEXT,
                    snippet TEXT NOT NULL,
                    score REAL NOT NULL,
                    why_relevant TEXT NOT NULL,
                    suggested_change TEXT NOT NULL,
                    location_hint TEXT
                );

                CREATE TABLE IF NOT EXISTS draft_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    artifact_type TEXT NOT NULL DEFAULT 'iis',
                    source_type TEXT NOT NULL,
                    retrieval_version_id INTEGER,
                    source_iis_version_id INTEGER,
                    draft_json TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column("sessions", "mode", "TEXT NOT NULL DEFAULT 'iis_mode'")
            self._ensure_column("sessions", "action_guide_outdated", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("sessions", "confirmed_iis_version_id", "INTEGER")
            self._ensure_column("sessions", "current_action_guide_version_id", "INTEGER")
            self._ensure_column("draft_versions", "artifact_type", "TEXT NOT NULL DEFAULT 'iis'")
            self._ensure_column("draft_versions", "source_iis_version_id", "INTEGER")
            self._connection.commit()

    def _ensure_column(self, table_name: str, column_name: str, ddl_suffix: str) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in columns:
            return
        self._connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_suffix}"
        )

    def create(self, epic_id: str, title: str, description: str, source_type: str = "local") -> Session:
        session_id = f"sess-{next(self._counter):03d}"
        now = _utc_now()
        session = Session(
            id=session_id,
            epic_id=epic_id,
            input_title=title,
            input_description=description,
            source_type=source_type,
            status="idle",
            current_phase="idle",
            current_message="Ready to generate.",
        )
        self._sessions[session_id] = session
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO sessions (
                    id, source_type, source_epic_id, title, input_description, status,
                    current_phase, current_message, mode, action_guide_outdated, confirmed_iis_version_id,
                    current_retrieval_version_id, current_draft_version_id, current_action_guide_version_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    source_type,
                    epic_id,
                    title,
                    description,
                    session.status,
                    session.current_phase,
                    session.current_message,
                    session.mode,
                    1 if session.action_guide_outdated else 0,
                    session.confirmed_iis_version_id,
                    None,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO user_events (session_id, event_type, actor, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session.id, "session_created", "system", json.dumps({"epic_id": epic_id, "title": title}), now),
            )
            self._connection.commit()
        return session

    def get(self, session_id: str) -> Session:
        return self._sessions[session_id]

    def update_runtime_state(self, session: Session, *, status: str, phase: str, message: str) -> None:
        session.status = status
        session.current_phase = phase
        session.current_message = message
        now = _utc_now()
        with self._lock:
            self._connection.execute(
                """
                UPDATE sessions
                SET status = ?, current_phase = ?, current_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, phase, message, now, session.id),
            )
            self._connection.commit()

    def update_session_metadata(
        self,
        session: Session,
        *,
        mode: str | None = None,
        action_guide_outdated: bool | None = None,
        confirmed_iis_version_id: int | None = None,
        current_retrieval_version_id: int | None = None,
        current_draft_version_id: int | None = None,
        current_action_guide_version_id: int | None = None,
    ) -> None:
        if mode is not None:
            session.mode = mode
        if action_guide_outdated is not None:
            session.action_guide_outdated = action_guide_outdated
        if confirmed_iis_version_id is not None:
            session.confirmed_iis_version_id = confirmed_iis_version_id
        if current_retrieval_version_id is not None:
            session.current_retrieval_version_id = current_retrieval_version_id
        if current_draft_version_id is not None:
            session.current_draft_version_id = current_draft_version_id
        if current_action_guide_version_id is not None:
            session.current_action_guide_version_id = current_action_guide_version_id

        now = _utc_now()
        with self._lock:
            self._connection.execute(
                """
                UPDATE sessions
                SET mode = ?, action_guide_outdated = ?, confirmed_iis_version_id = ?,
                    current_retrieval_version_id = ?, current_draft_version_id = ?,
                    current_action_guide_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    session.mode,
                    1 if session.action_guide_outdated else 0,
                    session.confirmed_iis_version_id,
                    session.current_retrieval_version_id,
                    session.current_draft_version_id,
                    session.current_action_guide_version_id,
                    now,
                    session.id,
                ),
            )
            self._connection.commit()

    def save_retrieval_snapshot(
        self,
        session: Session,
        intent: RetrievalIntent,
        evidence: list[EvidenceItem],
        *,
        trigger_source: str,
    ) -> int:
        version_number = self._next_retrieval_version_number(session.id)
        created_at = _utc_now()
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO retrieval_versions (
                    session_id, version_number, trigger_source, summary, technical_intent,
                    query, keywords_json, suspected_areas_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    version_number,
                    trigger_source,
                    intent.summary,
                    intent.technical_intent,
                    intent.query,
                    json.dumps(intent.keywords),
                    json.dumps(intent.suspected_areas),
                    created_at,
                ),
            )
            retrieval_version_id = int(cursor.lastrowid)
            for item in evidence:
                self._connection.execute(
                    """
                    INSERT INTO evidence_items (
                        retrieval_version_id, path, chunk_type, symbol, snippet, score,
                        why_relevant, suggested_change, location_hint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        retrieval_version_id,
                        item.path,
                        item.chunk_type,
                        item.symbol,
                        item.snippet,
                        item.score,
                        item.why_relevant,
                        item.suggested_change,
                        item.location_hint,
                    ),
                )
            self._connection.execute(
                """
                UPDATE sessions
                SET current_retrieval_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (retrieval_version_id, created_at, session.id),
            )
            self._connection.execute(
                """
                INSERT INTO user_events (session_id, event_type, actor, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    trigger_source,
                    "system",
                    json.dumps({"retrieval_version_id": retrieval_version_id, "query": intent.query}),
                    created_at,
                ),
            )
            self._connection.commit()
        session.current_retrieval_version_id = retrieval_version_id
        return retrieval_version_id

    def save_draft_version(
        self,
        session: Session,
        draft: WhatToDoDraft,
        *,
        source_type: str,
        retrieval_version_id: int | None,
        artifact_type: str = "iis",
        source_iis_version_id: int | None = None,
    ) -> int:
        created_at = _utc_now()
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO draft_versions (
                    session_id, version_number, artifact_type, source_type, retrieval_version_id,
                    source_iis_version_id, draft_json, raw_text, summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    draft.version,
                    artifact_type,
                    source_type,
                    retrieval_version_id,
                    source_iis_version_id,
                    json.dumps(asdict(draft), ensure_ascii=False),
                    draft.raw_text,
                    draft.summary,
                    created_at,
                ),
            )
            draft_version_id = int(cursor.lastrowid)
            if artifact_type == "action_guide":
                self._connection.execute(
                    """
                    UPDATE sessions
                    SET current_action_guide_version_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (draft_version_id, created_at, session.id),
                )
            else:
                self._connection.execute(
                    """
                    UPDATE sessions
                    SET current_draft_version_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (draft_version_id, created_at, session.id),
                )
            self._connection.execute(
                """
                INSERT INTO user_events (session_id, event_type, actor, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    source_type,
                    "system",
                    json.dumps(
                        {
                            "draft_version_id": draft_version_id,
                            "version_number": draft.version,
                            "artifact_type": artifact_type,
                            "source_iis_version_id": source_iis_version_id,
                        }
                    ),
                    created_at,
                ),
            )
            self._connection.commit()
        if artifact_type == "action_guide":
            session.current_action_guide_version_id = draft_version_id
        else:
            session.current_draft_version_id = draft_version_id
        return draft_version_id

    def save_action_guide_version(
        self,
        session: Session,
        action_guide: ImplementationActionGuide,
        *,
        source_type: str,
    ) -> int:
        created_at = _utc_now()
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO draft_versions (
                    session_id, version_number, artifact_type, source_type, retrieval_version_id,
                    source_iis_version_id, draft_json, raw_text, summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    action_guide.version,
                    "action_guide",
                    source_type,
                    session.current_retrieval_version_id,
                    action_guide.source_iis_version_id,
                    json.dumps(asdict(action_guide), ensure_ascii=False),
                    action_guide.raw_text,
                    f"Action guide version {action_guide.version}",
                    created_at,
                ),
            )
            version_id = int(cursor.lastrowid)
            self._connection.execute(
                """
                UPDATE sessions
                SET current_action_guide_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (version_id, created_at, session.id),
            )
            self._connection.execute(
                """
                INSERT INTO user_events (session_id, event_type, actor, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    source_type,
                    "system",
                    json.dumps(
                        {
                            "draft_version_id": version_id,
                            "version_number": action_guide.version,
                            "artifact_type": "action_guide",
                            "source_iis_version_id": action_guide.source_iis_version_id,
                        }
                    ),
                    created_at,
                ),
            )
            self._connection.commit()
        session.current_action_guide_version_id = version_id
        return version_id

    def save_user_event(self, session_id: str, event_type: str, actor: str, payload: dict) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO user_events (session_id, event_type, actor, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, event_type, actor, json.dumps(payload, ensure_ascii=False), _utc_now()),
            )
            self._connection.commit()

    def list_draft_versions(self, session_id: str) -> list[DraftVersionRecord]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, version_number, artifact_type, source_type, retrieval_version_id,
                       source_iis_version_id, summary, raw_text, created_at
                FROM draft_versions
                WHERE session_id = ?
                ORDER BY id DESC
                """,
                (session_id,),
            ).fetchall()
        return [
            DraftVersionRecord(
                id=row["id"],
                version_number=row["version_number"],
                artifact_type=row["artifact_type"],
                source_type=row["source_type"],
                retrieval_version_id=row["retrieval_version_id"],
                source_iis_version_id=row["source_iis_version_id"],
                summary=row["summary"],
                raw_text=row["raw_text"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def restore_draft_version(
        self, session: Session, version_id: int
    ) -> tuple[WhatToDoDraft | ImplementationActionGuide, int, str]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT version_number, artifact_type, retrieval_version_id, source_iis_version_id, draft_json
                FROM draft_versions
                WHERE id = ? AND session_id = ?
                """,
                (version_id, session.id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Draft version {version_id} not found for session {session.id}")

        payload = json.loads(row["draft_json"])
        artifact_type = row["artifact_type"]
        if artifact_type == "action_guide":
            restored_guide = ImplementationActionGuide(**payload)
            restored_guide.version = self._next_draft_version_number(session.id, artifact_type)
            new_version_id = self.save_action_guide_version(
                session,
                restored_guide,
                source_type="restore_version",
            )
            session.action_guide = restored_guide
            session.action_guide_history.append(restored_guide)
            return restored_guide, new_version_id, artifact_type

        restored_draft = WhatToDoDraft(**payload)
        restored_draft.version = self._next_draft_version_number(session.id, artifact_type)
        new_version_id = self.save_draft_version(
            session,
            restored_draft,
            source_type="restore_version",
            retrieval_version_id=row["retrieval_version_id"],
            artifact_type=artifact_type,
            source_iis_version_id=row["source_iis_version_id"],
        )
        session.draft = restored_draft
        session.draft_history.append(restored_draft)
        return restored_draft, new_version_id, artifact_type

    def _next_retrieval_version_number(self, session_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(version_number), 0) AS max_version FROM retrieval_versions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["max_version"]) + 1

    def _next_draft_version_number(self, session_id: str, artifact_type: str = "iis") -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COALESCE(MAX(version_number), 0) AS max_version
                FROM draft_versions
                WHERE session_id = ? AND artifact_type = ?
                """,
                (session_id, artifact_type),
            ).fetchone()
        return int(row["max_version"]) + 1
