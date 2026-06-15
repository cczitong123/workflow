from __future__ import annotations

"""
Run open-question evaluation from existing evaluation checkpoints.

Edit the configuration block at the top, then run:

    python tools/run_open_question_evaluation.py

This script:
- reads generated IIS artifacts from existing evaluation checkpoints
- extracts generated open questions
- evaluates their quality with an LLM judge
- writes standalone OQ evaluation outputs without changing the main eval pipeline
"""

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation_utils import (
    PROJECT_ROOT,
    aggregate_scores,
    evaluate_open_questions_case,
    get_case_description,
    get_case_difficulty,
    get_case_historical_what_to_do,
    get_case_id,
    get_case_task_type,
    load_eval_config,
    load_json,
    write_json,
    write_markdown,
)


# Configuration - edit these values directly before running.
SOURCE_EVALUATION_OUTPUT_DIR = PROJECT_ROOT / "tools" / "evaluation_outputs"
OUTPUT_DIR = PROJECT_ROOT / "tools" / "open_question_evaluation_outputs"
RESUME_FROM_CHECKPOINTS = True
SKIP_COMPLETED_CHECKPOINTS = True
CONTINUE_AFTER_CASE_ERROR = True
CASE_IDS: list[str] = []
STRATEGIES: list[str] = []  # Leave empty to evaluate every strategy directory.
MAX_CASES: int | None = None
CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    sanitized = "".join(char if char.isalnum() or char in "._-" else "_" for char in value.strip())
    return sanitized.strip("_") or "unnamed"


def _description_preview(text: str, limit: int = 120) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _resolve_case_id(case: dict[str, Any]) -> str:
    explicit_id = get_case_id(case)
    if explicit_id:
        return explicit_id
    description = " ".join(get_case_description(case).split()).strip()
    if description:
        digest = hashlib.sha1(description.encode("utf-8")).hexdigest()[:10].upper()
        return f"CASE-{digest}"
    return "CASE-NO-DESCRIPTION"


def _should_keep_case(case_id: str) -> bool:
    if CASE_IDS and case_id not in CASE_IDS:
        return False
    return True


def _log_progress(
    *,
    step_label: str,
    strategy_name: str,
    case_id: str,
    case_index: int,
    total_cases: int,
    strategy_index: int,
    total_strategies: int,
) -> None:
    print(
        f"[OQ-EVAL][strategy {strategy_index}/{total_strategies}={strategy_name}]"
        f"[case {case_index}/{total_cases}={case_id}] {step_label}",
        flush=True,
    )


def _checkpoint_path(strategy_name: str, case_id: str) -> Path:
    return CHECKPOINT_DIR / _slugify(strategy_name) / f"{_slugify(case_id)}.json"


def _load_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = load_json(path)
    return payload if isinstance(payload, dict) else None


def _save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    payload["updated_at"] = _utc_now_iso()
    write_json(path, payload)


def _mark_stage(checkpoint: dict[str, Any], stage_name: str, status: str) -> None:
    stages = checkpoint.setdefault("stages", {})
    if not isinstance(stages, dict):
        stages = {}
        checkpoint["stages"] = stages
    stages[stage_name] = {
        "status": status,
        "updated_at": _utc_now_iso(),
    }


def _classify_failure_status(checkpoint: dict[str, Any]) -> str:
    last_step = str(checkpoint.get("last_step", "")).strip()
    if last_step == "open_question_evaluation":
        return "judge_failed"
    if last_step in {"write_outputs", "completed"}:
        return "summary_failed"
    return "failed"


def _iter_source_checkpoints() -> list[tuple[str, Path]]:
    source_root = SOURCE_EVALUATION_OUTPUT_DIR / "checkpoints"
    if not source_root.exists():
        return []
    items: list[tuple[str, Path]] = []
    for strategy_dir in sorted(source_root.iterdir()):
        if not strategy_dir.is_dir():
            continue
        strategy_name = strategy_dir.name
        if STRATEGIES and strategy_name not in STRATEGIES:
            continue
        for path in sorted(strategy_dir.glob("*.json")):
            items.append((strategy_name, path))
    return items


