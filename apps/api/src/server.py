from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import config as app_config_module
from config import AppConfig, load_app_config
from modules.epics.jira_provider import JiraEpicProvider
from modules.epics.repository import EpicRepository
from modules.integrations.code_rag_adapter import retrieve_code_evidence
from modules.integrations.llm_adapter import (
    build_retrieval_intent,
    generate_draft,
    generate_software_requirements,
    refine_draft,
    refine_software_requirements,
)
from modules.sessions.store import SessionStore
from modules.shared.models import (
    FileChange,
    OpenQuestion,
    RetrievalIntent,
    SoftwareRequirementsDraft,
    Step,
    WhatToDoDraft,
    to_dict,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WEB_DIR = ROOT / "apps" / "web"

EPICS: EpicRepository | None = None
JIRA: JiraEpicProvider | None = None
SESSIONS: SessionStore | None = None
WEB_DIR = DEFAULT_WEB_DIR
APP_CONFIG: AppConfig | None = None


def log_event(scope: str, message: str) -> None:
    print(f"[AGENTIC-WORKFLOW][{scope}] {message}", flush=True)


def _mask_presence(value: str) -> str:
    return f"set(len={len(value)})" if value else "missing"


def log_config_summary(config: AppConfig) -> None:
    env_file = app_config_module.PROJECT_ROOT / ".env"
    dotenv_status = "available" if app_config_module.load_dotenv is not None else "missing"
    log_event("BOOT", f".env loader status={dotenv_status}")
    log_event("BOOT", f".env file exists={env_file.exists()}")
    log_event("BOOT", f"prompt_dir={config.prompt_dir}")
    log_event("BOOT", f"llm.mode={config.llm_api.mode}")
    log_event("BOOT", f"llm.endpoint={config.llm_api.endpoint or 'missing'}")
    log_event("BOOT", f"llm.api_path={config.llm_api.api_path or 'missing'}")
    log_event("BOOT", f"llm.model={config.llm_api.model or 'missing'}")
    log_event("BOOT", f"llm.auth_url={config.llm_api.auth_url or 'missing'}")
    log_event("BOOT", f"llm.cert_path={config.llm_api.cert_path or 'missing'}")
    log_event("BOOT", f"llm.api_key={_mask_presence(config.llm_api.api_key)}")
    log_event("BOOT", f"llm.client_id={_mask_presence(config.llm_api.client_id)}")
    log_event("BOOT", f"llm.client_secret={_mask_presence(config.llm_api.client_secret)}")
    log_event("BOOT", f"llm.access_token={_mask_presence(config.llm_api.access_token)}")
    log_event("BOOT", f"code_rag.mode={config.code_rag.mode}")
    log_event("BOOT", f"code_rag.device={config.code_rag.device or 'missing'}")
    log_event("BOOT", f"code_rag.embedding_model_path={config.code_rag.embedding_model_path or 'missing'}")
    log_event("BOOT", f"code_rag.vector_store_path={config.code_rag.vector_store_path or 'missing'}")
    log_event("BOOT", f"jira.base_url={config.jira.base_url or 'missing'}")
    log_event("BOOT", f"jira.saved_token={_mask_presence(config.jira.personal_token)}")


def _draft_from_payload(payload: dict, fallback_version: int) -> WhatToDoDraft:
    steps = [
        Step(
            condition=str(item.get("condition", "")),
            actions=[str(action) for action in item.get("actions", [])],
        )
        for item in payload.get("steps", [])
    ]
    files_to_change = [
        FileChange(
            path=str(item.get("path", "")),
            reason=str(item.get("reason", "")),
        )
        for item in payload.get("files_to_change", [])
    ]
    open_questions = [
        OpenQuestion(
            id=str(item.get("id", "")),
            question=str(item.get("question", "")),
            reason=str(item.get("reason", "")),
            status=str(item.get("status", "open")),
            answer=item.get("answer"),
        )
        for item in payload.get("open_questions", [])
    ]
    return WhatToDoDraft(
        version=int(payload.get("version", fallback_version)),
        steps=steps,
        files_to_change=files_to_change,
        open_questions=open_questions,
        raw_text=str(payload.get("raw_text", "")),
        summary=str(payload.get("summary", "")),
    )


def _software_requirements_from_payload(payload: dict, fallback_version: int) -> SoftwareRequirementsDraft:
    raw_text = str(payload.get("raw_text", "")).strip()
    requirements = [str(item).strip() for item in payload.get("requirements", []) if str(item).strip()]
    traceability_summary = [
        str(item).strip() for item in payload.get("traceability_summary", []) if str(item).strip()
    ]
    if raw_text and (not requirements and not traceability_summary):
        requirements, traceability_summary = _parse_software_requirements_raw_text(raw_text)
    if not raw_text:
        raw_text = "\n".join(
            [
                "## Software Requirements",
                "",
                *[f"- {item}" for item in requirements],
                "",
                "## Traceability Summary",
                "",
                *[f"- {item}" for item in traceability_summary],
            ]
        )
    return SoftwareRequirementsDraft(
        version=int(payload.get("version", fallback_version)),
        requirements=requirements,
        traceability_summary=traceability_summary,
        raw_text=raw_text,
        source_iis_version_id=payload.get("source_iis_version_id"),
        source_iis_version_number=payload.get("source_iis_version_number"),
    )


def _parse_software_requirements_raw_text(raw_text: str) -> tuple[list[str], list[str]]:
    requirements: list[str] = []
    traceability_summary: list[str] = []
    current_section = "requirements"

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower_line = line.lower()
        if "traceability summary" in lower_line:
            current_section = "traceability_summary"
            continue
        if "software requirements" in lower_line:
            current_section = "requirements"
            continue

        item = line
        if item.startswith("- "):
            item = item[2:].strip()
        elif item.startswith("* "):
            item = item[2:].strip()
        else:
            item = item.lstrip("0123456789. ").strip()

        if not item:
            continue
        if current_section == "traceability_summary":
            traceability_summary.append(item)
        else:
            requirements.append(item)

    return requirements, traceability_summary


def _sync_manual_draft_if_needed(session, payload: dict) -> None:
    if SESSIONS is None or session.draft is None:
        return
    current_draft_payload = payload.get("currentDraft")
    if not isinstance(current_draft_payload, dict):
        return
    updated_draft = _draft_from_payload(current_draft_payload, session.draft.version)
    if updated_draft.raw_text == session.draft.raw_text:
        return
    updated_draft.version = session.draft.version + 1
    session.draft = updated_draft
    session.draft_history.append(updated_draft)
    draft_version_id = SESSIONS.save_draft_version(
        session,
        updated_draft,
        source_type="manual_edit",
        retrieval_version_id=session.current_retrieval_version_id,
    )
    SESSIONS.save_user_event(
        session.id,
        "manual_edit",
        "user",
        {"draftVersionId": draft_version_id},
    )
    log_event("DRAFT", f"Session {session.id} saved manual editor changes as version_id={draft_version_id}")


def _sync_manual_software_requirements_if_needed(session, payload: dict) -> None:
    if SESSIONS is None or session.software_requirements is None:
        return
    current_software_requirements_payload = payload.get("currentSoftwareRequirements")
    if not isinstance(current_software_requirements_payload, dict):
        return
    updated_software_requirements = _software_requirements_from_payload(
        current_software_requirements_payload,
        session.software_requirements.version,
    )
    if updated_software_requirements.raw_text == session.software_requirements.raw_text:
        return
    updated_software_requirements.version = session.software_requirements.version + 1
    updated_software_requirements.source_iis_version_id = session.software_requirements.source_iis_version_id
    updated_software_requirements.source_iis_version_number = session.software_requirements.source_iis_version_number
    session.software_requirements = updated_software_requirements
    session.software_requirements_history.append(updated_software_requirements)
    version_id = SESSIONS.save_software_requirements_version(
        session,
        updated_software_requirements,
        source_type="manual_edit",
    )
    SESSIONS.save_user_event(
        session.id,
        "manual_edit",
        "user",
        {"softwareRequirementsVersionId": version_id, "artifactType": "software_requirements"},
    )
    log_event("SQ", f"Session {session.id} saved manual software requirements changes as version_id={version_id}")


def _render_software_requirements_markdown(software_requirements: SoftwareRequirementsDraft | None) -> str:
    if software_requirements is None:
        return ""
    lines = ["## Software Requirements", ""]
    lines.extend(f"- {item}" for item in software_requirements.requirements)
    lines.extend(["", "## Traceability Summary", ""])
    lines.extend(f"- {item}" for item in software_requirements.traceability_summary)
    return "\n".join(lines)


def _mark_software_requirements_outdated_if_needed(session) -> None:
    if session.confirmed_iis_version_id is None:
        return
    if session.current_draft_version_id is None:
        return
    session.software_requirements_outdated = session.current_draft_version_id != session.confirmed_iis_version_id


def _serialize_epic_record(record) -> dict:
    return {
        "id": record.source.id,
        "title": record.source.title,
        "description": record.source.description,
        "whatToDo": record.source.what_to_do,
        "parsedWhatToDo": to_dict(record.parsed_what_to_do),
        "metadata": record.source.metadata,
    }


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if EPICS is None or SESSIONS is None:
            self._json({"error": "Epic repository is not configured."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        parsed = urlparse(self.path)
        if JIRA is not None and parsed.path == "/api/jira/credential-status":
            self._json({"hasSavedToken": JIRA.has_saved_token()})
            return

        if JIRA is not None and parsed.path == "/api/jira/projects":
            self._json({"projects": JIRA.list_projects()})
            return

        if JIRA is not None and parsed.path.startswith("/api/jira/projects/") and parsed.path.endswith("/epics"):
            project_key = unquote(parsed.path.split("/")[-2])
            self._json({"epics": JIRA.list_epics(project_key)})
            return

        if JIRA is not None and parsed.path.startswith("/api/jira/epics/"):
            issue_key = unquote(parsed.path.split("/")[-1])
            self._json(_serialize_epic_record(JIRA.get_epic(issue_key)))
            return

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

        if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/versions"):
            session_id = parsed.path.split("/")[-2]
            session = SESSIONS.get(session_id)
            self._json(
                {
                    "sessionId": session.id,
                    "currentDraftVersionId": session.current_draft_version_id,
                    "currentSoftwareRequirementsVersionId": session.current_software_requirements_version_id,
                    "versions": to_dict(SESSIONS.list_draft_versions(session_id)),
                }
            )
            return

        if parsed.path.startswith("/api/sessions/"):
            session_id = parsed.path.split("/")[-1]
            session = SESSIONS.get(session_id)
            self._json(
                {
                    "sessionId": session.id,
                    "status": session.status,
                    "currentPhase": session.current_phase,
                    "currentMessage": session.current_message,
                    "mode": session.mode,
                    "softwareRequirementsOutdated": session.software_requirements_outdated,
                    "confirmedIisVersionId": session.confirmed_iis_version_id,
                    "currentDraftVersionId": session.current_draft_version_id,
                    "currentSoftwareRequirementsVersionId": session.current_software_requirements_version_id,
                    "currentRetrievalVersionId": session.current_retrieval_version_id,
                    "retrievalIntent": to_dict(session.retrieval_intent) if session.retrieval_intent else None,
                    "evidence": to_dict(session.evidence),
                    "draft": to_dict(session.draft) if session.draft else None,
                    "softwareRequirements": to_dict(session.software_requirements) if session.software_requirements else None,
                }
            )
            return

        if parsed.path.startswith("/api/epics/"):
            epic_id = parsed.path.split("/")[-1]
            record = EPICS.get_epic(epic_id)
            self._json(
                _serialize_epic_record(record)
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
        if EPICS is None or SESSIONS is None:
            self._json({"error": "Epic repository is not configured."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        parsed = urlparse(self.path)
        payload = self._read_json()
        log_event("HTTP", f"POST {parsed.path} received")

        try:
            if parsed.path == "/api/jira/connect":
                if JIRA is None:
                    self._json({"error": "Jira provider is not configured."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                token = str(payload.get("token", "")).strip()
                remember_locally = bool(payload.get("rememberLocally", False))
                if not token:
                    self._json({"error": "A Jira personal token is required."}, status=HTTPStatus.BAD_REQUEST)
                    return
                profile = JIRA.connect(token, remember_locally)
                self._json(
                    {
                        "connected": True,
                        "profile": {
                            "displayName": profile.get("displayName", ""),
                            "name": profile.get("name", ""),
                            "emailAddress": profile.get("emailAddress", ""),
                        },
                    }
                )
                return

            if parsed.path == "/api/sessions":
                source_type = str(payload.get("sourceType", "local"))
                epic_payload = payload.get("epic")
                if isinstance(epic_payload, dict):
                    epic_id = epic_payload.get("id", f"{source_type}-epic")
                    log_event("SESSION", f"Creating imported session for epic={epic_id} source={source_type}")
                    from modules.epics.repository import normalize_epic_payload

                    record = normalize_epic_payload(epic_payload)
                elif source_type == "jira":
                    epic_payload = payload.get("epic")
                    self._json({"error": "A Jira Epic payload is required."}, status=HTTPStatus.BAD_REQUEST)
                    return
                else:
                    epic_id = payload["epicId"]
                    log_event("SESSION", f"Creating session for epic={epic_id}")
                    record = EPICS.get_epic(epic_id)
                session = SESSIONS.create(
                    epic_id=record.source.id,
                    title=record.source.title,
                    description=record.source.description,
                    source_type=source_type,
                )
                log_event("SESSION", f"Session created id={session.id}")
                self._json({"sessionId": session.id})
                return

            if parsed.path == "/api/epics/normalize":
                epic_payload = payload.get("epic")
                if not isinstance(epic_payload, dict):
                    self._json({"error": "An Epic payload is required."}, status=HTTPStatus.BAD_REQUEST)
                    return
                from modules.epics.repository import normalize_epic_payload

                self._json(_serialize_epic_record(normalize_epic_payload(epic_payload)))
                return

            if parsed.path.endswith("/generate"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                log_event("GENERATE", f"Session {session_id} started for epic={session.epic_id}")
                if APP_CONFIG is None:
                    self._json({"error": "Application config is not loaded."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return

                SESSIONS.update_runtime_state(
                    session,
                    status="generating",
                    phase="generating_retrieval_intent",
                    message="Generating retrieval intent...",
                )
                log_event("STEP-1", "Building retrieval intent")
                summary, technical_intent, keywords, suspected_areas, query = build_retrieval_intent(
                    session.input_description,
                    APP_CONFIG.llm_api,
                )
                intent = RetrievalIntent(
                    summary=summary,
                    technical_intent=technical_intent,
                    keywords=keywords,
                    suspected_areas=suspected_areas,
                    query=query,
                )
                log_event(
                    "STEP-1",
                    f"Retrieval intent ready. keywords={len(keywords)} suspected_areas={len(suspected_areas)} query_chars={len(query)}",
                )
                session.retrieval_intent = intent

                SESSIONS.update_runtime_state(
                    session,
                    status="generating",
                    phase="retrieving_code_evidence",
                    message="Searching code evidence...",
                )
                log_event("STEP-2", "Retrieving code evidence")
                evidence = retrieve_code_evidence(intent, APP_CONFIG.code_rag)
                log_event("STEP-2", f"Code evidence ready. items={len(evidence)}")
                session.evidence = evidence
                retrieval_version_id = SESSIONS.save_retrieval_snapshot(
                    session,
                    intent,
                    evidence,
                    trigger_source="initial_generate",
                )

                SESSIONS.update_runtime_state(
                    session,
                    status="generating",
                    phase="collecting_references",
                    message="Collecting historical references...",
                )
                log_event("STEP-3", "Collecting historical references")
                reference_records = [
                    record.parsed_what_to_do
                    for record in EPICS.list_epics()
                    if record.source.id != session.epic_id and record.parsed_what_to_do is not None
                ]
                references = [record for record in reference_records if record.steps or record.files_to_change][:3]
                log_event("STEP-3", f"Historical references ready. items={len(references)}")

                SESSIONS.update_runtime_state(
                    session,
                    status="generating",
                    phase="drafting",
                    message="Generating Implementation Intent Specification...",
                )
                log_event("STEP-4", "Generating Implementation Intent Specification")
                draft = generate_draft(
                    session.input_description,
                    evidence,
                    references,
                    APP_CONFIG.llm_api,
                )
                log_event("STEP-4", f"IIS generated. steps={len(draft.steps)} files={len(draft.files_to_change)}")

                session.retrieval_intent = intent
                session.evidence = evidence
                session.reference_samples = references
                session.draft = draft
                session.draft_history.append(draft)
                SESSIONS.save_draft_version(
                    session,
                    draft,
                    source_type="initial_generate",
                    retrieval_version_id=retrieval_version_id,
                )
                SESSIONS.update_session_metadata(
                    session,
                    mode="iis_mode",
                    software_requirements_outdated=False,
                )
                SESSIONS.update_runtime_state(
                    session,
                    status="generated",
                    phase="done",
                    message="Implementation Intent Specification generated.",
                )

                ground_truth = None
                if session.source_type == "local":
                    ground_truth = EPICS.get_epic(session.epic_id).parsed_what_to_do
                log_event("GENERATE", f"Session {session_id} completed successfully")
                self._json(
                    {
                        "sessionId": session.id,
                        "retrievalIntent": to_dict(intent),
                        "evidence": to_dict(evidence),
                        "referenceSamples": to_dict(references),
                        "draft": to_dict(draft),
                        "softwareRequirements": None,
                        "mode": session.mode,
                        "softwareRequirementsOutdated": session.software_requirements_outdated,
                        "confirmedIisVersionId": session.confirmed_iis_version_id,
                        "groundTruth": to_dict(ground_truth),
                    }
                )
                return

            if parsed.path.endswith("/refine"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                log_event("REFINE", f"Session {session_id} refine started")
                if APP_CONFIG is None:
                    self._json({"error": "Application config is not loaded."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                user_message = payload.get("userMessage", "")
                answered = payload.get("answeredQuestions", [])
                SESSIONS.save_user_event(
                    session.id,
                    "refine_request",
                    "user",
                    {"userMessage": user_message, "answeredQuestions": answered},
                )

                if session.mode == "software_requirements_mode":
                    if session.software_requirements is None:
                        self._json({"error": "Software Requirements not generated yet."}, status=HTTPStatus.BAD_REQUEST)
                        return
                    _sync_manual_software_requirements_if_needed(session, payload)
                    SESSIONS.update_runtime_state(
                        session,
                        status="refining",
                        phase="refining_software_requirements",
                        message="Refining Software Requirements...",
                    )
                    software_requirements = refine_software_requirements(
                        session.software_requirements, user_message, answered, APP_CONFIG.llm_api
                    )
                    session.software_requirements = software_requirements
                    session.software_requirements_history.append(software_requirements)
                    SESSIONS.save_software_requirements_version(
                        session,
                        software_requirements,
                        source_type="refine",
                    )
                    SESSIONS.update_runtime_state(
                        session,
                        status="generated",
                        phase="done",
                        message="Software Requirements refined.",
                    )
                    log_event(
                        "REFINE",
                        f"Session {session_id} software requirements refine completed. version={software_requirements.version}",
                    )
                    self._json(
                        {
                            "softwareRequirements": to_dict(software_requirements),
                            "mode": session.mode,
                            "softwareRequirementsOutdated": session.software_requirements_outdated,
                        }
                    )
                    return

                if session.draft is None:
                    self._json({"error": "Implementation Intent Specification not generated yet."}, status=HTTPStatus.BAD_REQUEST)
                    return
                _sync_manual_draft_if_needed(session, payload)
                SESSIONS.update_runtime_state(
                    session,
                    status="refining",
                    phase="refining_iis",
                    message="Refining Implementation Intent Specification...",
                )
                draft = refine_draft(session.draft, user_message, answered, APP_CONFIG.llm_api)
                session.draft = draft
                session.draft_history.append(draft)
                SESSIONS.save_draft_version(
                    session,
                    draft,
                    source_type="refine",
                    retrieval_version_id=session.current_retrieval_version_id,
                )
                _mark_software_requirements_outdated_if_needed(session)
                SESSIONS.update_session_metadata(
                    session,
                    mode="iis_mode",
                    software_requirements_outdated=session.software_requirements_outdated,
                )
                SESSIONS.update_runtime_state(
                    session,
                    status="generated",
                    phase="done",
                    message="Implementation Intent Specification refined.",
                )
                log_event("REFINE", f"Session {session_id} refine completed. version={draft.version}")
                self._json(
                    {
                        "draft": to_dict(draft),
                        "mode": session.mode,
                        "softwareRequirementsOutdated": session.software_requirements_outdated,
                        "diffSummary": [
                            "Appended reviewer guidance as an extra action block.",
                            "Marked answered questions and preserved unresolved ones.",
                        ],
                    }
                )
                return

            if parsed.path.endswith("/rerun-retrieval"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                log_event("RERUN", f"Session {session_id} retrieval rerun started")
                if session.mode == "software_requirements_mode":
                    self._json(
                        {"error": "Reopen the Implementation Intent Specification before rerunning retrieval."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                if APP_CONFIG is None:
                    self._json({"error": "Application config is not loaded."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                _sync_manual_draft_if_needed(session, payload)
                user_message = payload.get("userMessage", "")
                answered = payload.get("answeredQuestions", [])
                answer_lines = [f"- {item.get('id')}: {item.get('answer')}" for item in answered if item.get("answer")]
                additional_context_parts = [session.input_description]
                if user_message.strip():
                    additional_context_parts.extend(["", "Latest user guidance:", user_message.strip()])
                if answer_lines:
                    additional_context_parts.extend(["", "Answered questions:", *answer_lines])
                augmented_description = "\n".join(additional_context_parts)
                SESSIONS.save_user_event(
                    session.id,
                    "rerun_retrieval",
                    "user",
                    {"userMessage": user_message, "answeredQuestions": answered},
                )

                SESSIONS.update_runtime_state(
                    session,
                    status="rerunning_retrieval",
                    phase="generating_retrieval_intent",
                    message="Generating retrieval intent...",
                )
                summary, technical_intent, keywords, suspected_areas, query = build_retrieval_intent(
                    augmented_description,
                    APP_CONFIG.llm_api,
                )
                intent = RetrievalIntent(
                    summary=summary,
                    technical_intent=technical_intent,
                    keywords=keywords,
                    suspected_areas=suspected_areas,
                    query=query,
                )
                session.retrieval_intent = intent

                SESSIONS.update_runtime_state(
                    session,
                    status="rerunning_retrieval",
                    phase="retrieving_code_evidence",
                    message="Searching code evidence...",
                )
                evidence = retrieve_code_evidence(intent, APP_CONFIG.code_rag)
                session.evidence = evidence
                retrieval_version_id = SESSIONS.save_retrieval_snapshot(
                    session,
                    intent,
                    evidence,
                    trigger_source="rerun_retrieval",
                )

                SESSIONS.update_runtime_state(
                    session,
                    status="rerunning_retrieval",
                    phase="drafting",
                    message="Regenerating Implementation Intent Specification...",
                )
                reference_records = [
                    record.parsed_what_to_do
                    for record in EPICS.list_epics()
                    if record.source.id != session.epic_id and record.parsed_what_to_do is not None
                ]
                references = [record for record in reference_records if record.steps or record.files_to_change][:3]
                draft = generate_draft(
                    augmented_description,
                    evidence,
                    references,
                    APP_CONFIG.llm_api,
                )

                session.retrieval_intent = intent
                session.evidence = evidence
                session.reference_samples = references
                session.draft = draft
                session.draft_history.append(draft)
                SESSIONS.save_draft_version(
                    session,
                    draft,
                    source_type="rerun_retrieval",
                    retrieval_version_id=retrieval_version_id,
                )
                _mark_software_requirements_outdated_if_needed(session)
                SESSIONS.update_session_metadata(
                    session,
                    mode="iis_mode",
                    software_requirements_outdated=session.software_requirements_outdated,
                )
                SESSIONS.update_runtime_state(
                    session,
                    status="generated",
                    phase="done",
                    message="Retrieval rerun completed. Implementation Intent Specification updated.",
                )
                self._json(
                    {
                        "retrievalIntent": to_dict(intent),
                        "evidence": to_dict(evidence),
                        "draft": to_dict(draft),
                        "softwareRequirementsOutdated": session.software_requirements_outdated,
                    }
                )
                return

            if "/restore/" in parsed.path:
                parts = parsed.path.split("/")
                session_id = parts[-3]
                version_id = int(parts[-1])
                session = SESSIONS.get(session_id)
                log_event("RESTORE", f"Session {session_id} restoring version id={version_id}")
                SESSIONS.update_runtime_state(
                    session,
                    status="restoring_version",
                    phase="restoring_version",
                    message="Restoring selected version...",
                )
                restored_artifact, new_version_id, artifact_type = SESSIONS.restore_draft_version(session, version_id)
                if artifact_type == "software_requirements":
                    session.software_requirements = restored_artifact
                    SESSIONS.update_session_metadata(
                        session,
                        mode="software_requirements_mode",
                        current_software_requirements_version_id=new_version_id,
                        software_requirements_outdated=(
                            session.confirmed_iis_version_id is not None
                            and restored_artifact.source_iis_version_id != session.confirmed_iis_version_id
                        ),
                    )
                else:
                    session.draft = restored_artifact
                    _mark_software_requirements_outdated_if_needed(session)
                    SESSIONS.update_session_metadata(
                        session,
                        mode="iis_mode",
                        current_draft_version_id=new_version_id,
                        software_requirements_outdated=session.software_requirements_outdated,
                    )
                SESSIONS.save_user_event(
                    session.id,
                    "restore_version",
                    "user",
                    {
                        "restoredFromVersionId": version_id,
                        "newVersionId": new_version_id,
                        "artifactType": artifact_type,
                    },
                )
                SESSIONS.update_runtime_state(
                    session,
                    status="generated",
                    phase="done",
                    message="Version restored.",
                )
                self._json(
                    {
                        "draft": to_dict(session.draft) if session.draft else None,
                        "softwareRequirements": to_dict(session.software_requirements) if session.software_requirements else None,
                        "currentDraftVersionId": session.current_draft_version_id,
                        "currentSoftwareRequirementsVersionId": session.current_software_requirements_version_id,
                        "mode": session.mode,
                        "softwareRequirementsOutdated": session.software_requirements_outdated,
                    }
                )
                return

            if parsed.path.endswith("/confirm"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                _sync_manual_draft_if_needed(session, payload)
                SESSIONS.update_runtime_state(
                    session,
                    status="confirming",
                    phase="confirming",
                    message="Confirming final IIS export...",
                )
                SESSIONS.save_user_event(session.id, "confirm_session", "user", {})
                SESSIONS.update_runtime_state(
                    session,
                    status="confirmed",
                    phase="done",
                    message="Final IIS export confirmed.",
                )
                log_event("CONFIRM", f"Session {session_id} confirmed")
                self._json(
                    {
                        "sessionId": session.id,
                        "finalDraft": to_dict(session.draft),
                        "exportText": session.draft.raw_text if session.draft else "",
                    }
                )
                return

            if parsed.path.endswith("/confirm-iis"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                if session.draft is None or session.retrieval_intent is None:
                    self._json(
                        {"error": "Implementation Intent Specification is not ready yet."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                if APP_CONFIG is None:
                    self._json({"error": "Application config is not loaded."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                _sync_manual_draft_if_needed(session, payload)
                SESSIONS.update_runtime_state(
                    session,
                    status="confirming",
                    phase="confirming_iis",
                    message="Confirming Implementation Intent Specification...",
                )
                confirmed_iis_version_id = session.current_draft_version_id
                if confirmed_iis_version_id is None:
                    self._json(
                        {"error": "Implementation Intent Specification version is not available yet."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                software_requirements_outdated = bool(
                    session.software_requirements
                    and session.software_requirements.source_iis_version_id != confirmed_iis_version_id
                )
                SESSIONS.update_session_metadata(
                    session,
                    mode="software_requirements_mode",
                    confirmed_iis_version_id=confirmed_iis_version_id,
                    software_requirements_outdated=software_requirements_outdated,
                )
                SESSIONS.save_user_event(
                    session.id,
                    "confirm_iis",
                    "user",
                    {
                        "iisVersionId": confirmed_iis_version_id,
                    },
                )
                SESSIONS.update_runtime_state(
                    session,
                    status="generated",
                    phase="done",
                    message="Implementation Intent Specification confirmed.",
                )
                self._json(
                    {
                        "draft": to_dict(session.draft),
                        "softwareRequirements": to_dict(session.software_requirements) if session.software_requirements else None,
                        "mode": session.mode,
                        "confirmedIisVersionId": session.confirmed_iis_version_id,
                        "currentSoftwareRequirementsVersionId": session.current_software_requirements_version_id,
                        "softwareRequirementsOutdated": session.software_requirements_outdated,
                    }
                )
                return

            if parsed.path.endswith("/generate-software-requirements"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                if session.draft is None or session.retrieval_intent is None:
                    self._json(
                        {"error": "Implementation Intent Specification is not ready yet."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                if APP_CONFIG is None:
                    self._json({"error": "Application config is not loaded."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                if session.confirmed_iis_version_id is None or session.current_draft_version_id != session.confirmed_iis_version_id:
                    self._json(
                        {"error": "Confirm the current Implementation Intent Specification before generating software requirements."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                _sync_manual_software_requirements_if_needed(session, payload)
                SESSIONS.update_runtime_state(
                    session,
                    status="confirming",
                    phase="generating_software_requirements",
                    message="Generating Software Requirements...",
                )
                software_requirements = generate_software_requirements(
                    session.input_description,
                    session.draft,
                    APP_CONFIG.llm_api,
                    source_iis_version_id=session.confirmed_iis_version_id,
                    source_iis_version_number=session.draft.version,
                )
                session.software_requirements = software_requirements
                session.software_requirements_history.append(software_requirements)
                software_requirements_version_id = SESSIONS.save_software_requirements_version(
                    session,
                    software_requirements,
                    source_type="generated_from_confirmed_iis",
                )
                SESSIONS.update_session_metadata(
                    session,
                    mode="software_requirements_mode",
                    current_software_requirements_version_id=software_requirements_version_id,
                    software_requirements_outdated=False,
                )
                SESSIONS.save_user_event(
                    session.id,
                    "generate_software_requirements",
                    "user",
                    {
                        "iisVersionId": session.confirmed_iis_version_id,
                        "softwareRequirementsVersionId": software_requirements_version_id,
                    },
                )
                SESSIONS.update_runtime_state(
                    session,
                    status="generated",
                    phase="done",
                    message="Software Requirements generated.",
                )
                self._json(
                    {
                        "draft": to_dict(session.draft),
                        "softwareRequirements": to_dict(software_requirements),
                        "mode": session.mode,
                        "confirmedIisVersionId": session.confirmed_iis_version_id,
                        "currentSoftwareRequirementsVersionId": session.current_software_requirements_version_id,
                        "softwareRequirementsOutdated": session.software_requirements_outdated,
                    }
                )
                return

            if parsed.path.endswith("/reopen-iis"):
                session_id = parsed.path.split("/")[-2]
                session = SESSIONS.get(session_id)
                if session.draft is None:
                    self._json(
                        {"error": "Implementation Intent Specification is not ready yet."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                SESSIONS.save_user_event(session.id, "reopen_iis", "user", {})
                if session.software_requirements is not None:
                    session.software_requirements_outdated = True
                SESSIONS.update_session_metadata(
                    session,
                    mode="iis_mode",
                    software_requirements_outdated=session.software_requirements_outdated,
                )
                SESSIONS.update_runtime_state(
                    session,
                    status="generated",
                    phase="done",
                    message="Implementation Intent Specification reopened for editing.",
                )
                self._json(
                    {
                        "draft": to_dict(session.draft),
                        "softwareRequirements": to_dict(session.software_requirements) if session.software_requirements else None,
                        "mode": session.mode,
                        "softwareRequirementsOutdated": session.software_requirements_outdated,
                    }
                )
                return

            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            if SESSIONS is not None and parsed.path.startswith("/api/sessions/"):
                try:
                    session_id = parsed.path.split("/")[3]
                    session = SESSIONS.get(session_id)
                    SESSIONS.update_runtime_state(
                        session,
                        status="error",
                        phase="error",
                        message=str(exc),
                    )
                except Exception:
                    pass
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
    parser = argparse.ArgumentParser(description="Run the offline Epic implementation workbench.")
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
    global JIRA
    global SESSIONS

    if not data_dir.exists():
        raise FileNotFoundError(f"Epic data directory does not exist: {data_dir}")
    if not data_dir.is_dir():
        raise NotADirectoryError(f"Epic data path is not a directory: {data_dir}")
    if not any(data_dir.glob("*.json")):
        raise FileNotFoundError(f"No Epic JSON files found in: {data_dir}")
    if not DEFAULT_WEB_DIR.exists():
        raise FileNotFoundError(f"Web assets directory does not exist: {DEFAULT_WEB_DIR}")

    APP_CONFIG = load_app_config()
    log_config_summary(APP_CONFIG)
    EPICS = EpicRepository(data_dir)
    JIRA = JiraEpicProvider(APP_CONFIG.jira)
    SESSIONS = SessionStore(APP_CONFIG.storage_db_path)
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
