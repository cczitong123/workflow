from __future__ import annotations

"""
Run retrieval-only evaluation on a prepared dataset.

This script evaluates only the code retrieval chain:

    description -> retrieval intent -> top-k code evidence

It compares retrieved file paths against historical ground-truth changed files
and writes standalone retrieval reports without changing the main
`tools/run_evaluation.py` pipeline.
"""

import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation_utils import (
    PROJECT_ROOT,
    apply_retrieval_strategy_override,
    evaluate_retrieval_case,
    generate_retrieval_intent_payload,
    get_case_description,
    get_case_difficulty,
    get_case_historical_changed_files,
    get_case_id,
    get_case_task_type,
    load_eval_config,
    load_json,
    normalize_path_for_eval,
    retrieve_evidence_payload,
    write_json,
    write_markdown,
)


DATASET_PATH = PROJECT_ROOT / "tools" / "evaluation_dataset.example.json"
OUTPUT_DIR = PROJECT_ROOT / "tools" / "retrieval_evaluation_outputs"
RESUME_FROM_CHECKPOINTS = True
SKIP_COMPLETED_CHECKPOINTS = True
CONTINUE_AFTER_CASE_ERROR = True
CASE_IDS: list[str] = []
MAX_CASES: int | None = None
RANKING_STRATEGY_EXPERIMENTS = [
    {
        "enabled": True,
        "name": "semantic_only",
        "ranking_mode": "semantic_only",
    },
    {
        "enabled": True,
        "name": "filename_only",
        "ranking_mode": "filename_only",
    },
    {
        "enabled": True,
        "name": "weighted_85_15",
        "ranking_mode": "weight",
        "ranking_alpha": 0.85,
        "ranking_beta": 0.15,
    },
    {
        "enabled": True,
        "name": "weighted_70_30",
        "ranking_mode": "weight",
        "ranking_alpha": 0.7,
        "ranking_beta": 0.3,
    },
    {
        "enabled": True,
        "name": "weighted_50_50",
        "ranking_mode": "weight",
        "ranking_alpha": 0.5,
        "ranking_beta": 0.5,
    },
]
FILE_AGGREGATION_STRATEGY_EXPERIMENTS = [
    {
        "enabled": True,
        "name": "max_only",
        "file_aggregation_strategy": "max_only",
    },
    {
        "enabled": True,
        "name": "max_plus_second",
        "file_aggregation_strategy": "max_plus_second",
        "file_aggregation_alpha": 0.25,
    },
    {
        "enabled": True,
        "name": "max_plus_log_count",
        "file_aggregation_strategy": "max_plus_log_count",
        "file_aggregation_beta": 0.05,
    },
    {
        "enabled": True,
        "name": "max_plus_second_plus_log_count",
        "file_aggregation_strategy": "max_plus_second_plus_log_count",
        "file_aggregation_alpha": 0.25,
        "file_aggregation_beta": 0.05,
    },
    {
        "enabled": True,
        "name": "sum_all",
        "file_aggregation_strategy": "sum_all",
    },
]
CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return slug.strip("_") or "unnamed"


def _description_preview(text: str, limit: int = 120) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _resolve_case_id(case: dict[str, object]) -> str:
    explicit_id = get_case_id(case)
    if explicit_id:
        return explicit_id
    description = " ".join(get_case_description(case).split()).strip()
    if description:
        digest = hashlib.sha1(description.encode("utf-8")).hexdigest()[:10].upper()
        return f"CASE-{digest}"
    return "CASE-NO-DESCRIPTION"


def _should_keep_case(case: dict[str, object]) -> bool:
    case_id = _resolve_case_id(case)
    if CASE_IDS and case_id not in CASE_IDS:
        return False
    return True