def _extract_source_case(path: Path) -> dict[str, Any] | None:
    payload = load_json(path)
    return payload if isinstance(payload, dict) else None


def _build_case_result(source_checkpoint: dict[str, Any], strategy_name: str) -> dict[str, Any]:
    case_result = source_checkpoint.get("case_result", {})
    if not isinstance(case_result, dict):
        case_result = {}
    case_id = str(case_result.get("case_id", "")).strip() or _resolve_case_id(case_result)
    description = get_case_description(case_result)
    generation = case_result.get("generation", {})
    if not isinstance(generation, dict):
        generation = {}
    iis = generation.get("iis", {})
    if not isinstance(iis, dict):
        iis = {}
    return {
        "case_id": case_id,
        "case_label": f"{case_id} | {_description_preview(description)}" if description else case_id,
        "description_preview": _description_preview(description),
        "source_strategy": strategy_name,
        "task_type": get_case_task_type(case_result),
        "difficulty": get_case_difficulty(case_result),
        "generated_open_question_count": len(iis.get("open_questions", []) or []),
        "open_question_evaluation": None,
    }


def _build_eval_case(source_checkpoint: dict[str, Any]) -> dict[str, Any]:
    case_result = source_checkpoint.get("case_result", {})
    if not isinstance(case_result, dict):
        return {}
    return case_result


