from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from config import CodeRagConfig
from modules.shared.models import EvidenceItem, RetrievalIntent


def _log(message: str) -> None:
    print(f"[AGENTIC-WORKFLOW][RAG] {message}", flush=True)


def retrieve_code_evidence(
    intent: RetrievalIntent,
    config: CodeRagConfig,
) -> list[EvidenceItem]:
    mode = config.mode.lower().strip()
    _log(f"retrieve_code_evidence mode={mode} top_k={config.top_k}")
    if mode == "mock":
        return _retrieve_mock_evidence(intent, config.top_k)
    if mode == "local":
        return _retrieve_local_evidence(intent, config)
    if mode == "remote":
        return _retrieve_remote_evidence(intent, config)
    raise NotImplementedError(
        f"Code RAG mode '{config.mode}' is not implemented yet. "
        "Update modules/integrations/code_rag_adapter.py."
    )


def _retrieve_mock_evidence(intent: RetrievalIntent, top_k: int) -> list[EvidenceItem]:
    base_reason = "Placeholder evidence until the real code RAG adapter is connected."
    items = [
        EvidenceItem(
            id="ev-1",
            path="src/feature/handler.cpp",
            chunk_type="function",
            symbol="HandleFeatureRequest",
            snippet="if (featureFlagEnabled && request.IsValid()) { ... }",
            score=0.91,
            why_relevant=f"{base_reason} Likely handler for the request path described in the epic.",
            suggested_change="Update the branch handling to account for the new condition and preserve fallback behavior.",
            location_hint="HandleFeatureRequest",
        ),
        EvidenceItem(
            id="ev-2",
            path="include/feature/config.h",
            chunk_type="file",
            symbol=None,
            snippet="struct FeatureConfig { bool featureFlagEnabled; };",
            score=0.83,
            why_relevant=f"{base_reason} Configuration surface may need extension for the new behavior.",
            suggested_change="Add or update configuration flags that gate the new logic path.",
            location_hint=None,
        ),
        EvidenceItem(
            id="ev-3",
            path="tests/feature/handler_test.cpp",
            chunk_type="window",
            symbol="FeatureHandlerTest",
            snippet="TEST_F(FeatureHandlerTest, KeepsCurrentBehaviorWhenDisabled) { ... }",
            score=0.79,
            why_relevant=f"{base_reason} Existing tests likely cover the current behavior that must remain stable.",
            suggested_change="Add coverage for the new branch while keeping regression coverage for the legacy case.",
            location_hint="FeatureHandlerTest",
        ),
    ]
    return items[:top_k]


def _retrieve_local_evidence(
    intent: RetrievalIntent,
    config: CodeRagConfig,
) -> list[EvidenceItem]:
    _log("Loading local RAG state")
    state = _get_local_rag_state(_state_cache_key(config))
    query = _build_query_from_intent(intent)
    _log(f"Local query built. chars={len(query)} ranking_mode={state['ranking_mode']}")
    query_vec = _embed_text(query, state)
    _log(f"Query embedding ready. dim={len(query_vec)}")
    raw_results = _search_local_index(
        query_vec,
        state,
        query,
        top_k=config.top_k,
        candidate_multiplier=config.file_aggregation_candidate_multiplier,
    )
    _log(f"Local chunk retrieval completed. raw_results={len(raw_results)}")
    results = _aggregate_results_by_file(raw_results, config)
    _log(
        "Local file-level aggregation completed. "
        f"strategy={config.file_aggregation_strategy} results={len(results)}"
    )
    return [_result_to_evidence(result, intent) for result in results]


def _retrieve_remote_evidence(
    intent: RetrievalIntent,
    config: CodeRagConfig,
) -> list[EvidenceItem]:
    raise NotImplementedError(
        "Remote Code RAG is selected, but the remote HTTP client is not implemented yet. "
        "Use config.code_rag.endpoint and auth fields in this file."
    )


def _build_query_from_intent(intent: RetrievalIntent) -> str:
    if intent.query.strip():
        return intent.query.strip()
    pieces = [intent.summary, intent.technical_intent]
    if intent.keywords:
        pieces.append("keywords: " + ", ".join(intent.keywords))
    if intent.suspected_areas:
        pieces.append("suspected areas: " + ", ".join(intent.suspected_areas))
    return "\n".join(piece for piece in pieces if piece.strip())


