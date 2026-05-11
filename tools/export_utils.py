from __future__ import annotations

"""
Shared SQLite export helpers for local debugging and analysis tools.

This module is intentionally kept separate so that individual export scripts can
stay small and only expose a configuration block at the top of each file.
"""

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "agentic_workflow.sqlite3"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tools" / "exports"


def open_connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_output_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def load_session(connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, source_type, source_epic_id, title, input_description, status,
               current_phase, current_message, current_retrieval_version_id,
               current_draft_version_id, created_at, updated_at
        FROM sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Session '{session_id}' was not found.")
    return dict(row)


def load_retrieval_versions(
    connection: sqlite3.Connection,
    session_id: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, session_id, version_number, trigger_source, summary,
               technical_intent, query, keywords_json, suspected_areas_json,
               created_at
        FROM retrieval_versions
        WHERE session_id = ?
        ORDER BY version_number ASC
        """,
        (session_id,),
    ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["keywords"] = json.loads(payload.pop("keywords_json") or "[]")
        payload["suspected_areas"] = json.loads(payload.pop("suspected_areas_json") or "[]")
        payload["evidence"] = load_evidence_for_retrieval(connection, int(payload["id"]))
        results.append(payload)
    return results


def load_single_retrieval_version(
    connection: sqlite3.Connection,
    retrieval_version_id: int,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, session_id, version_number, trigger_source, summary,
               technical_intent, query, keywords_json, suspected_areas_json,
               created_at
        FROM retrieval_versions
        WHERE id = ?
        """,
        (retrieval_version_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Retrieval version '{retrieval_version_id}' was not found.")

    payload = dict(row)
    payload["keywords"] = json.loads(payload.pop("keywords_json") or "[]")
    payload["suspected_areas"] = json.loads(payload.pop("suspected_areas_json") or "[]")
    payload["evidence"] = load_evidence_for_retrieval(connection, retrieval_version_id)
    return payload


def load_evidence_for_retrieval(
    connection: sqlite3.Connection,
    retrieval_version_id: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, retrieval_version_id, path, chunk_type, symbol, snippet, score,
               why_relevant, suggested_change, location_hint
        FROM evidence_items
        WHERE retrieval_version_id = ?
        ORDER BY score DESC, id ASC
        """,
        (retrieval_version_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def load_draft_versions(
    connection: sqlite3.Connection,
    session_id: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, session_id, version_number, source_type, retrieval_version_id,
               draft_json, raw_text, summary, created_at
        FROM draft_versions
        WHERE session_id = ?
        ORDER BY version_number ASC
        """,
        (session_id,),
    ).fetchall()
    return [_normalize_draft_row(row) for row in rows]


def load_single_draft_version(
    connection: sqlite3.Connection,
    draft_version_id: int,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, session_id, version_number, source_type, retrieval_version_id,
               draft_json, raw_text, summary, created_at
        FROM draft_versions
        WHERE id = ?
        """,
        (draft_version_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Draft version '{draft_version_id}' was not found.")
    return _normalize_draft_row(row)


def load_draft_version_by_number(
    connection: sqlite3.Connection,
    session_id: str,
    version_number: int,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, session_id, version_number, source_type, retrieval_version_id,
               draft_json, raw_text, summary, created_at
        FROM draft_versions
        WHERE session_id = ? AND version_number = ?
        """,
        (session_id, version_number),
    ).fetchone()
    if row is None:
        raise KeyError(
            f"Draft version number '{version_number}' was not found for session '{session_id}'."
        )
    return _normalize_draft_row(row)


def load_user_events(
    connection: sqlite3.Connection,
    session_id: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, session_id, event_type, actor, payload_json, created_at
        FROM user_events
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (session_id,),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["payload"] = json.loads(payload.pop("payload_json") or "{}")
        results.append(payload)
    return results


def write_json(output_path: Path, payload: Any) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def build_trace_pack(
    connection: sqlite3.Connection,
    session_id: str,
) -> dict[str, Any]:
    session = load_session(connection, session_id)
    retrieval_versions = load_retrieval_versions(connection, session_id)
    draft_versions = load_draft_versions(connection, session_id)
    events = load_user_events(connection, session_id)

    retrieval_by_id = {item["id"]: item for item in retrieval_versions}
    timeline: list[dict[str, Any]] = []

    for event in events:
        entry = {
            "event_id": event["id"],
            "event_type": event["event_type"],
            "actor": event["actor"],
            "created_at": event["created_at"],
            "payload": event["payload"],
        }

        retrieval_version_id = event["payload"].get("retrieval_version_id")
        draft_version_id = event["payload"].get("draft_version_id")

        if retrieval_version_id:
            retrieval = retrieval_by_id.get(retrieval_version_id)
            if retrieval is not None:
                entry["retrieval_version"] = {
                    "id": retrieval["id"],
                    "version_number": retrieval["version_number"],
                    "trigger_source": retrieval["trigger_source"],
                    "query": retrieval["query"],
                }

        if draft_version_id:
            linked_draft = next(
                (item for item in draft_versions if item["id"] == draft_version_id),
                None,
            )
            if linked_draft is not None:
                entry["draft_version"] = {
                    "id": linked_draft["id"],
                    "version_number": linked_draft["version_number"],
                    "source_type": linked_draft["source_type"],
                    "retrieval_version_id": linked_draft["retrieval_version_id"],
                }

        timeline.append(entry)

    current_draft = next(
        (item for item in draft_versions if item["id"] == session["current_draft_version_id"]),
        None,
    )
    current_retrieval = retrieval_by_id.get(session["current_retrieval_version_id"])

    return {
        "session": session,
        "input": {
            "description": session["input_description"],
            "source_type": session["source_type"],
            "source_epic_id": session["source_epic_id"],
            "title": session["title"],
        },
        "retrieval_versions": retrieval_versions,
        "draft_versions": draft_versions,
        "events": events,
        "timeline": timeline,
        "final_state": {
            "current_draft_version": current_draft,
            "current_retrieval_version": current_retrieval,
        },
    }


def build_draft_diff(
    earlier: dict[str, Any],
    later: dict[str, Any],
) -> dict[str, Any]:
    earlier_draft = earlier["draft"]
    later_draft = later["draft"]

    earlier_files = {item["path"] for item in earlier_draft.get("files_to_change", [])}
    later_files = {item["path"] for item in later_draft.get("files_to_change", [])}

    earlier_questions = {
        item["id"]: item for item in earlier_draft.get("open_questions", [])
    }
    later_questions = {
        item["id"]: item for item in later_draft.get("open_questions", [])
    }

    status_changes = []
    for question_id, later_question in later_questions.items():
        earlier_question = earlier_questions.get(question_id)
        if earlier_question and earlier_question.get("status") != later_question.get("status"):
            status_changes.append(
                {
                    "question_id": question_id,
                    "from": earlier_question.get("status"),
                    "to": later_question.get("status"),
                }
            )

    return {
        "from_version": {
            "id": earlier["id"],
            "version_number": earlier["version_number"],
            "source_type": earlier["source_type"],
            "retrieval_version_id": earlier["retrieval_version_id"],
        },
        "to_version": {
            "id": later["id"],
            "version_number": later["version_number"],
            "source_type": later["source_type"],
            "retrieval_version_id": later["retrieval_version_id"],
        },
        "draft_changed": earlier["raw_text"] != later["raw_text"],
        "retrieval_changed": earlier["retrieval_version_id"] != later["retrieval_version_id"],
        "changes": {
            "summary_changed": earlier["summary"] != later["summary"],
            "raw_text_changed": earlier["raw_text"] != later["raw_text"],
            "files_added": sorted(later_files - earlier_files),
            "files_removed": sorted(earlier_files - later_files),
            "step_count_delta": len(later_draft.get("steps", [])) - len(earlier_draft.get("steps", [])),
            "open_question_status_changed": status_changes,
        },
    }


def _normalize_draft_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["draft"] = json.loads(payload.pop("draft_json") or "{}")
    return payload


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value