def _render_compact_summary(results: list[dict[str, Any]]) -> str:
    lines = ["# Open Question Evaluation Compact Summary", ""]
    lines.extend(
        [
            "## Strategy Comparison",
            "",
            "| Strategy | Necessity | Specificity | Impact | Non-Redundancy | Critical Gap Detection | Overall | Avg OQ Count |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    buckets: dict[str, dict[str, list[float]]] = {}
    for result in results:
        strategy = str(result.get("source_strategy", "")).strip() or "unknown"
        scores = ((result.get("open_question_evaluation") or {}).get("scores") or {})
        bucket = buckets.setdefault(
            strategy,
            {
                "necessity": [],
                "specificity": [],
                "impact_on_downstream": [],
                "non_redundancy": [],
                "missing_critical_gap_detection": [],
                "overall": [],
                "generated_open_question_count": [],
            },
        )
        for key in ("necessity", "specificity", "impact_on_downstream", "non_redundancy", "missing_critical_gap_detection", "overall"):
            value = scores.get(key)
            if isinstance(value, (int, float)):
                bucket[key].append(float(value))
        count = result.get("generated_open_question_count")
        if isinstance(count, (int, float)):
            bucket["generated_open_question_count"].append(float(count))

    def _avg(values: list[float]) -> str:
        return f"{(sum(values) / len(values)):.3f}" if values else ""

    for strategy, bucket in sorted(buckets.items()):
        lines.append(
            f"| {strategy} | {_avg(bucket['necessity'])} | {_avg(bucket['specificity'])} | "
            f"{_avg(bucket['impact_on_downstream'])} | {_avg(bucket['non_redundancy'])} | "
            f"{_avg(bucket['missing_critical_gap_detection'])} | {_avg(bucket['overall'])} | "
            f"{_avg(bucket['generated_open_question_count'])} |"
        )

    lines.extend(["", "## Per-Case Key Scores", ""])
    lines.extend(
        [
            "| Case ID | Strategy | OQ Count | Necessity | Specificity | Impact | Non-Redundancy | Critical Gap Detection | Overall |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in results:
        scores = ((result.get("open_question_evaluation") or {}).get("scores") or {})
        lines.append(
            f"| {result.get('case_id', '')} | {result.get('source_strategy', '')} | "
            f"{result.get('generated_open_question_count', '')} | "
            f"{scores.get('necessity', '')} | {scores.get('specificity', '')} | "
            f"{scores.get('impact_on_downstream', '')} | {scores.get('non_redundancy', '')} | "
            f"{scores.get('missing_critical_gap_detection', '')} | {scores.get('overall', '')} |"
        )
    return "\n".join(lines) + "\n"


def _render_detailed_summary(results: list[dict[str, Any]]) -> str:
    lines = ["# Open Question Evaluation Summary", ""]
    for result in results:
        evaluation = result.get("open_question_evaluation") or {}
        scores = evaluation.get("scores", {}) if isinstance(evaluation, dict) else {}
        rationales = evaluation.get("dimension_rationales", {}) if isinstance(evaluation, dict) else {}
        strengths = evaluation.get("strengths", []) if isinstance(evaluation, dict) else []
        gaps = evaluation.get("gaps", []) if isinstance(evaluation, dict) else []
        lines.extend(
            [
                f"## {result.get('case_id', '')} · {result.get('source_strategy', '')}",
                "",
                f"- Case Preview: {result.get('description_preview', '')}",
                f"- Open Question Count: {result.get('generated_open_question_count', '')}",
                "",
                "### Scores",
                "",
                "| Dimension | Score |",
                "| --- | ---: |",
            ]
        )
        for key in ("necessity", "specificity", "impact_on_downstream", "non_redundancy", "missing_critical_gap_detection", "overall"):
            lines.append(f"| {key} | {scores.get(key, '')} |")
        lines.extend(["", "### Rationales", ""])
        for key in ("necessity", "specificity", "impact_on_downstream", "non_redundancy", "missing_critical_gap_detection", "overall"):
            lines.append(f"- **{key}**: {rationales.get(key, '')}")
        lines.extend(["", "### Strengths", ""])
        if strengths:
            lines.extend([f"- {item}" for item in strengths])
        else:
            lines.append("- None.")
        lines.extend(["", "### Gaps", ""])
        if gaps:
            lines.extend([f"- {item}" for item in gaps])
        else:
            lines.append("- None.")
        summary = evaluation.get("summary", "") if isinstance(evaluation, dict) else ""
        lines.extend(["", "### Summary", "", summary or ""])
        lines.append("")
    return "\n".join(lines)


def _write_outputs(results: list[dict[str, Any]]) -> None:
    write_json(OUTPUT_DIR / "open_question_results.json", results)
    write_markdown(OUTPUT_DIR / "open_question_summary.md", _render_detailed_summary(results))
    write_markdown(OUTPUT_DIR / "open_question_compact_summary.md", _render_compact_summary(results))


def main() -> None:
    app_config = load_eval_config()
    source_items = _iter_source_checkpoints()
    if MAX_CASES is not None:
        source_items = source_items[:MAX_CASES]
    if not source_items:
        print(f"[OQ-EVAL] No source checkpoints found under {SOURCE_EVALUATION_OUTPUT_DIR / 'checkpoints'}", flush=True)
        return

    total_cases = len(source_items)
    strategy_names = sorted({strategy for strategy, _ in source_items})
    total_strategies = len(strategy_names)
    strategy_order = {name: idx for idx, name in enumerate(strategy_names, start=1)}
    results: list[dict[str, Any]] = []

    for case_index, (strategy_name, source_path) in enumerate(source_items, start=1):
        source_checkpoint = _extract_source_case(source_path)
        if source_checkpoint is None:
            continue
        source_case = _build_eval_case(source_checkpoint)
        case_id = str((source_case or {}).get("case_id", "")).strip() or source_path.stem
        if not _should_keep_case(case_id):
            continue

        checkpoint_path = _checkpoint_path(strategy_name, case_id)
        checkpoint = _load_checkpoint(checkpoint_path) if RESUME_FROM_CHECKPOINTS else None
        if checkpoint is None:
            checkpoint = {
                "case_id": case_id,
                "source_strategy": strategy_name,
                "source_checkpoint_path": str(source_path),
                "status": "pending",
                "last_step": "",
                "started_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
                "completed_at": None,
                "error": None,
                "stages": {},
                "case_result": _build_case_result(source_checkpoint, strategy_name),
            }

        case_result = checkpoint.get("case_result")
        if not isinstance(case_result, dict):
            case_result = _build_case_result(source_checkpoint, strategy_name)
            checkpoint["case_result"] = case_result

        if RESUME_FROM_CHECKPOINTS and SKIP_COMPLETED_CHECKPOINTS and checkpoint.get("status") == "completed":
            results.append(case_result)
            _log_progress(
                step_label="skipping completed checkpoint",
                strategy_name=strategy_name,
                case_id=case_id,
                case_index=case_index,
                total_cases=total_cases,
                strategy_index=strategy_order[strategy_name],
                total_strategies=total_strategies,
            )
            continue

        generation = ((source_case.get("generation") or {}) if isinstance(source_case, dict) else {})
        iis = ((generation.get("iis") or {}) if isinstance(generation, dict) else {})
        open_questions = iis.get("open_questions") or []
        if not isinstance(open_questions, list):
            open_questions = []
        case_result["generated_open_question_count"] = len(open_questions)

        checkpoint["status"] = "running"
        checkpoint["error"] = None
        checkpoint["last_step"] = "starting"
        _save_checkpoint(checkpoint_path, checkpoint)

        if not open_questions:
            checkpoint["status"] = "completed"
            checkpoint["completed_at"] = _utc_now_iso()
            checkpoint["last_step"] = "completed"
            case_result["open_question_evaluation"] = {
                "scores": {
                    "necessity": None,
                    "specificity": None,
                    "impact_on_downstream": None,
                    "non_redundancy": None,
                    "missing_critical_gap_detection": None,
                    "overall": None,
                },
                "dimension_rationales": {
                    "necessity": "No open questions were generated.",
                    "specificity": "No open questions were generated.",
                    "impact_on_downstream": "No open questions were generated.",
                    "non_redundancy": "No open questions were generated.",
                    "missing_critical_gap_detection": "No open questions were generated.",
                    "overall": "No open questions were generated.",
                },
                "strengths": [],
                "gaps": ["No open questions were generated."],
                "summary": "No open questions were available for evaluation.",
            }
            _save_checkpoint(checkpoint_path, checkpoint)
            results.append(case_result)
            _write_outputs(results)
            continue

        case_started_at = time.perf_counter()
        try:
            checkpoint["last_step"] = "open_question_evaluation"
            _log_progress(
                step_label="scoring generated open questions",
                strategy_name=strategy_name,
                case_id=case_id,
                case_index=case_index,
                total_cases=total_cases,
                strategy_index=strategy_order[strategy_name],
                total_strategies=total_strategies,
            )
            case_result["open_question_evaluation"] = evaluate_open_questions_case(
                case=source_case,
                candidate_label="generated_open_questions",
                upstream_iis_text=str(iis.get("raw_text", "")).strip(),
                open_questions=open_questions,
                app_config=app_config,
            )
            _mark_stage(checkpoint, "open_question_evaluation", "completed")
            checkpoint["status"] = "completed"
            checkpoint["completed_at"] = _utc_now_iso()
            checkpoint["last_step"] = "write_outputs"
            _save_checkpoint(checkpoint_path, checkpoint)

            results.append(case_result)
            _write_outputs(results)
            checkpoint["last_step"] = "completed"
            _save_checkpoint(checkpoint_path, checkpoint)

            elapsed_seconds = time.perf_counter() - case_started_at
            _log_progress(
                step_label=f"finished case in {elapsed_seconds:.1f}s",
                strategy_name=strategy_name,
                case_id=case_id,
                case_index=case_index,
                total_cases=total_cases,
                strategy_index=strategy_order[strategy_name],
                total_strategies=total_strategies,
            )
        except Exception as exc:
            checkpoint["status"] = _classify_failure_status(checkpoint)
            checkpoint["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "last_step": checkpoint.get("last_step", ""),
                "failed_at": _utc_now_iso(),
            }
            _save_checkpoint(checkpoint_path, checkpoint)
            _write_outputs(results)
            _log_progress(
                step_label=f"failed at {checkpoint.get('last_step', 'unknown_step')}: {type(exc).__name__}: {exc}",
                strategy_name=strategy_name,
                case_id=case_id,
                case_index=case_index,
                total_cases=total_cases,
                strategy_index=strategy_order[strategy_name],
                total_strategies=total_strategies,
            )
            if CONTINUE_AFTER_CASE_ERROR:
                continue
            raise

    print("[OQ-EVAL] Aggregating results and writing output files", flush=True)
    _write_outputs(results)
    print(f"[OQ-EVAL] Wrote results to {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
