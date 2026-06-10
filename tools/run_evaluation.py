from __future__ import annotations

"""
Run end-to-end IIS and Software Requirements evaluation on a prepared dataset.

Edit the configuration block at the top of this file, then run:

    python tools/run_evaluation.py

This script can:
- generate IIS and Software Requirements from Epic descriptions
- compare them against historical references
- ask an LLM judge to score multiple quality dimensions
- aggregate scores by task type and difficulty
- export detailed JSON and a Markdown summary
"""

import json
import time

from evaluation_utils import (
    PROJECT_ROOT,
    apply_retrieval_strategy_override,
    aggregate_scores,
    evaluate_iis_case,
    evaluate_retrieval_case,
    evaluate_software_requirements_case,
    load_eval_config,
    load_json,
    render_markdown_summary,
    run_generation_pipeline,
    write_json,
    write_markdown,
)


# Configuration - edit these values directly before running.
DATASET_PATH = PROJECT_ROOT / "tools" / "evaluation_dataset.example.json"
OUTPUT_DIR = PROJECT_ROOT / "tools" / "evaluation_outputs"
RUN_GENERATION = True
EVALUATE_IIS = True
EVALUATE_SOFTWARE_REQUIREMENTS = True
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


def _should_keep_case(case: dict[str, object]) -> bool:
    case_id = str(case.get("case_id", ""))
    if CASE_IDS and case_id not in CASE_IDS:
        return False
    return True


def _log_eval_progress(
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
        f"[EVAL][strategy {strategy_index}/{total_strategies}={strategy_name}]"
        f"[case {case_index}/{total_cases}={case_id}] {step_label}",
        flush=True,
    )


def _is_enabled(experiment: dict[str, object]) -> bool:
    value = experiment.get("enabled", True)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    app_config = load_eval_config()
    dataset = load_json(DATASET_PATH)
    if not isinstance(dataset, list):
        raise TypeError("Evaluation dataset must be a JSON array of case objects.")

    cases = [case for case in dataset if isinstance(case, dict) and _should_keep_case(case)]
    if MAX_CASES is not None:
        cases = cases[:MAX_CASES]

    results: list[dict[str, object]] = []
    total_cases = len(cases)
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

    total_strategies = len(strategy_matrix)

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
            case_id = str(case.get("case_id", "unknown-case"))
            case_started_at = time.perf_counter()
            _log_eval_progress(
                step_label=f"starting case (ranking={ranking_name}, file_agg={file_aggregation_name})",
                strategy_name=strategy_name,
                case_id=case_id,
                case_index=case_index,
                total_cases=total_cases,
                strategy_index=strategy_index,
                total_strategies=total_strategies,
            )

            if RUN_GENERATION:
                _log_eval_progress(
                    step_label="running generation pipeline (retrieval intent -> evidence -> IIS -> software requirements)",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                generation = run_generation_pipeline(str(case.get("description", "")), strategy_app_config)
            else:
                _log_eval_progress(
                    step_label="using pre-generated artifacts from dataset",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                generation = {
                    "retrieval_intent": case.get("generated_retrieval_intent"),
                    "evidence": case.get("generated_evidence"),
                    "iis": case.get("generated_iis"),
                    "software_requirements": case.get("generated_software_requirements"),
                }

            generated_iis_text = str((generation.get("iis") or {}).get("raw_text", "")).strip()
            generated_software_requirements_text = str(
                (generation.get("software_requirements") or {}).get("raw_text", "")
            ).strip()

            case_result: dict[str, object] = {
                "case_id": case_id,
                "retrieval_strategy": strategy_name,
                "ranking_strategy": ranking_name,
                "file_aggregation_strategy": file_aggregation_name,
                "task_type": case.get("task_type", ""),
                "difficulty": case.get("difficulty", ""),
                "generation": generation,
                "retrieval_metrics": evaluate_retrieval_case(
                    case=case,
                    generation=generation,
                ),
            }
            _log_eval_progress(
                step_label="computed retrieval metrics",
                strategy_name=strategy_name,
                case_id=case_id,
                case_index=case_index,
                total_cases=total_cases,
                strategy_index=strategy_index,
                total_strategies=total_strategies,
            )

            if EVALUATE_IIS and generated_iis_text:
                _log_eval_progress(
                    step_label="scoring generated IIS",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                case_result["iis_evaluation"] = evaluate_iis_case(
                    case=case,
                    candidate_label="generated_iis",
                    candidate_iis_text=generated_iis_text,
                    candidate_evidence=generation.get("evidence"),
                    app_config=strategy_app_config,
                )
            historical_what_to_do = str(case.get("historical_what_to_do", "")).strip()
            if EVALUATE_IIS and historical_what_to_do:
                _log_eval_progress(
                    step_label="scoring historical what-to-do baseline",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                case_result["historical_iis_baseline_evaluation"] = evaluate_iis_case(
                    case=case,
                    candidate_label="historical_what_to_do_baseline",
                    candidate_iis_text=historical_what_to_do,
                    candidate_evidence=None,
                    app_config=strategy_app_config,
                )

            if EVALUATE_SOFTWARE_REQUIREMENTS and generated_software_requirements_text:
                _log_eval_progress(
                    step_label="scoring generated software requirements",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                case_result["software_requirements_evaluation"] = evaluate_software_requirements_case(
                    case=case,
                    confirmed_iis_text=generated_iis_text,
                    candidate_label="generated_software_requirements",
                    candidate_software_requirements_text=generated_software_requirements_text,
                    app_config=strategy_app_config,
                )
            historical_software_requirements = case.get("historical_software_requirements")
            historical_software_requirements_text = ""
            if historical_software_requirements:
                historical_software_requirements_text = "\n".join(
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
            if EVALUATE_SOFTWARE_REQUIREMENTS and historical_software_requirements_text:
                _log_eval_progress(
                    step_label="scoring historical software requirements baseline",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                case_result["historical_software_requirements_baseline_evaluation"] = evaluate_software_requirements_case(
                    case=case,
                    confirmed_iis_text=generated_iis_text,
                    candidate_label="historical_software_requirements_baseline",
                    candidate_software_requirements_text=historical_software_requirements_text,
                    app_config=strategy_app_config,
                )

            results.append(case_result)
            elapsed_seconds = time.perf_counter() - case_started_at
            _log_eval_progress(
                step_label=f"finished case in {elapsed_seconds:.1f}s",
                strategy_name=strategy_name,
                case_id=case_id,
                case_index=case_index,
                total_cases=total_cases,
                strategy_index=strategy_index,
                total_strategies=total_strategies,
            )

    print("[EVAL] Aggregating results and writing output files", flush=True)
    aggregate = aggregate_scores(results)

    write_json(OUTPUT_DIR / "evaluation_results.json", results)
    write_json(OUTPUT_DIR / "evaluation_aggregate.json", aggregate)
    write_markdown(OUTPUT_DIR / "evaluation_summary.md", render_markdown_summary(results, aggregate))

    print(f"[EVAL] Wrote results to {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
