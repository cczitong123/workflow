from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[3]

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULT_EPIC_DATA_DIR = PROJECT_ROOT / "data" / "epics"
DEFAULT_PROMPT_DIR = PROJECT_ROOT / "apps" / "api" / "src" / "prompts"

# Edit these blocks directly when you want a ready-to-run local setup.
# `.env` is still supported for secrets and machine-specific overrides.
USER_SERVER = {
    "host": "127.0.0.1",
    "port": 8000,
}

USER_PATHS = {
    "epic_data_dir": "./data/epics",
    "prompt_dir": "./apps/api/src/prompts",
}

DEFAULT_CODE_RAG = {
    "mode": "local",
    "embedding_model_path": "./local_jina-code-embeddings-1.5b",
    "vector_store_path": "./ce_jina-code-embeddings-1.5b/codebase_embeddings_clang_800.json",
    "metadata_path": "",
    "device": "cpu",
    "max_tokens": 800,
    "stopwords_lang": "english",
    "include_prefix": "",
    "exclude_prefix": "requirements",
    "allowed_extensions": ["cpp", "h", "zstm", "build"],
    "exclude_filename_keywords": ["test"],
    "exclude_path_keywords": [],
    "ranking_mode": "filter",
    "top_k": 20,
}

DEFAULT_LLM = {
    "mode": "remote",
    "local_model_path": "",
    "device": "cpu",
    "endpoint": "",
    "api_path": "",
    "model": "",
    "cert_path": "",
    "auth_url": "",
    "api_key": "",
    "access_token": "",
    "client_id": "",
    "client_secret": "",
    "timeout_seconds": 60,
    "temperature": 0.2,
    "max_tokens": 1200,
    "top_p": 1.0,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
}


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
    max_tokens: int
    stopwords_lang: str
    include_prefix: str
    exclude_prefix: str
    allowed_extensions: list[str]
    exclude_filename_keywords: list[str]
    exclude_path_keywords: list[str]
    ranking_mode: str
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
    api_path: str
    model: str
    api_key: str
    access_token: str
    cert_path: str
    auth_url: str
    client_id: str
    client_secret: str
    timeout_seconds: int
    temperature: float
    max_tokens: int
    top_p: float
    presence_penalty: float
    frequency_penalty: float


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    prompt_dir: Path
    epic_data: EpicDataConfig
    code_rag: CodeRagConfig
    llm_api: LlmApiConfig


def _resolve_path(value: str | Path) -> Path:
    raw_value = str(value)
    if re.match(r"^[A-Za-z]:[\\/]", raw_value) or raw_value.startswith("\\\\"):
        return Path(raw_value)
    raw = Path(raw_value).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    return (PROJECT_ROOT / raw).resolve()


def _resolve_optional_path(value: str) -> str:
    if not value:
        return ""
    return str(_resolve_path(value))