def _result_to_evidence(result: dict[str, Any], intent: RetrievalIntent) -> EvidenceItem:
    location_bits = []
    if result.get("name"):
        location_bits.append(str(result["name"]))
    if result.get("start_line") and result.get("end_line"):
        location_bits.append(f"lines {result['start_line']}-{result['end_line']}")
    location_hint = ", ".join(location_bits) if location_bits else None
    snippet = (
        f"type={result.get('type')}, chunk={result.get('chunk_index')}, "
        f"lines={result.get('start_line')}-{result.get('end_line')}"
    )
    matched_chunk_count = int(result.get("matched_chunk_count", 1))
    file_score = float(result.get("file_score", result.get("final_score", result.get("similarity", 0.0))))
    why_relevant = (
        f"Matched the generated retrieval intent with file-level score {file_score:.3f}"
    )
    if matched_chunk_count > 1:
        why_relevant += f" across {matched_chunk_count} retrieved chunks from the same file"
    suggested_change = (
        f"Inspect this {result.get('type') or 'code'} area for behavior related to: {intent.technical_intent}"
    )
    return EvidenceItem(
        id=f"local-{result['index']}",
        path=str(result.get("file_path") or ""),
        chunk_type=str(result.get("type") or "unknown"),
        symbol=result.get("name"),
        snippet=snippet,
        score=file_score,
        why_relevant=why_relevant,
        suggested_change=suggested_change,
        location_hint=location_hint,
    )


def _state_cache_key(config: CodeRagConfig) -> str:
    return json.dumps(
        {
            "embedding_model_path": config.embedding_model_path,
            "vector_store_path": config.vector_store_path,
            "metadata_path": config.metadata_path,
            "device": config.device,
            "max_tokens": config.max_tokens,
            "stopwords_lang": config.stopwords_lang,
            "include_prefix": config.include_prefix,
            "exclude_prefix": config.exclude_prefix,
            "allowed_extensions": config.allowed_extensions,
            "exclude_filename_keywords": config.exclude_filename_keywords,
            "exclude_path_keywords": config.exclude_path_keywords,
            "ranking_mode": config.ranking_mode,
            "ranking_alpha": config.ranking_alpha,
            "ranking_beta": config.ranking_beta,
            "file_aggregation_strategy": config.file_aggregation_strategy,
            "file_aggregation_alpha": config.file_aggregation_alpha,
            "file_aggregation_beta": config.file_aggregation_beta,
            "file_aggregation_candidate_multiplier": config.file_aggregation_candidate_multiplier,
            "top_k": config.top_k,
        },
        sort_keys=True,
    )


@lru_cache(maxsize=2)
def _get_local_rag_state(cache_key: str) -> dict[str, Any]:
    config_data = json.loads(cache_key)
    resolved = _resolve_local_config(config_data)
    _log(
        "Initializing local RAG state "
        f"embedding_model_path={resolved['embedding_model_path']} "
        f"vector_store_path={resolved['vector_store_path']}"
    )

    try:
        import faiss
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Local Code RAG requires faiss-cpu, numpy, torch, transformers, and nltk. "
            "Install dependencies with `python -m pip install -r requirements.txt`."
        ) from exc

    device_name = _normalize_torch_device_name(resolved["device"], torch)
    device = torch.device(device_name)

    tokenizer = AutoTokenizer.from_pretrained(
        resolved["embedding_model_path"],
        trust_remote_code=True,
    )
    model = AutoModel.from_pretrained(
        resolved["embedding_model_path"],
        trust_remote_code=True,
    )
    model.eval()
    model.to(device)

    vectors, metadatas = _load_embeddings_filtered(
        path=resolved["vector_store_path"],
        include_prefixes=[resolved["include_prefix"]] if resolved["include_prefix"] else [],
        exclude_prefixes=[resolved["exclude_prefix"]] if resolved["exclude_prefix"] else [],
        allowed_extensions=resolved["allowed_extensions"],
        exclude_filename_keywords=resolved["exclude_filename_keywords"],
        exclude_path_keywords=resolved["exclude_path_keywords"],
    )
    _log(f"Loaded local embeddings. vectors={vectors.shape[0]} dim={vectors.shape[1]}")
    index = _build_faiss_index_cosine(vectors)
    _log("FAISS index built successfully")
    return {
        "tokenizer": tokenizer,
        "model": model,
        "device": device,
        "max_tokens": resolved["max_tokens"],
        "stopwords_lang": resolved["stopwords_lang"],
        "vectors": vectors,
        "metadatas": metadatas,
        "index": index,
        "ranking_mode": resolved["ranking_mode"],
        "ranking_alpha": float(resolved["ranking_alpha"]),
        "ranking_beta": float(resolved["ranking_beta"]),
    }


