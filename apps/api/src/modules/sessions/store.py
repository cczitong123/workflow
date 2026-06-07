from __future__ import annotations

import json
import sqlite3
import threading
import base64
import hashlib
import hmac
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from modules.shared.models import (
    DraftVersionRecord,
    EvidenceItem,
    RetrievalIntent,
    RetrievalVersionRecord,
    Session,
    SoftwareRequirementsDraft,
    WhatToDoDraft,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self._sessions: dict[str, Session] = {}
        self._auth_runtime_jira_tokens: dict[str, str] = {}
        self._auth_pin_cache: dict[str, str] = {}
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
                    user_id INTEGER NOT NULL DEFAULT 0,
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
                    source_iis_version_number INTEGER,
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

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_key TEXT NOT NULL UNIQUE,
                    pin_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jira_credentials (
                    user_id INTEGER PRIMARY KEY,
                    token_ciphertext TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column("sessions", "user_id", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("sessions", "mode", "TEXT NOT NULL DEFAULT 'iis_mode'")
            self._ensure_column("sessions", "action_guide_outdated", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("sessions", "confirmed_iis_version_id", "INTEGER")
            self._ensure_column("sessions", "current_action_guide_version_id", "INTEGER")
            self._ensure_column("draft_versions", "artifact_type", "TEXT NOT NULL DEFAULT 'iis'")
            self._ensure_column("draft_versions", "source_iis_version_id", "INTEGER")
            self._ensure_column("draft_versions", "source_iis_version_number", "INTEGER")
            self._connection.execute("DELETE FROM auth_sessions")
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

    def create(self, *, user_id: int, user_key: str, epic_id: str, title: str, description: str, source_type: str = "local") -> Session:
        session_id = f"sess-{secrets.token_hex(4)}"
        now = _utc_now()
        session = Session(
            id=session_id,
            user_id=user_id,
            user_key=user_key,
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
                    id, user_id, source_type, source_epic_id, title, input_description, status,
                    current_phase, current_message, mode, action_guide_outdated, confirmed_iis_version_id,
                    current_retrieval_version_id, current_draft_version_id, current_action_guide_version_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    user_id,
                    source_type,
                    epic_id,
                    title,
                    description,
                    session.status,
                    session.current_phase,
                    session.current_message,
                    session.mode,
                    1 if session.software_requirements_outdated else 0,
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

    def get(self, session_id: str, *, user_id: int | None = None) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            session = self._load_session(session_id)
            self._sessions[session_id] = session
        if user_id is not None and session.user_id != user_id:
            raise KeyError(f"Session {session_id} not found for user {user_id}")
        return session

    def register_workspace(self, user_key: str, pin: str) -> dict[str, object]:
        normalized_user_key = _normalize_user_key(user_key)
        now = _utc_now()
        with self._lock:
            existing = self._connection.execute(
                "SELECT id FROM users WHERE user_key = ?",
                (normalized_user_key,),
            ).fetchone()
            if existing is not None:
                raise ValueError("A workspace already exists for this email or employee ID.")
            pin_hash = _hash_pin(pin)
            cursor = self._connection.execute(
                """
                INSERT INTO users (user_key, pin_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_user_key, pin_hash, now, now),
            )
            user_id = int(cursor.lastrowid)
            auth_session_id = secrets.token_urlsafe(32)
            self._connection.execute(
                """
                INSERT INTO auth_sessions (id, user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (auth_session_id, user_id, now, now),
            )
            self._connection.commit()
        self._auth_pin_cache[auth_session_id] = pin
        return {
            "userId": user_id,
            "userKey": normalized_user_key,
            "authSessionId": auth_session_id,
            "recentSessions": self.list_user_sessions(user_id),
            "hasSavedJiraToken": False,
        }

    def open_workspace(self, user_key: str, pin: str) -> dict[str, object]:
        normalized_user_key = _normalize_user_key(user_key)
        with self._lock:
            row = self._connection.execute(
                "SELECT id, pin_hash FROM users WHERE user_key = ?",
                (normalized_user_key,),
            ).fetchone()
        if row is None or not _verify_pin(pin, row["pin_hash"]):
            raise ValueError("Invalid workspace ID or PIN.")
        user_id = int(row["id"])
        now = _utc_now()
        auth_session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO auth_sessions (id, user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (auth_session_id, user_id, now, now),
            )
            credential_row = self._connection.execute(
                "SELECT token_ciphertext FROM jira_credentials WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            self._connection.commit()
        self._auth_pin_cache[auth_session_id] = pin
        if credential_row is not None:
            self._auth_runtime_jira_tokens[auth_session_id] = _decrypt_token(
                credential_row["token_ciphertext"], normalized_user_key, pin
            )
        return {
            "userId": user_id,
            "userKey": normalized_user_key,
            "authSessionId": auth_session_id,
            "recentSessions": self.list_user_sessions(user_id),
            "hasSavedJiraToken": credential_row is not None,
        }

    def get_user_for_auth_session(self, auth_session_id: str | None) -> dict[str, object] | None:
        if not auth_session_id:
            return None
        with self._lock:
            row = self._connection.execute(
                """
                SELECT auth_sessions.user_id, users.user_key
                FROM auth_sessions
                JOIN users ON users.id = auth_sessions.user_id
                WHERE auth_sessions.id = ?
                """,
                (auth_session_id,),
            ).fetchone()
            if row is not None:
                self._connection.execute(
                    "UPDATE auth_sessions SET updated_at = ? WHERE id = ?",
                    (_utc_now(), auth_session_id),
                )
                self._connection.commit()
        if row is None:
            return None
        return {
            "userId": int(row["user_id"]),
            "userKey": row["user_key"],
            "authSessionId": auth_session_id,
            "hasSavedJiraToken": self.has_saved_jira_token(int(row["user_id"]), auth_session_id=auth_session_id),
        }

    def close_auth_session(self, auth_session_id: str | None) -> None:
        if not auth_session_id:
            return
        with self._lock:
            self._connection.execute("DELETE FROM auth_sessions WHERE id = ?", (auth_session_id,))
            self._connection.commit()
        self._auth_runtime_jira_tokens.pop(auth_session_id, None)
        self._auth_pin_cache.pop(auth_session_id, None)

    def has_saved_jira_token(self, user_id: int, *, auth_session_id: str | None = None) -> bool:
        if auth_session_id and auth_session_id in self._auth_runtime_jira_tokens:
            return True
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM jira_credentials WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row is not None

    def save_jira_token(
        self,
        *,
        user_id: int,
        user_key: str,
        auth_session_id: str,
        token: str,
        remember: bool,
    ) -> None:
        self._auth_runtime_jira_tokens[auth_session_id] = token
        if not remember:
            return
        pin = self._auth_pin_cache.get(auth_session_id)
        if not pin:
            raise RuntimeError("Workspace PIN is not available to save the Jira token.")
        ciphertext = _encrypt_token(token, user_key, pin)
        now = _utc_now()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO jira_credentials (user_id, token_ciphertext, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    token_ciphertext = excluded.token_ciphertext,
                    updated_at = excluded.updated_at
                """,
                (user_id, ciphertext, now, now),
            )
            self._connection.commit()

    def get_jira_token(self, *, user_id: int, auth_session_id: str) -> str | None:
        if auth_session_id in self._auth_runtime_jira_tokens:
            return self._auth_runtime_jira_tokens[auth_session_id]
        return None

    def list_user_sessions(self, user_id: int, limit: int = 12) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, source_epic_id, title, source_type, status, mode, updated_at
                FROM sessions
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [
            {
                "sessionId": row["id"],
                "epicId": row["source_epic_id"],
                "title": row["title"],
                "sourceType": row["source_type"],
                "status": row["status"],
                "mode": row["mode"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

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
        software_requirements_outdated: bool | None = None,
        confirmed_iis_version_id: int | None = None,
        current_retrieval_version_id: int | None = None,
        current_draft_version_id: int | None = None,
        current_software_requirements_version_id: int | None = None,
    ) -> None:
        if mode is not None:
            session.mode = mode
        if software_requirements_outdated is not None:
            session.software_requirements_outdated = software_requirements_outdated
        if confirmed_iis_version_id is not None:
            session.confirmed_iis_version_id = confirmed_iis_version_id
        if current_retrieval_version_id is not None:
            session.current_retrieval_version_id = current_retrieval_version_id
        if current_draft_version_id is not None:
            session.current_draft_version_id = current_draft_version_id
        if current_software_requirements_version_id is not None:
            session.current_software_requirements_version_id = current_software_requirements_version_id

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
                    1 if session.software_requirements_outdated else 0,
                    session.confirmed_iis_version_id,
                    session.current_retrieval_version_id,
                    session.current_draft_version_id,
                    session.current_software_requirements_version_id,
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
        source_iis_version_number: int | None = None,
    ) -> int:
        created_at = _utc_now()
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO draft_versions (
                    session_id, version_number, artifact_type, source_type, retrieval_version_id,
                    source_iis_version_id, source_iis_version_number, draft_json, raw_text, summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    draft.version,
                    artifact_type,
                    source_type,
                    retrieval_version_id,
                    source_iis_version_id,
                    source_iis_version_number,
                    json.dumps(asdict(draft), ensure_ascii=False),
                    draft.raw_text,
                    draft.summary,
                    created_at,
                ),
            )
            draft_version_id = int(cursor.lastrowid)
            if artifact_type == "software_requirements":
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
                            "source_iis_version_number": source_iis_version_number,
                        }
                    ),
                    created_at,
                ),
            )
            self._connection.commit()
        if artifact_type == "software_requirements":
            session.current_software_requirements_version_id = draft_version_id
        else:
            session.current_draft_version_id = draft_version_id
        return draft_version_id

    def save_software_requirements_version(
        self,
        session: Session,
        software_requirements: SoftwareRequirementsDraft,
        *,
        source_type: str,
    ) -> int:
        created_at = _utc_now()
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO draft_versions (
                    session_id, version_number, artifact_type, source_type, retrieval_version_id,
                    source_iis_version_id, source_iis_version_number, draft_json, raw_text, summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    software_requirements.version,
                    "software_requirements",
                    source_type,
                    session.current_retrieval_version_id,
                    software_requirements.source_iis_version_id,
                    software_requirements.source_iis_version_number,
                    json.dumps(asdict(software_requirements), ensure_ascii=False),
                    software_requirements.raw_text,
                    f"Software requirements version {software_requirements.version}",
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
                            "version_number": software_requirements.version,
                            "artifact_type": "software_requirements",
                            "source_iis_version_id": software_requirements.source_iis_version_id,
                            "source_iis_version_number": software_requirements.source_iis_version_number,
                        }
                    ),
                    created_at,
                ),
            )
            self._connection.commit()
        session.current_software_requirements_version_id = version_id
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
                       source_iis_version_id, source_iis_version_number, summary, raw_text, created_at
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
                artifact_type=(
                    "software_requirements"
                    if row["artifact_type"] == "action_guide"
                    else row["artifact_type"]
                ),
                source_type=row["source_type"],
                retrieval_version_id=row["retrieval_version_id"],
                source_iis_version_id=row["source_iis_version_id"],
                source_iis_version_number=row["source_iis_version_number"],
                summary=row["summary"],
                raw_text=row["raw_text"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def restore_draft_version(
        self, session: Session, version_id: int
    ) -> tuple[WhatToDoDraft | SoftwareRequirementsDraft, int, str]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT version_number, artifact_type, retrieval_version_id, source_iis_version_id,
                       source_iis_version_number, draft_json
                FROM draft_versions
                WHERE id = ? AND session_id = ?
                """,
                (version_id, session.id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Draft version {version_id} not found for session {session.id}")

        payload = json.loads(row["draft_json"])
        artifact_type = row["artifact_type"]
        if artifact_type in ("software_requirements", "action_guide"):
            restored_requirements = SoftwareRequirementsDraft(
                version=int(payload.get("version", 1)),
                requirements=[str(item) for item in payload.get("requirements", [])],
                traceability_summary=[str(item) for item in payload.get("traceability_summary", [])],
                raw_text=str(payload.get("raw_text", "")),
                source_iis_version_id=row["source_iis_version_id"],
                source_iis_version_number=row["source_iis_version_number"],
            )
            restored_requirements.version = self._next_draft_version_number(session.id, "software_requirements")
            new_version_id = self.save_software_requirements_version(
                session,
                restored_requirements,
                source_type="restore_version",
            )
            session.software_requirements = restored_requirements
            session.software_requirements_history.append(restored_requirements)
            return restored_requirements, new_version_id, "software_requirements"

        restored_draft = WhatToDoDraft(**payload)
        restored_draft.version = self._next_draft_version_number(session.id, artifact_type)
        new_version_id = self.save_draft_version(
            session,
            restored_draft,
            source_type="restore_version",
            retrieval_version_id=row["retrieval_version_id"],
            artifact_type=artifact_type,
            source_iis_version_id=row["source_iis_version_id"],
            source_iis_version_number=row["source_iis_version_number"],
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
            if artifact_type == "software_requirements":
                row = self._connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) AS max_version
                    FROM draft_versions
                    WHERE session_id = ? AND artifact_type IN ('software_requirements', 'action_guide')
                    """,
                    (session_id,),
                ).fetchone()
            else:
                row = self._connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) AS max_version
                    FROM draft_versions
                    WHERE session_id = ? AND artifact_type = ?
                    """,
                    (session_id, artifact_type),
                ).fetchone()
        return int(row["max_version"]) + 1

    def _load_session(self, session_id: str) -> Session:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, user_id, source_type, source_epic_id, title, input_description, status,
                       current_phase, current_message, mode, action_guide_outdated,
                       confirmed_iis_version_id, current_retrieval_version_id,
                       current_draft_version_id, current_action_guide_version_id
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Session {session_id} not found")
            user_row = self._connection.execute(
                "SELECT user_key FROM users WHERE id = ?",
                (row["user_id"],),
            ).fetchone()
        if user_row is None:
            raise KeyError(f"User for session {session_id} not found")

        session = Session(
            id=row["id"],
            user_id=int(row["user_id"]),
            user_key=user_row["user_key"],
            epic_id=row["source_epic_id"],
            input_title=row["title"],
            input_description=row["input_description"],
            source_type=row["source_type"],
            status=row["status"],
            current_phase=row["current_phase"],
            current_message=row["current_message"],
            mode=row["mode"],
            software_requirements_outdated=bool(row["action_guide_outdated"]),
            confirmed_iis_version_id=row["confirmed_iis_version_id"],
            current_retrieval_version_id=row["current_retrieval_version_id"],
            current_draft_version_id=row["current_draft_version_id"],
            current_software_requirements_version_id=row["current_action_guide_version_id"],
        )
        if session.current_retrieval_version_id is not None:
            session.retrieval_intent, session.evidence = self._load_retrieval_snapshot(session.current_retrieval_version_id)
        if session.current_draft_version_id is not None:
            session.draft = self._load_draft(session.current_draft_version_id)
        if session.current_software_requirements_version_id is not None:
            session.software_requirements = self._load_software_requirements(session.current_software_requirements_version_id)
        return session

    def _load_retrieval_snapshot(self, retrieval_version_id: int) -> tuple[RetrievalIntent, list[EvidenceItem]]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT summary, technical_intent, query, keywords_json, suspected_areas_json
                FROM retrieval_versions
                WHERE id = ?
                """,
                (retrieval_version_id,),
            ).fetchone()
            evidence_rows = self._connection.execute(
                """
                SELECT id, path, chunk_type, symbol, snippet, score, why_relevant, suggested_change, location_hint
                FROM evidence_items
                WHERE retrieval_version_id = ?
                ORDER BY id
                """,
                (retrieval_version_id,),
            ).fetchall()
        if row is None:
            raise KeyError(f"Retrieval version {retrieval_version_id} not found")
        intent = RetrievalIntent(
            summary=row["summary"],
            technical_intent=row["technical_intent"],
            keywords=json.loads(row["keywords_json"]),
            suspected_areas=json.loads(row["suspected_areas_json"]),
            query=row["query"],
        )
        evidence = [
            EvidenceItem(
                id=f"ev-{item['id']}",
                path=item["path"],
                chunk_type=item["chunk_type"],
                symbol=item["symbol"],
                snippet=item["snippet"],
                score=item["score"],
                why_relevant=item["why_relevant"],
                suggested_change=item["suggested_change"],
                location_hint=item["location_hint"],
            )
            for item in evidence_rows
        ]
        return intent, evidence

    def _load_draft(self, version_id: int) -> WhatToDoDraft:
        with self._lock:
            row = self._connection.execute(
                "SELECT draft_json FROM draft_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Draft version {version_id} not found")
        return WhatToDoDraft(**json.loads(row["draft_json"]))

    def _load_software_requirements(self, version_id: int) -> SoftwareRequirementsDraft:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT draft_json, source_iis_version_id, source_iis_version_number
                FROM draft_versions
                WHERE id = ?
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Software Requirements version {version_id} not found")
        payload = json.loads(row["draft_json"])
        return SoftwareRequirementsDraft(
            version=int(payload.get("version", 1)),
            requirements=[str(item) for item in payload.get("requirements", [])],
            traceability_summary=[str(item) for item in payload.get("traceability_summary", [])],
            raw_text=str(payload.get("raw_text", "")),
            source_iis_version_id=row["source_iis_version_id"],
            source_iis_version_number=row["source_iis_version_number"],
        )


def _normalize_user_key(user_key: str) -> str:
    normalized = user_key.strip().lower()
    if not normalized:
        raise ValueError("A work email or employee ID is required.")
    return normalized


def _hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 120_000)
    return f"{base64.b64encode(salt).decode('ascii')}:{base64.b64encode(derived).decode('ascii')}"


def _verify_pin(pin: str, stored_hash: str) -> bool:
    try:
        salt_b64, digest_b64 = stored_hash.split(":", 1)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 120_000)
    return hmac.compare_digest(actual, expected)


def _derive_token_key(user_key: str, pin: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), user_key.encode("utf-8"), 80_000, dklen=32)


def _expand_keystream(seed: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(output[:length])


def _encrypt_token(token: str, user_key: str, pin: str) -> str:
    token_bytes = token.encode("utf-8")
    key = _derive_token_key(user_key, pin)
    keystream = _expand_keystream(key, len(token_bytes))
    ciphertext = bytes(a ^ b for a, b in zip(token_bytes, keystream))
    return base64.b64encode(ciphertext).decode("ascii")


def _decrypt_token(ciphertext: str, user_key: str, pin: str) -> str:
    encrypted = base64.b64decode(ciphertext.encode("ascii"))
    key = _derive_token_key(user_key, pin)
    keystream = _expand_keystream(key, len(encrypted))
    plaintext = bytes(a ^ b for a, b in zip(encrypted, keystream))
    return plaintext.decode("utf-8")
