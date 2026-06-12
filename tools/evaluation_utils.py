from __future__ import annotations

"""
Shared evaluation helpers for generation quality experiments.

This module keeps the actual evaluation scripts small and makes it easy to
reuse the same LLM-as-judge and aggregation logic across multiple evaluation
experiments.
"""

import json
import os
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_SRC = PROJECT_ROOT / "apps" / "api" / "src"

if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

from config import AppConfig, load_app_config  # noqa: E402
from modules.integrations.code_rag_adapter import retrieve_code_evidence  # noqa: E402
from modules.integrations.llm_adapter import (  # noqa: E402
    _call_remote_chat,
    build_retrieval_intent,
    generate_draft,
    generate_software_requirements,
)
from modules.shared.models import (  # noqa: E402
    EvidenceItem,
    FileChange,
    OpenQuestion,
    RetrievalIntent,
    SoftwareRequirementsDraft,
    Step,
    WhatToDoDraft,
)
from prompt_loader import render_prompt  # noqa: E402


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_markdown(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if "\n" in stripped:
            results = []
            for line in stripped.splitlines():
                normalized = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
                if normalized:
                    results.append(normalized)
            return results
        return [stripped]
    if isinstance(value, list):
        results = []
        for item in value:
            if isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if text:
                    results.append(text)
            else:
                text = str(item).strip()
                if text:
                    results.append(text)
        return results
    return [str(value).strip()] if str(value).strip() else []


def get_case_id(case: dict[str, Any]) -> str:
    for key in ("case_id", "id", "epic_id", "issue_key", "epic_key", "key"):
        value = str(case.get(key, "")).strip()
        if value:
            return value
    return ""


def get_case_description(case: dict[str, Any]) -> str:
    return str(case.get("description", "")).strip()


def get_case_historical_what_to_do(case: dict[str, Any]) -> str:
    for key in ("historical_what_to_do", "what_to_do"):
        value = str(case.get(key, "")).strip()
        if value:
            return value
    return ""


def get_case_historical_changed_files(case: dict[str, Any]) -> list[str]:
    for key in ("historical_changed_files", "files"):
        if key not in case:
            continue
        parsed = normalize_string_list(case.get(key))
        if parsed:
            return parsed
    return []


def get_case_historical_software_requirements(case: dict[str, Any]) -> list[str]:
    raw = case.get("historical_software_requirements")
    if raw:
        parsed = normalize_string_list(raw)
        if parsed:
            return parsed

    raw_srs = case.get("SRs")
    if isinstance(raw_srs, list):
        return normalize_string_list(raw_srs)
    if not isinstance(raw_srs, str):
        return []

    text = raw_srs.strip()
    if not text:
        return []

    matches = list(re.finditer(r"(?m)^\s*(\d+)[.)]\s*", text))
    if not matches:
        return normalize_string_list(text)

    items: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        item = " ".join(text[start:end].split()).strip()
        if item:
            items.append(item)
    return items


def get_case_task_type(case: dict[str, Any]) -> str:
    for key in ("task_type", "TaskType", "taskType", "type"):
        value = str(case.get(key, "")).strip()
        if value:
            return value
    return ""


def get_case_difficulty(case: dict[str, Any]) -> str:
    for key in ("difficulty", "Difficulty", "Tag", "tag"):
        value = str(case.get(key, "")).strip()
        if value:
            return value
    return ""


def format_list_block(items: list[str], empty_label: str = "None provided.") -> str:
    if not items:
        return empty_label
    return "\n".join(f"- {item}" for item in items)


def format_evidence_block(items: list[dict[str, Any]] | None, empty_label: str = "None provided.") -> str:
    if not items:
        return empty_label
    lines: list[str] = []
    for item in items:
        path = str(item.get("path", "")).strip() or "unknown-path"
        symbol = str(item.get("symbol", "")).strip()
        why_relevant = str(item.get("why_relevant", "")).strip()
        suggested_change = str(item.get("suggested_change", "")).strip()
        score = item.get("score")

        detail_parts = []
        if symbol:
            detail_parts.append(f"symbol={symbol}")
        if isinstance(score, (int, float)):
            detail_parts.append(f"score={score:.3f}")
        if why_relevant:
            detail_parts.append(f"why={why_relevant}")
        if suggested_change:
            detail_parts.append(f"suggested_change={suggested_change}")
        if detail_parts:
            lines.append(f"- {path} | " + " | ".join(detail_parts))
        else:
            lines.append(f"- {path}")
    return "\n".join(lines)


def normalize_path_for_eval(path: str) -> str:
    return str(path).strip().replace("\\", "/").lower()


def paths_match_for_eval(left: str, right: str) -> bool:
    left_norm = normalize_path_for_eval(left)
    right_norm = normalize_path_for_eval(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    return left_norm.endswith("/" + right_norm) or right_norm.endswith("/" + left_norm)


def build_eval_path_match_map(
    historical_paths: list[str],
    retrieved_paths: list[str],
) -> dict[str, str]:
    matches: dict[str, str] = {}
    for historical_path in historical_paths:
        historical_norm = normalize_path_for_eval(historical_path)
        if not historical_norm:
            continue
        for retrieved_path in retrieved_paths:
            if paths_match_for_eval(historical_path, retrieved_path):
                matches[historical_norm] = normalize_path_for_eval(retrieved_path)
                break
    return matches


def render_software_requirements_text(software_requirements: dict[str, Any]) -> str:
    reqs = software_requirements.get("requirements") or software_requirements.get("software_requirements") or []
    trace = software_requirements.get("traceability_summary") or []

    requirement_lines: list[str] = []
    for item in reqs:
        if isinstance(item, dict):
            req_id = str(item.get("id", "")).strip()
            text = str(item.get("text", "")).strip()
            if text:
                requirement_lines.append(f"{req_id}: {text}" if req_id else text)
        else:
            text = str(item).strip()
            if text:
                requirement_lines.append(text)

    trace_lines: list[str] = []
    for item in trace:
        if isinstance(item, dict):
            req_id = str(item.get("requirement_id", "")).strip()
            maps_to = [str(entry).strip() for entry in item.get("maps_to", []) if str(entry).strip()]
            if maps_to:
                trace_lines.append(f"{req_id}: {'; '.join(maps_to)}" if req_id else "; ".join(maps_to))
        else:
            text = str(item).strip()
            if text:
                trace_lines.append(text)

    sections = ["## Software Requirements", ""]
    sections.extend(f"- {item}" for item in requirement_lines)
    sections.extend(["", "## Traceability Summary", ""])
    sections.extend(f"- {item}" for item in trace_lines)
    return "\n".join(sections)


def build_retrieval_config_snapshot(app_config: AppConfig) -> dict[str, Any]:
    return {
        "ranking_mode": app_config.code_rag.ranking_mode,
        "ranking_alpha": app_config.code_rag.ranking_alpha,
        "ranking_beta": app_config.code_rag.ranking_beta,
        "file_aggregation_strategy": app_config.code_rag.file_aggregation_strategy,
        "file_aggregation_alpha": app_config.code_rag.file_aggregation_alpha,
        "file_aggregation_beta": app_config.code_rag.file_aggregation_beta,
        "file_aggregation_candidate_multiplier": app_config.code_rag.file_aggregation_candidate_multiplier,
    }


def serialize_retrieval_intent(intent: RetrievalIntent) -> dict[str, Any]:
    return {
        "summary": intent.summary,
        "technical_intent": intent.technical_intent,
        "keywords": intent.keywords,
        "suspected_areas": intent.suspected_areas,
        "query": intent.query,
    }


def deserialize_retrieval_intent(payload: dict[str, Any]) -> RetrievalIntent:
    return RetrievalIntent(
        summary=str(payload.get("summary", "")),
        technical_intent=str(payload.get("technical_intent", "")),
        keywords=[str(item) for item in payload.get("keywords", [])],
        suspected_areas=[str(item) for item in payload.get("suspected_areas", [])],
        query=str(payload.get("query", "")),
    )


def serialize_evidence_items(items: list[EvidenceItem]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "path": item.path,
            "chunk_type": item.chunk_type,
            "symbol": item.symbol,
            "snippet": item.snippet,
            "score": item.score,
            "why_relevant": item.why_relevant,
            "suggested_change": item.suggested_change,
            "location_hint": item.location_hint,
        }
        for item in items
    ]


def deserialize_evidence_items(items: list[dict[str, Any]] | None) -> list[EvidenceItem]:
    if not items:
        return []
    return [
        EvidenceItem(
            id=str(item.get("id", "")),
            path=str(item.get("path", "")),
            chunk_type=str(item.get("chunk_type", "")),
            symbol=str(item.get("symbol")) if item.get("symbol") is not None else None,
            snippet=str(item.get("snippet", "")),
            score=float(item.get("score", 0.0)),
            why_relevant=str(item.get("why_relevant", "")),
            suggested_change=str(item.get("suggested_change", "")),
            location_hint=(
                str(item.get("location_hint"))
                if item.get("location_hint") is not None
                else None
            ),
        )
        for item in items
    ]


def serialize_iis_draft(draft: WhatToDoDraft) -> dict[str, Any]:
    return {
        "version": draft.version,
        "steps": [
            {
                "condition": step.condition,
                "actions": step.actions,
            }
            for step in draft.steps
        ],
        "files_to_change": [
            {
                "path": item.path,
                "reason": item.reason,
            }
            for item in draft.files_to_change
        ],
        "open_questions": [
            {
                "id": item.id,
                "question": item.question,
                "reason": item.reason,
                "status": item.status,
                "answer": item.answer,
            }
            for item in draft.open_questions
        ],
        "raw_text": draft.raw_text,
        "summary": draft.summary,
    }


def deserialize_iis_draft(payload: dict[str, Any]) -> WhatToDoDraft:
    return WhatToDoDraft(
        version=int(payload.get("version", 1)),
        steps=[
            Step(
                condition=str(item.get("condition", "")),
                actions=[str(action) for action in item.get("actions", [])],
            )
            for item in payload.get("steps", [])
        ],
        files_to_change=[
            FileChange(
                path=str(item.get("path", "")),
                reason=str(item.get("reason", "")),
            )
            for item in payload.get("files_to_change", [])
        ],
        open_questions=[
            OpenQuestion(
                id=str(item.get("id", "")),
                question=str(item.get("question", "")),
                reason=str(item.get("reason", "")),
                status=str(item.get("status", "open")),
                answer=(
                    str(item.get("answer")) if item.get("answer") is not None else None
                ),
            )
            for item in payload.get("open_questions", [])
        ],
        raw_text=str(payload.get("raw_text", "")),
        summary=str(payload.get("summary", "")),
    )


def serialize_software_requirements_draft(
    software_requirements: SoftwareRequirementsDraft,
) -> dict[str, Any]:
    return {
        "version": software_requirements.version,
        "requirements": software_requirements.requirements,
        "traceability_summary": software_requirements.traceability_summary,
        "raw_text": software_requirements.raw_text,
        "source_iis_version_id": software_requirements.source_iis_version_id,
        "source_iis_version_number": software_requirements.source_iis_version_number,
    }


def render_historical_software_requirements_text(
    historical_software_requirements: Any,
) -> str:
    if not historical_software_requirements:
        return ""
    return "\n".join(
        [
            "## Software Requirements",
            "",
            *[
                f"- {str(item.get('id', '')).strip()}: {str(item.get('text', '')).strip()}".strip(": ")
                if isinstance(item, dict)
                else f"- {str(item).strip()}"
                for item in historical_software_requirements
                if (
                    isinstance(item, dict)
                    and (
                        str(item.get("id", "")).strip()
                        or str(item.get("text", "")).strip()
                    )
                )
                or (not isinstance(item, dict) and str(item).strip())
            ],
            "",
            "## Traceability Summary",
            "",
        ]
    ).strip()


def generate_retrieval_intent_payload(description: str, app_config: AppConfig) -> dict[str, Any]:
    summary, technical_intent, keywords, suspected_areas, query = build_retrieval_intent(
        description,
        app_config.llm_api,
    )
    return serialize_retrieval_intent(
        RetrievalIntent(
            summary=summary,
            technical_intent=technical_intent,
            keywords=keywords,
            suspected_areas=suspected_areas,
            query=query,
        )
    )


def retrieve_evidence_payload(
    retrieval_intent_payload: dict[str, Any],
    app_config: AppConfig,
) -> list[dict[str, Any]]:
    retrieval_intent_model = deserialize_retrieval_intent(retrieval_intent_payload)
    evidence_items = retrieve_code_evidence(retrieval_intent_model, app_config.code_rag)
    return serialize_evidence_items(evidence_items)


def generate_iis_payload(
    description: str,
    evidence_payload: list[dict[str, Any]],
    app_config: AppConfig,
) -> dict[str, Any]:
    evidence_items = deserialize_evidence_items(evidence_payload)
    draft = generate_draft(description, evidence_items, [], app_config.llm_api)
    return serialize_iis_draft(draft)


def generate_software_requirements_payload(
    description: str,
    iis_payload: dict[str, Any],
    app_config: AppConfig,
) -> dict[str, Any]:
    iis = deserialize_iis_draft(iis_payload)
    software_requirements = generate_software_requirements(
        description,
        iis,
        app_config.llm_api,
        source_iis_version_id=None,
        source_iis_version_number=iis.version,
    )
    return serialize_software_requirements_draft(software_requirements)


def run_generation_pipeline(
    description: str,
    app_config: AppConfig,
) -> dict[str, Any]:
    retrieval_intent = generate_retrieval_intent_payload(description, app_config)
    evidence = retrieve_evidence_payload(retrieval_intent, app_config)
    iis = generate_iis_payload(description, evidence, app_config)
    software_requirements = generate_software_requirements_payload(
        description,
        iis,
        app_config,
    )
    return {
        "retrieval_config": build_retrieval_config_snapshot(app_config),
        "retrieval_intent": retrieval_intent,
        "evidence": evidence,
        "iis": iis,
        "software_requirements": software_requirements,
    }


def apply_retrieval_strategy_override(
    app_config: AppConfig,
    *,
    ranking_mode: str | None = None,
    ranking_alpha: float | None = None,
    ranking_beta: float | None = None,
    file_aggregation_strategy: str | None = None,
    file_aggregation_alpha: float | None = None,
    file_aggregation_beta: float | None = None,
    candidate_multiplier: int | None = None,
) -> AppConfig:
    code_rag = replace(
        app_config.code_rag,
        ranking_mode=ranking_mode if ranking_mode is not None else app_config.code_rag.ranking_mode,
        ranking_alpha=ranking_alpha if ranking_alpha is not None else app_config.code_rag.ranking_alpha,
        ranking_beta=ranking_beta if ranking_beta is not None else app_config.code_rag.ranking_beta,
        file_aggregation_strategy=(
            file_aggregation_strategy
            if file_aggregation_strategy is not None
            else app_config.code_rag.file_aggregation_strategy
        ),
        file_aggregation_alpha=(
            file_aggregation_alpha
            if file_aggregation_alpha is not None
            else app_config.code_rag.file_aggregation_alpha
        ),
        file_aggregation_beta=(
            file_aggregation_beta
            if file_aggregation_beta is not None
            else app_config.code_rag.file_aggregation_beta
        ),
        file_aggregation_candidate_multiplier=(
            candidate_multiplier
            if candidate_multiplier is not None
            else app_config.code_rag.file_aggregation_candidate_multiplier
        ),
    )
    return replace(app_config, code_rag=code_rag)


def build_eval_judge_config(app_config: AppConfig):
    base = app_config.llm_api

    def override(name: str, current: str) -> str:
        return os.getenv(f"AGENTIC_WORKFLOW_EVAL_JUDGE_{name}", current)

    def override_int(name: str, current: int) -> int:
        value = os.getenv(f"AGENTIC_WORKFLOW_EVAL_JUDGE_{name}")
        return int(value) if value is not None and value.strip() else current

    def override_float(name: str, current: float) -> float:
        value = os.getenv(f"AGENTIC_WORKFLOW_EVAL_JUDGE_{name}")
        return float(value) if value is not None and value.strip() else current

    return replace(
        base,
        mode=override("MODE", base.mode),
        endpoint=override("ENDPOINT", base.endpoint),
        api_path=override("API_PATH", base.api_path),
        model=override("MODEL", base.model),
        api_key=override("API_KEY", base.api_key),
        access_token=override("ACCESS_TOKEN", base.access_token),
        cert_path=override("CERT_PATH", base.cert_path),
        auth_url=override("AUTH_URL", base.auth_url),
        client_id=override("CLIENT_ID", base.client_id),
        client_secret=override("CLIENT_SECRET", base.client_secret),
        timeout_seconds=override_int("TIMEOUT_SECONDS", base.timeout_seconds),
        max_retries=override_int("MAX_RETRIES", base.max_retries),
        retry_backoff_seconds=override_float(
            "RETRY_BACKOFF_SECONDS",
            base.retry_backoff_seconds,
        ),
        include_tuning_params=override("INCLUDE_TUNING_PARAMS", str(base.include_tuning_params)).lower() == "true",
        temperature=override_float("TEMPERATURE", base.temperature),
        max_tokens=override_int("MAX_TOKENS", base.max_tokens),
        top_p=override_float("TOP_P", base.top_p),
        presence_penalty=override_float("PRESENCE_PENALTY", base.presence_penalty),
        frequency_penalty=override_float("FREQUENCY_PENALTY", base.frequency_penalty),
    )


def evaluate_retrieval_case(
    *,
    case: dict[str, Any],
    generation: dict[str, Any],
) -> dict[str, Any]:
    historical_paths_original = [
        str(path).strip()
        for path in get_case_historical_changed_files(case)
        if str(path).strip()
    ]
    historical_paths = {
        normalize_path_for_eval(path)
        for path in historical_paths_original
        if normalize_path_for_eval(path)
    }
    retrieved_paths_original = [
        str(item.get("path", "")).strip()
        for item in (generation.get("evidence") or [])
        if str(item.get("path", "")).strip()
    ]
    retrieved_paths = [
        normalize_path_for_eval(path)
        for path in retrieved_paths_original
        if normalize_path_for_eval(path)
    ]

    if not historical_paths:
        return {
            "historical_changed_file_count": 0,
            "retrieved_file_count": len(retrieved_paths),
            "matched_changed_file_count": 0,
            "recall_at_all": None,
            "recall_at_5": None,
            "recall_at_10": None,
            "recall_at_20": None,
            "precision_at_10": None,
            "precision_at_20": None,
            "mrr": None,
            "matched_paths": [],
        }

    match_map = build_eval_path_match_map(historical_paths_original, retrieved_paths_original)
    matched_paths = sorted(match_map.keys())
    first_match_rank = None
    for idx, path in enumerate(retrieved_paths_original, start=1):
        if any(paths_match_for_eval(gt_path, path) for gt_path in historical_paths_original):
            first_match_rank = idx
            break

    recall_at_all = len(matched_paths) / len(historical_paths)
    recall_at_5 = len(build_eval_path_match_map(historical_paths_original, retrieved_paths_original[:5])) / len(historical_paths)
    recall_at_10 = len(build_eval_path_match_map(historical_paths_original, retrieved_paths_original[:10])) / len(historical_paths)
    recall_at_20 = len(build_eval_path_match_map(historical_paths_original, retrieved_paths_original[:20])) / len(historical_paths)
    precision_at_10 = (
        len(build_eval_path_match_map(historical_paths_original, retrieved_paths_original[:10])) / min(10, len(retrieved_paths_original))
        if retrieved_paths_original
        else 0.0
    )
    precision_at_20 = (
        len(build_eval_path_match_map(historical_paths_original, retrieved_paths_original[:20])) / min(20, len(retrieved_paths_original))
        if retrieved_paths_original
        else 0.0
    )
    mrr = 1.0 / first_match_rank if first_match_rank is not None else 0.0

    return {
        "historical_changed_file_count": len(historical_paths),
        "retrieved_file_count": len(retrieved_paths),
        "matched_changed_file_count": len(matched_paths),
        "recall_at_all": round(recall_at_all, 3),
        "recall_at_5": round(recall_at_5, 3),
        "recall_at_10": round(recall_at_10, 3),
        "recall_at_20": round(recall_at_20, 3),
        "precision_at_10": round(precision_at_10, 3),
        "precision_at_20": round(precision_at_20, 3),
        "mrr": round(mrr, 3),
        "matched_paths": matched_paths,
    }


def evaluate_iis_case(
    *,
    case: dict[str, Any],
    candidate_label: str,
    candidate_iis_text: str,
    candidate_evidence: list[dict[str, Any]] | None,
    app_config: AppConfig,
) -> dict[str, Any]:
    judge_config = build_eval_judge_config(app_config)
    prompt = render_prompt(
        "eval_iis_judge_system",
        case_id=get_case_id(case),
        task_type=get_case_task_type(case),
        difficulty=get_case_difficulty(case),
        candidate_label=candidate_label,
        description=get_case_description(case),
        candidate_iis=candidate_iis_text,
        candidate_evidence=format_evidence_block(candidate_evidence),
        historical_what_to_do=get_case_historical_what_to_do(case) or "None provided.",
        historical_changed_files=format_list_block(get_case_historical_changed_files(case)),
        notes=str(case.get("notes", "")).strip() or "None provided.",
    )
    raw = _call_remote_chat(prompt, judge_config)
    return json.loads(raw)


def evaluate_software_requirements_case(
    *,
    case: dict[str, Any],
    confirmed_iis_text: str,
    candidate_label: str,
    candidate_software_requirements_text: str,
    app_config: AppConfig,
) -> dict[str, Any]:
    judge_config = build_eval_judge_config(app_config)
    prompt = render_prompt(
        "eval_software_requirements_judge_system",
        case_id=get_case_id(case),
        task_type=get_case_task_type(case),
        difficulty=get_case_difficulty(case),
        candidate_label=candidate_label,
        description=get_case_description(case),
        confirmed_iis=confirmed_iis_text,
        candidate_software_requirements=candidate_software_requirements_text,
        historical_software_requirements=format_list_block(get_case_historical_software_requirements(case)),
        notes=str(case.get("notes", "")).strip() or "None provided.",
    )
    raw = _call_remote_chat(prompt, judge_config)
    return json.loads(raw)


def aggregate_scores(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_artifact: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_strategy_retrieval: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_strategy_artifact: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    by_ranking_strategy_retrieval: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_file_aggregation_strategy_retrieval: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_ranking_strategy_artifact: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    by_file_aggregation_strategy_artifact: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    by_task_type: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    by_difficulty: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    by_task_and_difficulty: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for case in case_results:
        strategy = str(case.get("retrieval_strategy", "default"))
        ranking_strategy = str(case.get("ranking_strategy", "default"))
        file_aggregation_strategy = str(case.get("file_aggregation_strategy", "default"))
        task_type = str(case.get("task_type", "unknown"))
        difficulty = str(case.get("difficulty", "unknown"))
        retrieval_metrics = case.get("retrieval_metrics", {})
        if isinstance(retrieval_metrics, dict):
            for metric_name, value in retrieval_metrics.items():
                if isinstance(value, (int, float)):
                    by_strategy_retrieval[strategy][metric_name].append(float(value))
                    by_ranking_strategy_retrieval[ranking_strategy][metric_name].append(float(value))
                    by_file_aggregation_strategy_retrieval[file_aggregation_strategy][metric_name].append(float(value))
        for artifact_name in (
            "iis_evaluation",
            "historical_iis_baseline_evaluation",
            "software_requirements_evaluation",
            "historical_software_requirements_baseline_evaluation",
        ):
            evaluation = case.get(artifact_name)
            if not evaluation:
                continue
            scores = evaluation.get("scores", {})
            for dimension, value in scores.items():
                if isinstance(value, (int, float)):
                    by_artifact[artifact_name][dimension].append(float(value))
                    by_strategy_artifact[strategy][artifact_name][dimension].append(float(value))
                    by_ranking_strategy_artifact[ranking_strategy][artifact_name][dimension].append(float(value))
                    by_file_aggregation_strategy_artifact[file_aggregation_strategy][artifact_name][dimension].append(float(value))
                    by_task_type[artifact_name][task_type][dimension].append(float(value))
                    by_difficulty[artifact_name][difficulty][dimension].append(float(value))
                    by_task_and_difficulty[artifact_name][f"{task_type}__{difficulty}"][dimension].append(float(value))

    aggregate = {
        "by_artifact": {},
        "by_strategy": {},
        "by_ranking_strategy": {},
        "by_file_aggregation_strategy": {},
        "by_task_type": {},
        "by_difficulty": {},
        "by_task_type_and_difficulty": {},
    }

    for artifact_name, dimension_map in by_artifact.items():
        aggregate["by_artifact"][artifact_name] = {
            dimension: round(statistics.mean(values), 3)
            for dimension, values in dimension_map.items()
            if values
        }

    for strategy, metric_map in by_strategy_retrieval.items():
        aggregate["by_strategy"].setdefault(strategy, {})
        aggregate["by_strategy"][strategy]["retrieval_metrics"] = {
            metric: round(statistics.mean(values), 3)
            for metric, values in metric_map.items()
            if values
        }
    for strategy, artifact_grouping in by_strategy_artifact.items():
        aggregate["by_strategy"].setdefault(strategy, {})
        for artifact_name, dimension_map in artifact_grouping.items():
            aggregate["by_strategy"][strategy][artifact_name] = {
                dimension: round(statistics.mean(values), 3)
                for dimension, values in dimension_map.items()
                if values
            }

    for strategy, metric_map in by_ranking_strategy_retrieval.items():
        aggregate["by_ranking_strategy"].setdefault(strategy, {})
        aggregate["by_ranking_strategy"][strategy]["retrieval_metrics"] = {
            metric: round(statistics.mean(values), 3)
            for metric, values in metric_map.items()
            if values
        }
    for strategy, artifact_grouping in by_ranking_strategy_artifact.items():
        aggregate["by_ranking_strategy"].setdefault(strategy, {})
        for artifact_name, dimension_map in artifact_grouping.items():
            aggregate["by_ranking_strategy"][strategy][artifact_name] = {
                dimension: round(statistics.mean(values), 3)
                for dimension, values in dimension_map.items()
                if values
            }

    for strategy, metric_map in by_file_aggregation_strategy_retrieval.items():
        aggregate["by_file_aggregation_strategy"].setdefault(strategy, {})
        aggregate["by_file_aggregation_strategy"][strategy]["retrieval_metrics"] = {
            metric: round(statistics.mean(values), 3)
            for metric, values in metric_map.items()
            if values
        }
    for strategy, artifact_grouping in by_file_aggregation_strategy_artifact.items():
        aggregate["by_file_aggregation_strategy"].setdefault(strategy, {})
        for artifact_name, dimension_map in artifact_grouping.items():
            aggregate["by_file_aggregation_strategy"][strategy][artifact_name] = {
                dimension: round(statistics.mean(values), 3)
                for dimension, values in dimension_map.items()
                if values
            }

    for artifact_name, grouping in by_task_type.items():
        aggregate["by_task_type"][artifact_name] = {}
        for task_type, dimension_map in grouping.items():
            aggregate["by_task_type"][artifact_name][task_type] = {
                dimension: round(statistics.mean(values), 3)
                for dimension, values in dimension_map.items()
                if values
            }

    for artifact_name, grouping in by_difficulty.items():
        aggregate["by_difficulty"][artifact_name] = {}
        for difficulty, dimension_map in grouping.items():
            aggregate["by_difficulty"][artifact_name][difficulty] = {
                dimension: round(statistics.mean(values), 3)
                for dimension, values in dimension_map.items()
                if values
            }

    for artifact_name, grouping in by_task_and_difficulty.items():
        aggregate["by_task_type_and_difficulty"][artifact_name] = {}
        for group_name, dimension_map in grouping.items():
            aggregate["by_task_type_and_difficulty"][artifact_name][group_name] = {
                dimension: round(statistics.mean(values), 3)
                for dimension, values in dimension_map.items()
                if values
            }

    return aggregate


def render_markdown_summary(case_results: list[dict[str, Any]], aggregate: dict[str, Any]) -> str:
    lines = ["# Evaluation Summary", ""]

    lines.append("## Case Reference")
    lines.append("")
    lines.append("| Case ID | Case Preview | Task Type | Difficulty |")
    lines.append("| --- | --- | --- | --- |")
    seen_case_ids: set[str] = set()
    for case in case_results:
        case_id = str(case.get("case_id", "")).strip()
        if not case_id or case_id in seen_case_ids:
            continue
        seen_case_ids.add(case_id)
        description_preview = str(case.get("description_preview", "")).replace("\n", " ").strip()
        task_type = str(case.get("task_type", "")).strip()
        difficulty = str(case.get("difficulty", "")).strip()
        lines.append(f"| {case_id} | {description_preview} | {task_type} | {difficulty} |")
    lines.append("")

    lines.append("## Aggregate Scores by Artifact")
    lines.append("")
    for artifact_name, scores in aggregate.get("by_artifact", {}).items():
        lines.append(f"### {artifact_name}")
        lines.append("")
        lines.append("| Dimension | Mean Score |")
        lines.append("| --- | ---: |")
        for dimension, value in sorted(scores.items()):
            lines.append(f"| {dimension} | {value:.3f} |")
        lines.append("")

    lines.append("## Aggregate Results by Retrieval Strategy")
    lines.append("")
    for strategy, sections in sorted(aggregate.get("by_strategy", {}).items()):
        lines.append(f"### strategy=`{strategy}`")
        lines.append("")
        retrieval_scores = sections.get("retrieval_metrics", {})
        if retrieval_scores:
            lines.append("#### Retrieval Metrics")
            lines.append("")
            lines.append("| Metric | Mean Value |")
            lines.append("| --- | ---: |")
            for metric, value in sorted(retrieval_scores.items()):
                lines.append(f"| {metric} | {value:.3f} |")
            lines.append("")
        for artifact_name in ("iis_evaluation", "software_requirements_evaluation"):
            artifact_scores = sections.get(artifact_name, {})
            if not artifact_scores:
                continue
            lines.append(f"#### {artifact_name}")
            lines.append("")
            lines.append("| Dimension | Mean Score |")
            lines.append("| --- | ---: |")
            for dimension, value in sorted(artifact_scores.items()):
                lines.append(f"| {dimension} | {value:.3f} |")
            lines.append("")

    lines.append("## Aggregate Results by Ranking Strategy")
    lines.append("")
    for strategy, sections in sorted(aggregate.get("by_ranking_strategy", {}).items()):
        lines.append(f"### ranking_strategy=`{strategy}`")
        lines.append("")
        retrieval_scores = sections.get("retrieval_metrics", {})
        if retrieval_scores:
            lines.append("#### Retrieval Metrics")
            lines.append("")
            lines.append("| Metric | Mean Value |")
            lines.append("| --- | ---: |")
            for metric, value in sorted(retrieval_scores.items()):
                lines.append(f"| {metric} | {value:.3f} |")
            lines.append("")
        for artifact_name in ("iis_evaluation", "software_requirements_evaluation"):
            artifact_scores = sections.get(artifact_name, {})
            if not artifact_scores:
                continue
            lines.append(f"#### {artifact_name}")
            lines.append("")
            lines.append("| Dimension | Mean Score |")
            lines.append("| --- | ---: |")
            for dimension, value in sorted(artifact_scores.items()):
                lines.append(f"| {dimension} | {value:.3f} |")
            lines.append("")

    lines.append("## Aggregate Results by File Aggregation Strategy")
    lines.append("")
    for strategy, sections in sorted(aggregate.get("by_file_aggregation_strategy", {}).items()):
        lines.append(f"### file_aggregation_strategy=`{strategy}`")
        lines.append("")
        retrieval_scores = sections.get("retrieval_metrics", {})
        if retrieval_scores:
            lines.append("#### Retrieval Metrics")
            lines.append("")
            lines.append("| Metric | Mean Value |")
            lines.append("| --- | ---: |")
            for metric, value in sorted(retrieval_scores.items()):
                lines.append(f"| {metric} | {value:.3f} |")
            lines.append("")
        for artifact_name in ("iis_evaluation", "software_requirements_evaluation"):
            artifact_scores = sections.get(artifact_name, {})
            if not artifact_scores:
                continue
            lines.append(f"#### {artifact_name}")
            lines.append("")
            lines.append("| Dimension | Mean Score |")
            lines.append("| --- | ---: |")
            for dimension, value in sorted(artifact_scores.items()):
                lines.append(f"| {dimension} | {value:.3f} |")
            lines.append("")

    lines.append("## IIS vs Historical Baseline")
    lines.append("")
    lines.append("| Strategy | Generated IIS Overall | Historical IIS Overall | Delta |")
    lines.append("| --- | ---: | ---: | ---: |")
    for strategy, sections in sorted(aggregate.get("by_strategy", {}).items()):
        generated_overall = sections.get("iis_evaluation", {}).get("overall")
        historical_overall = sections.get("historical_iis_baseline_evaluation", {}).get("overall")
        if generated_overall is None and historical_overall is None:
            continue
        delta = (
            round(float(generated_overall) - float(historical_overall), 3)
            if generated_overall is not None and historical_overall is not None
            else ""
        )
        generated_text = f"{generated_overall:.3f}" if isinstance(generated_overall, (int, float)) else ""
        historical_text = f"{historical_overall:.3f}" if isinstance(historical_overall, (int, float)) else ""
        delta_text = f"{delta:+.3f}" if isinstance(delta, (int, float)) else ""
        lines.append(f"| {strategy} | {generated_text} | {historical_text} | {delta_text} |")
    lines.append("")

    lines.append("## Software Requirements vs Historical Baseline")
    lines.append("")
    lines.append("| Strategy | Generated SQ Overall | Historical SQ Overall | Delta |")
    lines.append("| --- | ---: | ---: | ---: |")
    for strategy, sections in sorted(aggregate.get("by_strategy", {}).items()):
        generated_overall = sections.get("software_requirements_evaluation", {}).get("overall")
        historical_overall = sections.get("historical_software_requirements_baseline_evaluation", {}).get("overall")
        if generated_overall is None and historical_overall is None:
            continue
        delta = (
            round(float(generated_overall) - float(historical_overall), 3)
            if generated_overall is not None and historical_overall is not None
            else ""
        )
        generated_text = f"{generated_overall:.3f}" if isinstance(generated_overall, (int, float)) else ""
        historical_text = f"{historical_overall:.3f}" if isinstance(historical_overall, (int, float)) else ""
        delta_text = f"{delta:+.3f}" if isinstance(delta, (int, float)) else ""
        lines.append(f"| {strategy} | {generated_text} | {historical_text} | {delta_text} |")
    lines.append("")

    lines.append("## Aggregate Scores by Task Type and Difficulty")
    lines.append("")
    for artifact_name, groups in aggregate.get("by_task_type", {}).items():
        lines.append(f"### {artifact_name} by task_type")
        lines.append("")
        for task_type, scores in sorted(groups.items()):
            lines.append(f"#### task_type=`{task_type}`")
            lines.append("")
            lines.append("| Dimension | Mean Score |")
            lines.append("| --- | ---: |")
            for dimension, value in sorted(scores.items()):
                lines.append(f"| {dimension} | {value:.3f} |")
            lines.append("")

    for artifact_name, groups in aggregate.get("by_difficulty", {}).items():
        lines.append(f"### {artifact_name} by difficulty")
        lines.append("")
        for difficulty, scores in sorted(groups.items()):
            lines.append(f"#### difficulty=`{difficulty}`")
            lines.append("")
            lines.append("| Dimension | Mean Score |")
            lines.append("| --- | ---: |")
            for dimension, value in sorted(scores.items()):
                lines.append(f"| {dimension} | {value:.3f} |")
            lines.append("")

    for artifact_name, groups in aggregate.get("by_task_type_and_difficulty", {}).items():
        lines.append(f"### {artifact_name} by task_type and difficulty")
        lines.append("")
        for group_name, scores in sorted(groups.items()):
            task_type, difficulty = group_name.split("__", 1)
            lines.append(f"#### task_type=`{task_type}` difficulty=`{difficulty}`")
            lines.append("")
            lines.append("| Dimension | Mean Score |")
            lines.append("| --- | ---: |")
            for dimension, value in sorted(scores.items()):
                lines.append(f"| {dimension} | {value:.3f} |")
            lines.append("")

    lines.append("## Per-Case Overview")
    lines.append("")
    lines.append("| Case ID | Case Preview | Ranking Strategy | File Aggregation | Strategy | Task Type | Difficulty | Recall@10 | Recall@20 | IIS Overall | Historical IIS Overall | IIS Delta | SQ Overall | Historical SQ Overall | SQ Delta |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for case in case_results:
        iis_score = case.get("iis_evaluation", {}).get("scores", {}).get("overall", "")
        historical_iis_score = case.get("historical_iis_baseline_evaluation", {}).get("scores", {}).get("overall", "")
        sq_score = case.get("software_requirements_evaluation", {}).get("scores", {}).get("overall", "")
        historical_sq_score = case.get("historical_software_requirements_baseline_evaluation", {}).get("scores", {}).get("overall", "")
        retrieval_recall_at_10 = case.get("retrieval_metrics", {}).get("recall_at_10", "")
        retrieval_recall_at_20 = case.get("retrieval_metrics", {}).get("recall_at_20", "")
        description_preview = str(case.get("description_preview", "")).replace("\n", " ").strip()
        iis_delta = (
            round(float(iis_score) - float(historical_iis_score), 3)
            if isinstance(iis_score, (int, float)) and isinstance(historical_iis_score, (int, float))
            else ""
        )
        sq_delta = (
            round(float(sq_score) - float(historical_sq_score), 3)
            if isinstance(sq_score, (int, float)) and isinstance(historical_sq_score, (int, float))
            else ""
        )
        lines.append(
            f"| {case.get('case_id', '')} | {description_preview} | {case.get('ranking_strategy', '')} | {case.get('file_aggregation_strategy', '')} | {case.get('retrieval_strategy', '')} | {case.get('task_type', '')} | {case.get('difficulty', '')} | {retrieval_recall_at_10} | {retrieval_recall_at_20} | {iis_score} | {historical_iis_score} | {iis_delta} | {sq_score} | {historical_sq_score} | {sq_delta} |"
        )

    return "\n".join(lines) + "\n"


def load_eval_config() -> AppConfig:
    return load_app_config()
