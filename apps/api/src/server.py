from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config import AppConfig, load_app_config
from modules.epics.repository import EpicRepository
from modules.integrations.code_rag_adapter import retrieve_code_evidence
from modules.integrations.llm_adapter import (
    build_retrieval_intent,
    generate_draft,
    refine_draft,
)
from modules.sessions.store import SessionStore
from modules.shared.models import RetrievalIntent, to_dict


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WEB_DIR = ROOT / "apps" / "web"

EPICS: EpicRepository | None = None
SESSIONS = SessionStore()
WEB_DIR = DEFAULT_WEB_DIR
APP_CONFIG: AppConfig | None = None


def log_event(scope: str, message: str) -> None:
    print(f"[AGENTIC-WORKFLOW][{scope}] {message}", flush=True)


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if EPICS is None:
            self._json({"error": "Epic repository is not configured."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/epics":
            self._json(
                [
                    {
                        "id": record.source.id,
                        "title": record.source.title,
                        "hasHistoricalWhatToDo": bool(record.source.what_to_do.strip()),
                    }
                    for record in EPICS.list_epics()
                ]
            )
            return

        if parsed.path.startswith("/api/epics/"):
            epic_id = parsed.path.split("/")[-1]
            record = EPICS.get_epic(epic_id)
            self._json(
                {
                    "id": record.source.id,
                    "title": record.source.title,
                    "description": record.source.description,
                    "whatToDo": record.source.what_to_do,
                    "parsedWhatToDo": to_dict(record.parsed_what_to_do),
                }
            )
            return

        if parsed.path == "/" or parsed.path == "/index.html":
            self._serve_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return

        if parsed.path == "/app.js":
            self._serve_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            return

        if parsed.path == "/styles.css":
            self._serve_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if EPICS is None:
            self._json({"error": "Epic repository is not configured."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        parsed = urlparse(self.path)
        payload = self._read_json()
        log_event("HTTP", f"POST {parsed.path} received")

        try:
            if parsed.path == "/api/sessions":
                epic_id = payload["epicId"]
                log_event("SESSION", f"Creating session for epic={epic_id}")
                record = EPICS.get_epic(epic_id)
                session = SESSIONS.create(
                    epic_id=record.source.id,
                    title=record.source.title,
                    description=record.source.description,
                )
                log_event("SESSION", f"Session created id={session.id}")
                self._json({"sessionId": session.id})
                return

            if parsed.path.endswith("/generate"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                log_event("GENERATE", f"Session {session_id} started for epic={session.epic_id}")
                if APP_CONFIG is None:
                    self._json({"error": "Application config is not loaded."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return

                log_event("STEP-1", "Building retrieval intent")
                summary, technical_intent, keywords, suspected_areas = build_retrieval_intent(
                    session.input_description,
                    APP_CONFIG.llm_api,
                )
                intent = RetrievalIntent(
                    summary=summary,
                    technical_intent=technical_intent,
                    keywords=keywords,
                    suspected_areas=suspected_areas,
                )
                log_event(
                    "STEP-1",
                    f"Retrieval intent ready. keywords={len(keywords)} suspected_areas={len(suspected_areas)}",
                )

                log_event("STEP-2", "Retrieving code evidence")
                evidence = retrieve_code_evidence(intent, APP_CONFIG.code_rag)
                log_event("STEP-2", f"Code evidence ready. items={len(evidence)}")

                log_event("STEP-3", "Collecting historical references")
                reference_records = [
                    record.parsed_what_to_do
                    for record in EPICS.list_epics()
                    if record.source.id != session.epic_id and record.parsed_what_to_do is not None
                ]
                references = [record for record in reference_records if record.steps or record.files_to_change][:3]
                log_event("STEP-3", f"Historical references ready. items={len(references)}")

                log_event("STEP-4", "Generating draft")
                draft = generate_draft(
                    session.input_description,
                    evidence,
                    references,
                    APP_CONFIG.llm_api,
                )
                log_event("STEP-4", f"Draft generated. steps={len(draft.steps)} files={len(draft.files_to_change)}")

                session.retrieval_intent = intent
                session.evidence = evidence
                session.reference_samples = references
                session.draft = draft
                session.draft_history.append(draft)
                session.status = "generated"

                ground_truth = EPICS.get_epic(session.epic_id).parsed_what_to_do
                log_event("GENERATE", f"Session {session_id} completed successfully")
                self._json(
                    {
                        "sessionId": session.id,
                        "retrievalIntent": to_dict(intent),
                        "evidence": to_dict(evidence),
                        "referenceSamples": to_dict(references),
                        "draft": to_dict(draft),
                        "groundTruth": to_dict(ground_truth),
                    }
                )
                return

            if parsed.path.endswith("/refine"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                log_event("REFINE", f"Session {session_id} refine started")
                if session.draft is None:
                    self._json({"error": "Draft not generated yet."}, status=HTTPStatus.BAD_REQUEST)
                    return
                if APP_CONFIG is None:
                    self._json({"error": "Application config is not loaded."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                user_message = payload.get("userMessage", "")
                answered = payload.get("answeredQuestions", [])
                draft = refine_draft(session.draft, user_message, answered, APP_CONFIG.llm_api)
                session.draft = draft
                session.draft_history.append(draft)
                session.status = "refining"
                log_event("REFINE", f"Session {session_id} refine completed. version={draft.version}")
                self._json(
                    {
                        "draft": to_dict(draft),
                        "diffSummary": [
                            "Appended reviewer guidance as an extra action block.",
                            "Marked answered questions and preserved unresolved ones.",
                        ],
                    }
                )
                return

            if parsed.path.endswith("/confirm"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                session.status = "confirmed"
                log_event("CONFIRM", f"Session {session_id} confirmed")
                self._json(
                    {
                        "sessionId": session.id,
                        "finalDraft": to_dict(session.draft),
                        "exportText": session.draft.raw_text if session.draft else "",
                    }
                )
                return

            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            log_event("ERROR", f"{parsed.path} failed with {type(exc).__name__}: {exc}")
            self._json(
                {
                    "error": str(exc),
                    "type": type(exc).__name__,
                    "path": parsed.path,
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else b"{}"
        return json.loads(body.decode("utf-8"))

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def resolve_runtime_settings() -> tuple[str, int, Path]:
    parser = argparse.ArgumentParser(description="Run the offline Epic What-to-Do workbench.")
    defaults = load_app_config()
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument(
        "--data-dir",
        default=str(defaults.epic_data.data_dir),
        help="Directory containing Epic JSON files.",
    )
    args = parser.parse_args()
    return args.host, args.port, Path(args.data_dir).expanduser().resolve()


def configure_runtime(data_dir: Path) -> None:
    global EPICS
    global WEB_DIR
    global APP_CONFIG

    if not data_dir.exists():
        raise FileNotFoundError(f"Epic data directory does not exist: {data_dir}")
    if not data_dir.is_dir():
        raise NotADirectoryError(f"Epic data path is not a directory: {data_dir}")
    if not any(data_dir.glob("*.json")):
        raise FileNotFoundError(f"No Epic JSON files found in: {data_dir}")
    if not DEFAULT_WEB_DIR.exists():
        raise FileNotFoundError(f"Web assets directory does not exist: {DEFAULT_WEB_DIR}")

    APP_CONFIG = load_app_config()
    EPICS = EpicRepository(data_dir)
    WEB_DIR = DEFAULT_WEB_DIR


def run() -> None:
    host, port, data_dir = resolve_runtime_settings()
    configure_runtime(data_dir)
    server = ThreadingHTTPServer((host, port), AppHandler)
    log_event("BOOT", "Server code version: prompt-render-logging-v2")
    log_event("BOOT", f"Serving offline MVP at http://{host}:{port}")
    log_event("BOOT", f"Using Epic data directory: {data_dir}")
    server.serve_forever()


if __name__ == "__main__":
    run()
