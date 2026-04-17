from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class EpicDataConfig:
    data_dir: Path


@dataclass(frozen=True)
class CodeRagConfig:
    mode: str
    embedding_model_path: str
    vector_store_path: str
    metadata_path: str
    device: str
    endpoint: str
    index_name: str
    api_key: str
    access_token: str
    top_k: int


@dataclass(frozen=True)
class LlmApiConfig:
    mode: str
    local_model_path: str
    device: str
    endpoint: str
    model: str
    api_key: str
    access_token: str
    timeout_seconds: int


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    epic_data: EpicDataConfig
    code_rag: CodeRagConfig
    llm_api: LlmApiConfig


def load_app_config() -> AppConfig:
    data_dir = Path(
        os.getenv("BMWCODE_EPIC_DATA_DIR", str(PROJECT_ROOT / "data" / "epics"))
    ).expanduser().resolve()

    return AppConfig(
        host=os.getenv("BMWCODE_HOST", "127.0.0.1"),
        port=int(os.getenv("BMWCODE_PORT", "8000")),
        epic_data=EpicDataConfig(data_dir=data_dir),
        code_rag=CodeRagConfig(
            mode=os.getenv("BMWCODE_CODE_RAG_MODE", "mock"),
            embedding_model_path=os.getenv("BMWCODE_CODE_RAG_EMBEDDING_MODEL_PATH", ""),
            vector_store_path=os.getenv("BMWCODE_CODE_RAG_VECTOR_STORE_PATH", ""),
            metadata_path=os.getenv("BMWCODE_CODE_RAG_METADATA_PATH", ""),
            device=os.getenv("BMWCODE_CODE_RAG_DEVICE", "cpu"),
            endpoint=os.getenv("BMWCODE_CODE_RAG_ENDPOINT", ""),
            index_name=os.getenv("BMWCODE_CODE_RAG_INDEX", ""),
            api_key=os.getenv("BMWCODE_CODE_RAG_API_KEY", ""),
            access_token=os.getenv("BMWCODE_CODE_RAG_ACCESS_TOKEN", ""),
            top_k=int(os.getenv("BMWCODE_CODE_RAG_TOP_K", "3")),
        ),
        llm_api=LlmApiConfig(
            mode=os.getenv("BMWCODE_LLM_MODE", "mock"),
            local_model_path=os.getenv("BMWCODE_LLM_LOCAL_MODEL_PATH", ""),
            device=os.getenv("BMWCODE_LLM_DEVICE", "cpu"),
            endpoint=os.getenv("BMWCODE_LLM_ENDPOINT", ""),
            model=os.getenv("BMWCODE_LLM_MODEL", "mock-model"),
            api_key=os.getenv("BMWCODE_LLM_API_KEY", ""),
            access_token=os.getenv("BMWCODE_LLM_ACCESS_TOKEN", ""),
            timeout_seconds=int(os.getenv("BMWCODE_LLM_TIMEOUT_SECONDS", "30")),
        ),
    )