def _normalize_torch_device_name(configured_device: str, torch_module) -> str:
    raw = (configured_device or "").strip().lower()
    if not raw:
        return "cuda" if torch_module.cuda.is_available() else "cpu"
    if raw == "gpu":
        if torch_module.cuda.is_available():
            _log("Configured device alias 'gpu' mapped to 'cuda'")
            return "cuda"
        _log("Configured device alias 'gpu' requested, but CUDA is unavailable. Falling back to 'cpu'")
        return "cpu"
    if raw == "cuda" and not torch_module.cuda.is_available():
        _log("Configured device 'cuda' requested, but CUDA is unavailable. Falling back to 'cpu'")
        return "cpu"
    return raw


def _resolve_local_config(config_data: dict[str, Any]) -> dict[str, Any]:
    if not config_data.get("embedding_model_path") or not config_data.get("vector_store_path"):
        raise RuntimeError(
            "Local Code RAG requires AGENTIC_WORKFLOW_CODE_RAG_EMBEDDING_MODEL_PATH and "
            "AGENTIC_WORKFLOW_CODE_RAG_VECTOR_STORE_PATH to be set."
        )
    embedding_model_path = str(config_data["embedding_model_path"]).strip()
    vector_store_path = str(config_data["vector_store_path"]).strip()

    placeholder_values = {
        "PASTE_LOCAL_EMBEDDING_MODEL_PATH_HERE",
        "PASTE_LOCAL_VECTOR_STORE_JSON_PATH_HERE",
    }
    if embedding_model_path in placeholder_values or vector_store_path in placeholder_values:
        raise RuntimeError(
            "Local Code RAG is enabled, but the .env file still contains placeholder values for "
            "AGENTIC_WORKFLOW_CODE_RAG_EMBEDDING_MODEL_PATH or "
            "AGENTIC_WORKFLOW_CODE_RAG_VECTOR_STORE_PATH. Replace them with real local paths, "
            "or switch AGENTIC_WORKFLOW_CODE_RAG_MODE to 'mock' if local retrieval is not ready yet."
        )

    missing_paths: list[str] = []
    if not Path(embedding_model_path).exists():
        missing_paths.append(
            f"embedding model path does not exist: {embedding_model_path}"
        )
    if not Path(vector_store_path).exists():
        missing_paths.append(
            f"vector store path does not exist: {vector_store_path}"
        )
    if missing_paths:
        raise RuntimeError(
            "Local Code RAG is enabled, but required local assets are missing: "
            + "; ".join(missing_paths)
            + ". Update the .env paths or switch AGENTIC_WORKFLOW_CODE_RAG_MODE to 'mock'."
        )
    return config_data