def load_app_config() -> AppConfig:
    data_dir = _resolve_path(os.getenv("BMWCODE_EPIC_DATA_DIR", USER_PATHS["epic_data_dir"]))
    prompt_dir = _resolve_path(os.getenv("BMWCODE_PROMPT_DIR", USER_PATHS["prompt_dir"]))

    return AppConfig(
        host=os.getenv("BMWCODE_HOST", USER_SERVER["host"]),
        port=int(os.getenv("BMWCODE_PORT", str(USER_SERVER["port"]))),
        prompt_dir=prompt_dir,
        epic_data=EpicDataConfig(data_dir=data_dir),
        code_rag=CodeRagConfig(
            mode=os.getenv("BMWCODE_CODE_RAG_MODE", DEFAULT_CODE_RAG["mode"]),
            embedding_model_path=_resolve_optional_path(
                os.getenv(
                    "BMWCODE_CODE_RAG_EMBEDDING_MODEL_PATH",
                    DEFAULT_CODE_RAG["embedding_model_path"],
                )
            ),
            vector_store_path=_resolve_optional_path(
                os.getenv(
                    "BMWCODE_CODE_RAG_VECTOR_STORE_PATH",
                    DEFAULT_CODE_RAG["vector_store_path"],
                )
            ),
            metadata_path=_resolve_optional_path(
                os.getenv(
                    "BMWCODE_CODE_RAG_METADATA_PATH",
                    DEFAULT_CODE_RAG["metadata_path"],
                )
            ),
            device=os.getenv("BMWCODE_CODE_RAG_DEVICE", DEFAULT_CODE_RAG["device"]),
            max_tokens=int(os.getenv("BMWCODE_CODE_RAG_MAX_TOKENS", str(DEFAULT_CODE_RAG["max_tokens"]))),
            stopwords_lang=os.getenv("BMWCODE_CODE_RAG_STOPWORDS_LANG", DEFAULT_CODE_RAG["stopwords_lang"]),
            include_prefix=os.getenv("BMWCODE_CODE_RAG_INCLUDE_PREFIX", DEFAULT_CODE_RAG["include_prefix"]),
            exclude_prefix=os.getenv("BMWCODE_CODE_RAG_EXCLUDE_PREFIX", DEFAULT_CODE_RAG["exclude_prefix"]),
            allowed_extensions=[
                item.strip()
                for item in os.getenv(
                    "BMWCODE_CODE_RAG_ALLOWED_EXTENSIONS",
                    ",".join(DEFAULT_CODE_RAG["allowed_extensions"]),
                ).split(",")
                if item.strip()
            ],
            exclude_filename_keywords=[
                item.strip()
                for item in os.getenv(
                    "BMWCODE_CODE_RAG_EXCLUDE_FILENAME_KEYWORDS",
                    ",".join(DEFAULT_CODE_RAG["exclude_filename_keywords"]),
                ).split(",")
                if item.strip()
            ],
            exclude_path_keywords=[
                item.strip()
                for item in os.getenv(
                    "BMWCODE_CODE_RAG_EXCLUDE_PATH_KEYWORDS",
                    ",".join(DEFAULT_CODE_RAG["exclude_path_keywords"]),
                ).split(",")
                if item.strip()
            ],
            ranking_mode=os.getenv("BMWCODE_CODE_RAG_RANKING_MODE", DEFAULT_CODE_RAG["ranking_mode"]),
            endpoint=os.getenv("BMWCODE_CODE_RAG_ENDPOINT", ""),
            index_name=os.getenv("BMWCODE_CODE_RAG_INDEX", ""),
            api_key=os.getenv("BMWCODE_CODE_RAG_API_KEY", ""),
            access_token=os.getenv("BMWCODE_CODE_RAG_ACCESS_TOKEN", ""),
            top_k=int(os.getenv("BMWCODE_CODE_RAG_TOP_K", str(DEFAULT_CODE_RAG["top_k"]))),
        ),
        llm_api=LlmApiConfig(
            mode=os.getenv("BMWCODE_LLM_MODE", DEFAULT_LLM["mode"]),
            local_model_path=_resolve_optional_path(
                os.getenv("BMWCODE_LLM_LOCAL_MODEL_PATH", DEFAULT_LLM["local_model_path"])
            ),
            device=os.getenv("BMWCODE_LLM_DEVICE", DEFAULT_LLM["device"]),
            endpoint=os.getenv("BMWCODE_LLM_ENDPOINT", DEFAULT_LLM["endpoint"]),
            api_path=os.getenv("BMWCODE_LLM_API_PATH", DEFAULT_LLM["api_path"]),
            model=os.getenv("BMWCODE_LLM_MODEL", DEFAULT_LLM["model"]),
            api_key=os.getenv("BMWCODE_LLM_API_KEY", DEFAULT_LLM["api_key"]),
            access_token=os.getenv("BMWCODE_LLM_ACCESS_TOKEN", DEFAULT_LLM["access_token"]),
            cert_path=_resolve_optional_path(os.getenv("BMWCODE_LLM_CERT_PATH", DEFAULT_LLM["cert_path"])),
            auth_url=os.getenv(
                "BMWCODE_LLM_AUTH_URL",
                DEFAULT_LLM["auth_url"],
            ),
            client_id=os.getenv("BMWCODE_LLM_CLIENT_ID", DEFAULT_LLM["client_id"]),
            client_secret=os.getenv("BMWCODE_LLM_CLIENT_SECRET", DEFAULT_LLM["client_secret"]),
            timeout_seconds=int(os.getenv("BMWCODE_LLM_TIMEOUT_SECONDS", str(DEFAULT_LLM["timeout_seconds"]))),
            temperature=float(os.getenv("BMWCODE_LLM_TEMPERATURE", str(DEFAULT_LLM["temperature"]))),
            max_tokens=int(os.getenv("BMWCODE_LLM_MAX_TOKENS", str(DEFAULT_LLM["max_tokens"]))),
            top_p=float(os.getenv("BMWCODE_LLM_TOP_P", str(DEFAULT_LLM["top_p"]))),
            presence_penalty=float(
                os.getenv("BMWCODE_LLM_PRESENCE_PENALTY", str(DEFAULT_LLM["presence_penalty"]))
            ),
            frequency_penalty=float(
                os.getenv("BMWCODE_LLM_FREQUENCY_PENALTY", str(DEFAULT_LLM["frequency_penalty"]))
            ),
        ),
    )
