from __future__ import annotations

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
    raise NotImplementedError(
        "Local Code RAG is selected, but the local loader/search logic is not implemented yet. "
        "Use config.code_rag.embedding_model_path and config.code_rag.vector_store_path in this file."
    )


def _retrieve_remote_evidence(
    intent: RetrievalIntent,
    config: CodeRagConfig,
) -> list[EvidenceItem]:
    raise NotImplementedError(
        "Remote Code RAG is selected, but the remote HTTP client is not implemented yet. "
        "Use config.code_rag.endpoint and auth fields in this file."
    )