def _embed_text(text: str, state: dict[str, Any]) -> np.ndarray:
    import torch

    tokenizer = state["tokenizer"]
    model = state["model"]
    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=state["max_tokens"],
    )
    inputs = {key: value.to(state["device"]) for key, value in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
    token_embeddings = outputs.last_hidden_state
    attention_mask = inputs["attention_mask"]
    mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    sum_embeddings = torch.sum(token_embeddings * mask_expanded, dim=1)
    sum_mask = torch.clamp(mask_expanded.sum(dim=1), min=1e-9)
    embedding = (sum_embeddings / sum_mask).squeeze().cpu().numpy().astype("float32")
    return embedding


def _load_embeddings_filtered(
    path: str,
    include_prefixes: list[str],
    exclude_prefixes: list[str],
    allowed_extensions: list[str],
    exclude_filename_keywords: list[str],
    exclude_path_keywords: list[str],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    include_prefixes_norm = [item.lower().replace("\\", "/") for item in include_prefixes]
    exclude_prefixes_norm = [item.lower().replace("\\", "/") for item in exclude_prefixes]
    with open(path, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    filtered_data = []
    for item in data:
        file_path = item["metadata"]["file_path"].lower().replace("\\", "/")
        ext = file_path.split(".")[-1] if "." in file_path else ""
        filename = file_path.split("/")[-1]

        if any(keyword.lower() in file_path for keyword in exclude_path_keywords):
            continue
        if any(keyword.lower() in filename for keyword in exclude_filename_keywords):
            continue
        if include_prefixes_norm and not any(file_path.startswith(prefix) for prefix in include_prefixes_norm):
            continue
        if exclude_prefixes_norm and any(file_path.startswith(prefix) for prefix in exclude_prefixes_norm):
            continue
        if allowed_extensions and ext not in allowed_extensions:
            continue
        filtered_data.append(item)

    if not filtered_data:
        raise RuntimeError("No embedding vectors matched the local Code RAG filters.")

    vectors = np.array([item["vector"] for item in filtered_data], dtype="float32")
    metadatas = [item["metadata"] for item in filtered_data]
    return vectors, metadatas


def _build_faiss_index_cosine(vectors: np.ndarray):
    import faiss

    norm_vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    index = faiss.IndexFlatIP(norm_vectors.shape[1])
    index.add(norm_vectors)
    return index


def _search_local_index(
    query_vec: np.ndarray,
    state: dict[str, Any],
    query: str,
    top_k: int,
    candidate_multiplier: int,
) -> list[dict[str, Any]]:
    norm_query = query_vec / np.linalg.norm(query_vec)
    ranking_mode = state["ranking_mode"].lower().strip()
    metadatas = state["metadatas"]
    vectors = state["vectors"]
    index = state["index"]
    raw_top_k = max(top_k, top_k * max(candidate_multiplier, 1))
    _log(f"Searching local index with ranking_mode={ranking_mode}")

    if ranking_mode == "filter":
        filtered_indices = _filter_metadatas_by_filename(metadatas, query, state["stopwords_lang"])
        _log(f"Filename filter candidates={len(filtered_indices)}")
        if filtered_indices:
            filtered_vectors = vectors[filtered_indices]
            filtered_metas = [metadatas[idx] for idx in filtered_indices]
            filtered_index = _build_faiss_index_cosine(filtered_vectors)
            _log(f"Using filtered FAISS index size={filtered_vectors.shape[0]}")
            return _query_index_cosine(filtered_index, filtered_metas, norm_query, raw_top_k)
        _log("No filename-filter matches. Falling back to full FAISS index")
        return _query_index_cosine(index, metadatas, norm_query, raw_top_k)

    if ranking_mode == "weight":
        raw_results = _query_index_cosine(index, metadatas, norm_query, raw_top_k * 5)
        _log(f"Weight mode raw results={len(raw_results)}")
        return _weighted_ranking(
            raw_results,
            metadatas,
            query,
            state["stopwords_lang"],
            alpha=state["ranking_alpha"],
            beta=state["ranking_beta"],
        )[:raw_top_k]

    if ranking_mode == "semantic_only":
        raw_results = _query_index_cosine(index, metadatas, norm_query, raw_top_k)
        _log(f"Semantic-only mode raw results={len(raw_results)}")
        return raw_results

    if ranking_mode == "filename_only":
        raw_results = _query_index_cosine(index, metadatas, norm_query, raw_top_k * 5)
        _log(f"Filename-only mode raw results={len(raw_results)}")
        return _weighted_ranking(
            raw_results,
            metadatas,
            query,
            state["stopwords_lang"],
            alpha=0.0,
            beta=1.0,
        )[:raw_top_k]

    return _query_index_cosine(index, metadatas, norm_query, raw_top_k)


def _query_index_cosine(index, metadatas: list[dict[str, Any]], query_vec: np.ndarray, top_k: int) -> list[dict[str, Any]]:
    distances, indices = index.search(np.array([query_vec], dtype="float32"), top_k)
    results: list[dict[str, Any]] = []
    for similarity, idx in zip(distances[0], indices[0]):
        meta = metadatas[idx]
        results.append(
            {
                "similarity": float(similarity),
                "index": idx,
                "file_path": meta.get("file_path"),
                "chunk_index": meta.get("chunk_index"),
                "type": meta.get("type"),
                "name": meta.get("name"),
                "start_line": meta.get("start_line"),
                "end_line": meta.get("end_line"),
                "token_count": meta.get("token_count"),
            }
        )
    return results


@lru_cache(maxsize=8)
def _get_stopwords(stopwords_lang: str) -> set[str]:
    try:
        from nltk.corpus import stopwords
    except ImportError as exc:
        raise RuntimeError(
            "Local Code RAG requires nltk. Install dependencies with `python -m pip install -r requirements.txt`."
        ) from exc

    try:
        return set(stopwords.words(stopwords_lang))
    except LookupError:
        import nltk

        nltk.download("stopwords", quiet=True)
        return set(stopwords.words(stopwords_lang))


def _preprocess_query(query: str, stopwords_lang: str) -> set[str]:
    from nltk.stem import PorterStemmer

    stop_words = _get_stopwords(stopwords_lang)
    stemmer = PorterStemmer()
    words = re.split(r"\W+", query.lower())
    words = [word for word in words if word and word not in stop_words]
    return {stemmer.stem(word) for word in words}


def _wildcard_to_regex(pattern: str) -> str:
    return re.escape(pattern).replace("\\*", ".*")


def _filter_metadatas_by_filename(
    metadatas: list[dict[str, Any]],
    query: str,
    stopwords_lang: str,
) -> list[int]:
    keywords = _preprocess_query(query, stopwords_lang)
    matched_indices = []
    for index, meta in enumerate(metadatas):
        filename = str(meta.get("file_path", "")).lower()
        for keyword in keywords:
            if re.search(_wildcard_to_regex(keyword), filename, re.IGNORECASE):
                matched_indices.append(index)
                break
    return matched_indices


def _compute_filename_match_score(filename: str, keywords: list[str]) -> float:
    filename_lower = filename.lower()
    if not keywords:
        return 0.0
    match_count = sum(1 for keyword in keywords if keyword.lower() in filename_lower)
    return match_count / len(keywords)


def _weighted_ranking(
    results: list[dict[str, Any]],
    metadatas: list[dict[str, Any]],
    query: str,
    stopwords_lang: str,
    alpha: float = 0.7,
    beta: float = 0.3,
) -> list[dict[str, Any]]:
    keywords = list(_preprocess_query(query, stopwords_lang))
    for result in results:
        idx = result["index"]
        filename = str(metadatas[idx].get("file_path", ""))
        filename_score = _compute_filename_match_score(filename, keywords)
        result["final_score"] = alpha * result["similarity"] + beta * filename_score
    results.sort(key=lambda item: item["final_score"], reverse=True)
    return results


def _aggregate_results_by_file(
    results: list[dict[str, Any]],
    config: CodeRagConfig,
) -> list[dict[str, Any]]:
    strategy = config.file_aggregation_strategy.lower().strip()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        file_path = str(result.get("file_path") or "").strip()
        if not file_path:
            continue
        grouped.setdefault(file_path, []).append(result)

    aggregated_results: list[dict[str, Any]] = []
    for file_path, file_results in grouped.items():
        sorted_file_results = sorted(
            file_results,
            key=lambda item: float(item.get("final_score", item.get("similarity", 0.0))),
            reverse=True,
        )
        top_scores = [
            float(item.get("final_score", item.get("similarity", 0.0)))
            for item in sorted_file_results
        ]
        top1 = top_scores[0]
        top2 = top_scores[1] if len(top_scores) > 1 else 0.0
        matched_chunk_count = len(sorted_file_results)

        if strategy == "max_only":
            file_score = top1
        elif strategy == "max_plus_second":
            file_score = top1 + config.file_aggregation_alpha * top2
        elif strategy == "max_plus_log_count":
            file_score = top1 + config.file_aggregation_beta * math.log1p(matched_chunk_count)
        elif strategy == "sum_all":
            file_score = sum(top_scores)
        elif strategy == "top3_weighted":
            top3 = top_scores[:3]
            weights = [0.6, 0.3, 0.1]
            file_score = sum(score * weights[idx] for idx, score in enumerate(top3))
        else:
            file_score = (
                top1
                + config.file_aggregation_alpha * top2
                + config.file_aggregation_beta * math.log1p(matched_chunk_count)
            )

        representative = dict(sorted_file_results[0])
        representative["file_score"] = float(file_score)
        representative["matched_chunk_count"] = matched_chunk_count
        representative["supporting_chunk_scores"] = top_scores[:3]
        aggregated_results.append(representative)

    aggregated_results.sort(key=lambda item: float(item["file_score"]), reverse=True)
    return aggregated_results[: config.top_k]
