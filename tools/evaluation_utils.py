from __future__ import annotations

"""
Shared evaluation helpers for generation quality experiments.

This module keeps the actual evaluation scripts small and makes it easy to
reuse the same LLM-as-judge and aggregation logic across multiple evaluation
experiments.
"""

import json
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
from modules.shared.models import RetrievalIntent  # noqa: E402
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
        return [stripped] if stripped else []
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


def format_list_block(items: list[str], empty_label: str = "None provided.") -> str:
    if not items:
        return empty_label
    return "\n".join(f"- {item}" for item in items)


def normalize_path_for_eval(path: str) -> str:
    return str(path).strip().replace("\\", "/").lower()


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


def run_generation_pipeline(
    description: str,
    app_config: AppConfig,
) -> dict[str, Any]:
    summary, technical_intent, keywords, suspected_areas, query = build_retrieval_intent(
        description,
        app_config.llm_api,
    )
    retrieval_intent_model = RetrievalIntent(
        summary=summary,
        technical_intent=technical_intent,
        keywords=keywords,
        suspected_areas=suspected_areas,
        query=query,
    )
    evidence_items = retrieve_code_evidence(
        retrieval_intent_model,
        app_config.code_rag,
    )
    iis = generate_draft(description, evidence_items, [], app_config.llm_api)
    software_requirements = generate_software_requirements(
        description,
        iis,
        app_config.llm_api,
        source_iis_version_id=None,
        source_iis_version_number=iis.version,
    )
    return {
        "retrieval_intent": {
            "summary": retrieval_intent_model.summary,
            "technical_intent": retrieval_intent_model.technical_intent,
            "keywords": retrieval_intent_model.keywords,
            "suspected_areas": retrieval_intent_model.suspected_areas,
            "query": retrieval_intent_model.query,
        },
        "evidence": [
            {
                "path": item.path,
                "score": item.score,
                "symbol": item.symbol,
                "why_relevant": item.why_relevant,
                "suggested_change": item.suggested_change,
                "location_hint": item.location_hint,
            }
            for item in evidence_items
        ],
        "iis": {
            "version": iis.version,
            "raw_text": iis.raw_text,
            "summary": iis.summary,
        },
        "software_requirements": {
            "version": software_requirements.version,
            "requirements": software_requirements.requirements,
            "traceability_summary": software_requirements.traceability_summary,
            "raw_text": software_requirements.raw_text,
            "source_iis_version_number": software_requirements.source_iis_version_number,
        },
    }


def apply_retrieval_strategy_override(
    app_config: AppConfig,
    *,
    strategy: str,
    alpha: float | None = None,
    beta: float | None = None,
    candidate_multiplier: int | None = None,
) -> AppConfig:
    code_rag = replace(
        app_config.code_rag,
        file_aggregation_strategy=strategy,
        file_aggregation_alpha=alpha if alpha is not None else app_config.code_rag.file_aggregation_alpha,
        file_aggregation_beta=beta if beta is not None else app_config.code_rag.file_aggregation_beta,
        file_aggregation_candidate_multiplier=(
            candidate_multiplier
            if candidate_multiplier is not None
            else app_config.code_rag.file_aggregation_candidate_multiplier
        ),
    )
    return replace(app_config, code_rag=code_rag)


def evaluate_retrieval_case(
    *,
    case: dict[str, Any],
    generation: dict[str, Any],
) -> dict[str, Any]:
    historical_paths = {
        normalize_path_for_eval(path)
        for path in normalize_string_list(case.get("historical_changed_files"))
        if normalize_path_for_eval(path)
    }
    retrieved_paths = [
        normalize_path_for_eval(str(item.get("path", "")))
        for item in (generation.get("evidence") or [])
        if normalize_path_for_eval(str(item.get("path", "")))
    ]

    if not historical_paths:
        return {
            "historical_changed_file_count": 0,
            "retrieved_file_count": len(retrieved_paths),
            "matched_changed_file_count": 0,
            "recall_at_all": None,
            "recall_at_5": None,
            "recall_at_10": None,
            "mrr": None,
            "matched_paths": [],
        }

    matched_paths = sorted({path for path in retrieved_paths if path in historical_paths})
    first_match_rank = None
    for idx, path in enumerate(retrieved_paths, start=1):
        if path in historical_paths:
            first_match_rank = idx
            break

    recall_at_all = len({path for path in retrieved_paths if path in historical_paths}) / len(historical_paths)
    recall_at_5 = len({path for path in retrieved_paths[:5] if path in historical_paths}) / len(historical_paths)
    recall_at_10 = len({path for path in retrieved_paths[:10] if path in historical_paths}) / len(historical_paths)
    mrr = 1.0 / first_match_rank if first_match_rank is not None else 0.0

    return {
        "historical_changed_file_count": len(historical_paths),
        "retrieved_file_count": len(retrieved_paths),
        "matched_changed_file_count": len(matched_paths),
        "recall_at_all": round(recall_at_all, 3),
        "recall_at_5": round(recall_at_5, 3),
        "recall_at_10": round(recall_at_10, 3),
        "mrr": round(mrr, 3),
        "matched_paths": matched_paths,
    }