def _is_enabled(experiment: dict[str, object]) -> bool:
    value = experiment.get("enabled", True)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
        f"[RETRIEVAL-EVAL][strategy {strategy_index}/{total_strategies}={strategy_name}]"
        f"[case {case_index}/{total_cases}={case_id}] {step_label}",
        flush=True,
    )


def _log_checkpoint_path(path: Path) -> None:
    print(f"[RETRIEVAL-EVAL][checkpoint] {path}", flush=True)


def _checkpoint_path(strategy_name: str, case_id: str) -> Path:
    return CHECKPOINT_DIR / _slugify(strategy_name) / f"{_slugify(case_id)}.json"


def _load_checkpoint(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    payload = load_json(path)
    return payload if isinstance(payload, dict) else None


def _save_checkpoint(path: Path, checkpoint: dict[str, object]) -> None:
    checkpoint["updated_at"] = _utc_now_iso()
    write_json(path, checkpoint)


def _mark_stage(checkpoint: dict[str, object], *, stage_name: str, status: str) -> None:
    stages = checkpoint.setdefault("stages", {})
    if not isinstance(stages, dict):
        stages = {}
        checkpoint["stages"] = stages
    stages[stage_name] = {
        "status": status,
        "updated_at": _utc_now_iso(),
    }


def _compute_gt_path_breakdown(case: dict[str, object], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    gt_paths_original = [str(path).strip() for path in get_case_historical_changed_files(case) if str(path).strip()]
    gt_by_normalized = {
        normalize_path_for_eval(path): path
        for path in gt_paths_original
        if normalize_path_for_eval(path)
    }

    retrieved_paths_original = [
        str(item.get("path", "")).strip()
        for item in evidence
        if str(item.get("path", "")).strip()
    ]
    retrieved_by_normalized: dict[str, str] = {}
    for path in retrieved_paths_original:
        normalized = normalize_path_for_eval(path)
        if normalized and normalized not in retrieved_by_normalized:
            retrieved_by_normalized[normalized] = path

    matched_normalized = [
        normalized
        for normalized in gt_by_normalized
        if normalized in retrieved_by_normalized
    ]
    missed_normalized = [
        normalized
        for normalized in gt_by_normalized
        if normalized not in retrieved_by_normalized
    ]

    return {
        "gt_paths": gt_paths_original,
        "retrieved_paths": retrieved_paths_original,
        "matched_gt_paths": [gt_by_normalized[item] for item in matched_normalized],
        "matched_retrieved_paths": [retrieved_by_normalized[item] for item in matched_normalized],
        "missed_gt_paths": [gt_by_normalized[item] for item in missed_normalized],
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_strategy: dict[str, dict[str, list[float]]] = {}
    by_ranking_strategy: dict[str, dict[str, list[float]]] = {}
    by_file_aggregation_strategy: dict[str, dict[str, list[float]]] = {}

    metric_names = [
        "recall_at_10",
        "recall_at_20",
        "precision_at_10",
        "precision_at_20",
        "mrr",
        "matched_changed_file_count",
        "historical_changed_file_count",
        "retrieved_file_count",
    ]

    def ensure_bucket(container: dict[str, dict[str, list[float]]], key: str) -> dict[str, list[float]]:
        if key not in container:
            container[key] = {metric: [] for metric in metric_names}
        return container[key]

    for result in results:
        metrics = result.get("retrieval_metrics", {})
        if not isinstance(metrics, dict):
            continue
        buckets = [
            ensure_bucket(by_strategy, str(result.get("retrieval_strategy", "default"))),
            ensure_bucket(by_ranking_strategy, str(result.get("ranking_strategy", "default"))),
            ensure_bucket(
                by_file_aggregation_strategy,
                str(result.get("file_aggregation_strategy", "default")),
            ),
        ]
        for metric in metric_names:
            value = metrics.get(metric)
            if not isinstance(value, (int, float)):
                continue
            for bucket in buckets:
                bucket[metric].append(float(value))

    def collapse(container: dict[str, dict[str, list[float]]]) -> dict[str, dict[str, float | None]]:
        return {
            key: {metric: _mean(values) for metric, values in metrics.items()}
            for key, metrics in container.items()
        }

    return {
        "by_strategy": collapse(by_strategy),
        "by_ranking_strategy": collapse(by_ranking_strategy),
        "by_file_aggregation_strategy": collapse(by_file_aggregation_strategy),
    }


def _render_metrics_table(metrics: dict[str, float | None]) -> list[str]:
    lines = [
        "| Metric | Mean Value |",
        "| --- | ---: |",
    ]
    for metric_name, value in metrics.items():
        rendered = f"{value:.3f}" if isinstance(value, (int, float)) else ""
        lines.append(f"| {metric_name} | {rendered} |")
    return lines


def _render_markdown_summary(results: list[dict[str, Any]], aggregate: dict[str, Any]) -> str:
    lines = ["# Retrieval Evaluation Summary", ""]

    lines.extend(
        [
            "## Case Reference",
            "",
            "| Case ID | Case Preview | Task Type | Difficulty |",
            "| --- | --- | --- | --- |",
        ]
    )
    seen_case_ids: set[str] = set()
    for result in results:
        case_id = str(result.get("case_id", "")).strip()
        if not case_id or case_id in seen_case_ids:
            continue
        seen_case_ids.add(case_id)
        lines.append(
            f"| {case_id} | {str(result.get('description_preview', ''))} | "
            f"{str(result.get('task_type', ''))} | {str(result.get('difficulty', ''))} |"
        )
    lines.extend(["", "## Aggregate Results by Retrieval Strategy", ""])
    for strategy, metrics in aggregate.get("by_strategy", {}).items():
        lines.extend([f"### strategy=`{strategy}`", ""])
        lines.extend(_render_metrics_table(metrics))
        lines.append("")

    lines.extend(["## Aggregate Results by Ranking Strategy", ""])
    for strategy, metrics in aggregate.get("by_ranking_strategy", {}).items():
        lines.extend([f"### ranking_strategy=`{strategy}`", ""])
        lines.extend(_render_metrics_table(metrics))
        lines.append("")

    lines.extend(["## Aggregate Results by File Aggregation Strategy", ""])
    for strategy, metrics in aggregate.get("by_file_aggregation_strategy", {}).items():
        lines.extend([f"### file_aggregation_strategy=`{strategy}`", ""])
        lines.extend(_render_metrics_table(metrics))
        lines.append("")

    lines.extend(
        [
            "## Per-Case Overview",
            "",
            "| Case ID | Ranking Strategy | File Aggregation | Recall@10 | Recall@20 | MRR | Matched GT Files | Missed GT Files |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in results:
        metrics = result.get("retrieval_metrics", {}) if isinstance(result.get("retrieval_metrics"), dict) else {}
        gt = result.get("gt_comparison", {}) if isinstance(result.get("gt_comparison"), dict) else {}
        lines.append(
            f"| {result.get('case_id', '')} | {result.get('ranking_strategy', '')} | "
            f"{result.get('file_aggregation_strategy', '')} | "
            f"{metrics.get('recall_at_10', '') if metrics.get('recall_at_10') is not None else ''} | "
            f"{metrics.get('recall_at_20', '') if metrics.get('recall_at_20') is not None else ''} | "
            f"{metrics.get('mrr', '') if metrics.get('mrr') is not None else ''} | "
            f"{len(gt.get('matched_gt_paths', [])) if isinstance(gt.get('matched_gt_paths'), list) else 0} | "
            f"{len(gt.get('missed_gt_paths', [])) if isinstance(gt.get('missed_gt_paths'), list) else 0} |"
        )

    lines.extend(["", "## Detailed Retrieval Results", ""])
    for result in results:
        metrics = result.get("retrieval_metrics", {}) if isinstance(result.get("retrieval_metrics"), dict) else {}
        gt = result.get("gt_comparison", {}) if isinstance(result.get("gt_comparison"), dict) else {}
        retrieval_intent = result.get("generation", {}).get("retrieval_intent", {})
        evidence = result.get("generation", {}).get("evidence", [])
        lines.extend(
            [
                f"### {result.get('case_id', '')} · {result.get('retrieval_strategy', '')}",
                "",
                f"- Case Preview: {result.get('description_preview', '')}",
                f"- Ranking Strategy: `{result.get('ranking_strategy', '')}`",
                f"- File Aggregation Strategy: `{result.get('file_aggregation_strategy', '')}`",
                f"- Recall@10: {metrics.get('recall_at_10', 'n/a')}",
                f"- Recall@20: {metrics.get('recall_at_20', 'n/a')}",
                f"- MRR: {metrics.get('mrr', 'n/a')}",
                "",
                "#### Retrieval Intent",
                "",
                f"- Summary: {retrieval_intent.get('summary', '')}",
                f"- Technical Intent: {retrieval_intent.get('technical_intent', '')}",
                f"- Keywords: {', '.join(retrieval_intent.get('keywords', []))}",
                "",
                "#### Ground Truth Files",
                "",
            ]
        )
        for path in gt.get("gt_paths", []):
            lines.append(f"- {path}")
        if not gt.get("gt_paths"):
            lines.append("- None provided.")
        lines.extend(["", "#### Retrieved Files", ""])
        for item in evidence:
            path = str(item.get("path", "")).strip()
            score = item.get("score")
            if path:
                suffix = f" (score={score:.3f})" if isinstance(score, (int, float)) else ""
                lines.append(f"- {path}{suffix}")
        if not evidence:
            lines.append("- None retrieved.")
        lines.extend(["", "#### Matched GT Files", ""])
        for path in gt.get("matched_gt_paths", []):
            lines.append(f"- {path}")
        if not gt.get("matched_gt_paths"):
            lines.append("- None.")
        lines.extend(["", "#### Missed GT Files", ""])
        for path in gt.get("missed_gt_paths", []):
            lines.append(f"- {path}")
        if not gt.get("missed_gt_paths"):
            lines.append("- None.")
        lines.append("")

    return "\n".join(lines)


def _write_outputs(results: list[dict[str, Any]]) -> None:
    aggregate = _aggregate_results(results)
    write_json(OUTPUT_DIR / "retrieval_results.json", results)
    write_json(OUTPUT_DIR / "retrieval_aggregate.json", aggregate)
    write_markdown(OUTPUT_DIR / "retrieval_summary.md", _render_markdown_summary(results, aggregate))


def _build_base_case_result(
    case: dict[str, object],
    strategy_name: str,
    ranking_name: str,
    file_aggregation_name: str,
) -> dict[str, Any]:
    case_id = _resolve_case_id(case)
    description_preview = _description_preview(get_case_description(case))
    return {
        "case_id": case_id,
        "case_label": f"{case_id} | {description_preview}" if description_preview else case_id,
        "description_preview": description_preview,
        "retrieval_strategy": strategy_name,
        "ranking_strategy": ranking_name,
        "file_aggregation_strategy": file_aggregation_name,
        "task_type": get_case_task_type(case),
        "difficulty": get_case_difficulty(case),
        "generation": {},
    }


def _build_base_checkpoint(
    *,
    case: dict[str, object],
    strategy_name: str,
    ranking_name: str,
    file_aggregation_name: str,
) -> dict[str, Any]:
    return {
        "case_id": _resolve_case_id(case),
        "strategy_name": strategy_name,
        "ranking_strategy": ranking_name,
        "file_aggregation_strategy": file_aggregation_name,
        "status": "pending",
        "last_step": "",
        "started_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "completed_at": None,
        "error": None,
        "stages": {},
        "case_result": _build_base_case_result(case, strategy_name, ranking_name, file_aggregation_name),
    }


def main() -> None:
    app_config = load_eval_config()
    dataset = load_json(DATASET_PATH)
    if not isinstance(dataset, list):
        raise TypeError("Retrieval evaluation dataset must be a JSON array of case objects.")

    cases = [case for case in dataset if isinstance(case, dict) and _should_keep_case(case)]
    if MAX_CASES is not None:
        cases = cases[:MAX_CASES]

    enabled_ranking_experiments = [
        config for config in RANKING_STRATEGY_EXPERIMENTS if _is_enabled(config)
    ]
    enabled_file_aggregation_experiments = [
        config for config in FILE_AGGREGATION_STRATEGY_EXPERIMENTS if _is_enabled(config)
    ]
    if not enabled_ranking_experiments:
        raise ValueError("No ranking strategy experiments are enabled.")
    if not enabled_file_aggregation_experiments:
        raise ValueError("No file aggregation strategy experiments are enabled.")

    strategy_matrix = []
    for ranking_config in enabled_ranking_experiments:
        for aggregation_config in enabled_file_aggregation_experiments:
            ranking_name = str(ranking_config.get("name") or ranking_config.get("ranking_mode") or "unnamed")
            aggregation_name = str(
                aggregation_config.get("name")
                or aggregation_config.get("file_aggregation_strategy")
                or "unnamed"
            )
            strategy_matrix.append(
                {
                    "name": f"{ranking_name}__{aggregation_name}",
                    "ranking_name": ranking_name,
                    "file_aggregation_name": aggregation_name,
                    "ranking_mode": str(ranking_config.get("ranking_mode", "semantic_only")),
                    "ranking_alpha": ranking_config.get("ranking_alpha"),
                    "ranking_beta": ranking_config.get("ranking_beta"),
                    "file_aggregation_strategy": str(
                        aggregation_config.get("file_aggregation_strategy", "max_only")
                    ),
                    "file_aggregation_alpha": aggregation_config.get("file_aggregation_alpha"),
                    "file_aggregation_beta": aggregation_config.get("file_aggregation_beta"),
                    "candidate_multiplier": aggregation_config.get("candidate_multiplier"),
                }
            )

    total_cases = len(cases)
    total_strategies = len(strategy_matrix)
    results: list[dict[str, Any]] = []

    for strategy_index, strategy_config in enumerate(strategy_matrix, start=1):
        strategy_name = str(strategy_config.get("name", "unnamed"))
        ranking_name = str(strategy_config.get("ranking_name", "unknown"))
        file_aggregation_name = str(strategy_config.get("file_aggregation_name", "unknown"))
        strategy_app_config = apply_retrieval_strategy_override(
            app_config,
            ranking_mode=strategy_config.get("ranking_mode"),
            ranking_alpha=strategy_config.get("ranking_alpha"),
            ranking_beta=strategy_config.get("ranking_beta"),
            file_aggregation_strategy=strategy_config.get("file_aggregation_strategy"),
            file_aggregation_alpha=strategy_config.get("file_aggregation_alpha"),
            file_aggregation_beta=strategy_config.get("file_aggregation_beta"),
            candidate_multiplier=strategy_config.get("candidate_multiplier"),
        )

        for case_index, case in enumerate(cases, start=1):
            case_id = _resolve_case_id(case)
            case_started_at = time.perf_counter()
            checkpoint_path = _checkpoint_path(strategy_name, case_id)
            checkpoint = _load_checkpoint(checkpoint_path) if RESUME_FROM_CHECKPOINTS else None
            if checkpoint is None:
                checkpoint = _build_base_checkpoint(
                    case=case,
                    strategy_name=strategy_name,
                    ranking_name=ranking_name,
                    file_aggregation_name=file_aggregation_name,
                )
            case_result = checkpoint.get("case_result")
            if not isinstance(case_result, dict):
                case_result = _build_base_case_result(case, strategy_name, ranking_name, file_aggregation_name)
                checkpoint["case_result"] = case_result

            if (
                RESUME_FROM_CHECKPOINTS
                and SKIP_COMPLETED_CHECKPOINTS
                and checkpoint.get("status") == "completed"
            ):
                results.append(case_result)
                _log_progress(
                    step_label="skipping completed checkpoint",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                continue

            checkpoint["status"] = "running"
            checkpoint["error"] = None
            checkpoint["last_step"] = "starting"
            _save_checkpoint(checkpoint_path, checkpoint)
            _log_checkpoint_path(checkpoint_path)
            _log_progress(
                step_label=f"starting case (ranking={ranking_name}, file_agg={file_aggregation_name})",
                strategy_name=strategy_name,
                case_id=case_id,
                case_index=case_index,
                total_cases=total_cases,
                strategy_index=strategy_index,
                total_strategies=total_strategies,
            )

            try:
                generation = case_result.setdefault("generation", {})
                if not generation.get("retrieval_intent"):
                    checkpoint["last_step"] = "retrieval_intent"
                    _log_progress(
                        step_label="generating retrieval intent",
                        strategy_name=strategy_name,
                        case_id=case_id,
                        case_index=case_index,
                        total_cases=total_cases,
                        strategy_index=strategy_index,
                        total_strategies=total_strategies,
                    )
                    generation["retrieval_intent"] = generate_retrieval_intent_payload(
                        get_case_description(case),
                        strategy_app_config,
                    )
                    _mark_stage(checkpoint, stage_name="retrieval_intent", status="completed")
                    _save_checkpoint(checkpoint_path, checkpoint)

                if not generation.get("evidence"):
                    checkpoint["last_step"] = "evidence"
                    _log_progress(
                        step_label="retrieving top-k code evidence",
                        strategy_name=strategy_name,
                        case_id=case_id,
                        case_index=case_index,
                        total_cases=total_cases,
                        strategy_index=strategy_index,
                        total_strategies=total_strategies,
                    )
                    generation["evidence"] = retrieve_evidence_payload(
                        generation["retrieval_intent"],
                        strategy_app_config,
                    )
                    _mark_stage(checkpoint, stage_name="evidence", status="completed")
                    _save_checkpoint(checkpoint_path, checkpoint)

                checkpoint["last_step"] = "retrieval_metrics"
                case_result["retrieval_metrics"] = evaluate_retrieval_case(
                    case=case,
                    generation=generation,
                )
                case_result["gt_comparison"] = _compute_gt_path_breakdown(
                    case,
                    generation.get("evidence") or [],
                )
                _mark_stage(checkpoint, stage_name="retrieval_metrics", status="completed")
                checkpoint["status"] = "completed"
                checkpoint["completed_at"] = _utc_now_iso()
                checkpoint["last_step"] = "completed"
                _save_checkpoint(checkpoint_path, checkpoint)

                results.append(case_result)
                _write_outputs(results)

                elapsed_seconds = time.perf_counter() - case_started_at
                _log_progress(
                    step_label=f"finished case in {elapsed_seconds:.1f}s",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
            except Exception as exc:
                checkpoint["status"] = "failed"
                checkpoint["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "last_step": checkpoint.get("last_step", ""),
                    "failed_at": _utc_now_iso(),
                }
                _save_checkpoint(checkpoint_path, checkpoint)
                _log_progress(
                    step_label=f"failed at {checkpoint.get('last_step', 'unknown_step')}: {type(exc).__name__}: {exc}",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                _log_checkpoint_path(checkpoint_path)
                if CONTINUE_AFTER_CASE_ERROR:
                    continue
                raise

    print("[RETRIEVAL-EVAL] Aggregating results and writing output files", flush=True)
    _write_outputs(results)
    print(f"[RETRIEVAL-EVAL] Wrote results to {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
