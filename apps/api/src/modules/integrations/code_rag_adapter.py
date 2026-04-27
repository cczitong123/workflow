from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from config import CodeRagConfig
from modules.shared.models import EvidenceItem, RetrievalIntent


def retrieve_code_evidence(
    intent: RetrievalIntent,
    config: CodeRagConfig,
) -> list[EvidenceItem]:
    mode = config.mode.lower().strip()
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
    state = _get_local_rag_state(_state_cache_key(config))
    query = _build_query_from_intent(intent)
    query_vec = _embed_text(query, state)
    results = _search_local_index(query_vec, state, query, config.top_k)
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
    why_relevant = (
        f"Matched the generated retrieval intent with similarity {result['similarity']:.3f}"
    )
    suggested_change = (
        f"Inspect this {result.get('type') or 'code'} area for behavior related to: {intent.technical_intent}"
    )
    return EvidenceItem(
        id=f"local-{result['index']}",
        path=str(result.get("file_path") or ""),
        chunk_type=str(result.get("type") or "unknown"),
        symbol=result.get("name"),
        snippet=snippet,
        score=float(result.get("final_score", result.get("similarity", 0.0))),
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
            "top_k": config.top_k,
        },
        sort_keys=True,
    )


@lru_cache(maxsize=2)
def _get_local_rag_state(cache_key: str) -> dict[str, Any]:
    config_data = json.loads(cache_key)
    resolved = _resolve_local_config(config_data)

    try:
        import faiss
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Local Code RAG requires faiss-cpu, numpy, torch, transformers, and nltk. "
            "Install dependencies with `python -m pip install -r requirements.txt`."
        ) from exc

    device_name = resolved["device"] or ("cuda" if torch.cuda.is_available() else "cpu")
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
    index = _build_faiss_index_cosine(vectors)
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
    }


def _resolve_local_config(config_data: dict[str, Any]) -> dict[str, Any]:
    if not config_data.get("embedding_model_path") or not config_data.get("vector_store_path"):
        raise RuntimeError(
            "Local Code RAG requires BMWCODE_CODE_RAG_EMBEDDING_MODEL_PATH and "
            "BMWCODE_CODE_RAG_VECTOR_STORE_PATH to be set."
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
) -> list[dict[str, Any]]:
    norm_query = query_vec / np.linalg.norm(query_vec)
    ranking_mode = state["ranking_mode"].lower().strip()
    metadatas = state["metadatas"]
    vectors = state["vectors"]
    index = state["index"]

    if ranking_mode == "filter":
        filtered_indices = _filter_metadatas_by_filename(metadatas, query, state["stopwords_lang"])
        if filtered_indices:
            filtered_vectors = vectors[filtered_indices]
            filtered_metas = [metadatas[idx] for idx in filtered_indices]
            filtered_index = _build_faiss_index_cosine(filtered_vectors)
            return _query_index_cosine(filtered_index, filtered_metas, norm_query, top_k)
        return _query_index_cosine(index, metadatas, norm_query, top_k)

    if ranking_mode == "weight":
        raw_results = _query_index_cosine(index, metadatas, norm_query, top_k * 5)
        return _weighted_ranking(raw_results, metadatas, query, state["stopwords_lang"])[:top_k]

    return _query_index_cosine(index, metadatas, norm_query, top_k)


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