def evaluate_iis_case(
    *,
    case: dict[str, Any],
    generated_iis_text: str,
    app_config: AppConfig,
) -> dict[str, Any]:
    prompt = render_prompt(
        "eval_iis_judge_system",
        case_id=str(case.get("case_id", "")),
        task_type=str(case.get("task_type", "")),
        difficulty=str(case.get("difficulty", "")),
        description=str(case.get("description", "")),
        generated_iis=generated_iis_text,
        historical_what_to_do=str(case.get("historical_what_to_do", "")).strip() or "None provided.",
        historical_changed_files=format_list_block(normalize_string_list(case.get("historical_changed_files"))),
        notes=str(case.get("notes", "")).strip() or "None provided.",
    )
    raw = _call_remote_chat(prompt, app_config.llm_api)
    return json.loads(raw)


def evaluate_software_requirements_case(
    *,
    case: dict[str, Any],
    confirmed_iis_text: str,
    generated_software_requirements_text: str,
    app_config: AppConfig,
) -> dict[str, Any]:
    prompt = render_prompt(
        "eval_software_requirements_judge_system",
        case_id=str(case.get("case_id", "")),
        task_type=str(case.get("task_type", "")),
        difficulty=str(case.get("difficulty", "")),
        description=str(case.get("description", "")),
        confirmed_iis=confirmed_iis_text,
        generated_software_requirements=generated_software_requirements_text,
        historical_software_requirements=format_list_block(
            normalize_string_list(case.get("historical_software_requirements"))
        ),
        notes=str(case.get("notes", "")).strip() or "None provided.",
    )
    raw = _call_remote_chat(prompt, app_config.llm_api)
    return json.loads(raw)


def aggregate_scores(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_artifact: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_strategy_retrieval: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_strategy_artifact: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
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
        task_type = str(case.get("task_type", "unknown"))
        difficulty = str(case.get("difficulty", "unknown"))
        retrieval_metrics = case.get("retrieval_metrics", {})
        if isinstance(retrieval_metrics, dict):
            for metric_name, value in retrieval_metrics.items():
                if isinstance(value, (int, float)):
                    by_strategy_retrieval[strategy][metric_name].append(float(value))
        for artifact_name in ("iis_evaluation", "software_requirements_evaluation"):
            evaluation = case.get(artifact_name)
            if not evaluation:
                continue
            scores = evaluation.get("scores", {})
            for dimension, value in scores.items():
                if isinstance(value, (int, float)):
                    by_artifact[artifact_name][dimension].append(float(value))
                    by_strategy_artifact[strategy][artifact_name][dimension].append(float(value))
                    by_task_type[artifact_name][task_type][dimension].append(float(value))
                    by_difficulty[artifact_name][difficulty][dimension].append(float(value))
                    by_task_and_difficulty[artifact_name][f"{task_type}__{difficulty}"][dimension].append(float(value))

    aggregate = {
        "by_artifact": {},
        "by_strategy": {},
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
    lines.append("| Case ID | Strategy | Task Type | Difficulty | Retrieval Recall@10 | IIS Overall | SQ Overall |")
    lines.append("| --- | --- | --- | --- | ---: | ---: | ---: |")
    for case in case_results:
        iis_score = case.get("iis_evaluation", {}).get("scores", {}).get("overall", "")
        sq_score = case.get("software_requirements_evaluation", {}).get("scores", {}).get("overall", "")
        retrieval_recall_at_10 = case.get("retrieval_metrics", {}).get("recall_at_10", "")
        lines.append(
            f"| {case.get('case_id', '')} | {case.get('retrieval_strategy', '')} | {case.get('task_type', '')} | {case.get('difficulty', '')} | {retrieval_recall_at_10} | {iis_score} | {sq_score} |"
        )

    return "\n".join(lines) + "\n"


def load_eval_config() -> AppConfig:
    return load_app_config()
