"""Validated starter configuration. Real providers never fall back to fixtures."""

import hashlib
import json
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )

    app_name: str = "InsightHub API"
    environment: str = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = Field(
        default="postgresql://insighthub:insighthub@postgres:5432/insighthub",
        repr=False,
    )
    rag_mode: Literal["fixture", "real"] = "real"
    llm_provider: Literal["gemini", "anthropic", "ollama", "openai", "fixture"] = (
        "gemini"
    )
    embedding_provider: Literal["gemini", "voyage", "openai", "ollama", "fixture"] = (
        "gemini"
    )
    gemini_api_key: str = Field(default="", repr=False)
    gemini_chat_model: str = ""
    gemini_embedding_model: str = "gemini-embedding-2"
    anthropic_api_key: str = Field(default="", repr=False)
    anthropic_chat_model: str = ""
    voyage_api_key: str = Field(default="", repr=False)
    voyage_embedding_model: str = "voyage-3.5"
    openai_api_key: str = Field(default="", repr=False)
    # Required explicitly for OpenAI, including OpenAI-compatible gateways.
    openai_base_url: str = ""
    openai_chat_model: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    ollama_base_url: str = "http://ollama:11434"
    ollama_chat_model: str = ""
    ollama_embedding_model: str = "mxbai-embed-large"
    llm_model: str = ""
    embedding_model: str = ""
    llm_max_tokens: int = Field(default=1024, ge=1, le=32768)
    embedding_dim: int = Field(default=1024, ge=1, le=2000)
    embedding_revision: str = Field(default="1", min_length=1, max_length=128)
    provider_timeout_seconds: float = Field(
        default=60, gt=0, le=300, allow_inf_nan=False
    )
    embedding_batch_size: int = Field(default=32, ge=1, le=100)
    chunk_size: int = Field(default=800, ge=2, le=8000)
    chunk_overlap: int = Field(default=100, ge=0)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    hnsw_ef_search: int = Field(default=100, ge=20, le=1000)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    # Day 1 async ingestion. API and ingestion-worker MUST read the same REDIS_URL
    # and the same UPLOAD_DIR volume, otherwise jobs or staged bytes are unreachable.
    redis_url: str = Field(default="redis://redis:6379/0", repr=False)
    upload_dir: str = "/var/lib/insighthub/uploads"

    @model_validator(mode="after")
    def validate_configuration(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.rag_mode == "fixture":
            if self.llm_provider != "fixture" or self.embedding_provider != "fixture":
                raise ValueError("RAG_MODE=fixture requires both providers=fixture")
            if self.llm_model or self.embedding_model:
                raise ValueError("Fixture model overrides are not supported")
        elif "fixture" in (self.llm_provider, self.embedding_provider):
            raise ValueError("Fixture providers require RAG_MODE=fixture")
        for provider in {self.llm_provider, self.embedding_provider}:
            if provider in {"gemini", "anthropic", "voyage", "openai"}:
                if not getattr(self, f"{provider}_api_key"):
                    raise ValueError(f"{provider.upper()}_API_KEY is required")
            if provider in {"ollama", "openai"}:
                value = getattr(self, f"{provider}_base_url")
                parsed = urlsplit(value)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError(
                        f"{provider.upper()}_BASE_URL must be an explicit HTTP(S) URL without credentials/query"
                    )
                try:
                    parsed.port
                except ValueError:
                    raise ValueError(
                        f"{provider.upper()}_BASE_URL has an invalid port"
                    ) from None
        if not self.resolved_chat_model or not self.resolved_embedding_model:
            raise ValueError("Selected providers require nonempty model names")
        if (
            self.embedding_provider == "gemini"
            and self.resolved_embedding_model
            not in {
                "gemini-embedding-001",
                "gemini-embedding-2",
                "gemini-embedding-2-preview",
            }
        ):
            raise ValueError("Unsupported Gemini embedding model")
        if self.embedding_provider == "ollama":
            # This starter supports a tested, dedicated embedding contract.
            if self.resolved_embedding_model.split(":")[0] != "mxbai-embed-large":
                raise ValueError("Ollama embedding requires mxbai-embed-large")
            if self.embedding_dim != 1024:
                raise ValueError("mxbai-embed-large requires EMBEDDING_DIM=1024")
        redis = urlsplit(self.redis_url)
        if redis.scheme not in {"redis", "rediss", "unix"} or (
            redis.scheme != "unix" and not redis.hostname
        ):
            raise ValueError("REDIS_URL must be redis://, rediss:// or unix://")
        return self

    @property
    def resolved_chat_model(self) -> str:
        if self.llm_provider == "fixture":
            return "extractive-fixture-v1"
        return self.llm_model or getattr(self, f"{self.llm_provider}_chat_model")

    @property
    def resolved_embedding_model(self) -> str:
        if self.embedding_provider == "fixture":
            return "shake256-fixture-v1"
        return self.embedding_model or getattr(
            self, f"{self.embedding_provider}_embedding_model"
        )

    @property
    def embedding_identity(self) -> dict:
        endpoint = {
            "gemini": "https://generativelanguage.googleapis.com/v1beta",
            "voyage": "https://api.voyageai.com/v1",
            "openai": self.openai_base_url.rstrip("/"),
            "ollama": self.ollama_base_url.rstrip("/"),
            "fixture": "fixture",
        }[self.embedding_provider]
        return {
            "mode": self.rag_mode,
            "provider": self.embedding_provider,
            "model": self.resolved_embedding_model,
            "dimension": self.embedding_dim,
            "endpoint": endpoint,
            "revision": self.embedding_revision,
            "preprocessing": "retrieval-v1",
            "normalization": "l2-v1",
        }

    @property
    def embedding_identity_id(self) -> str:
        return hashlib.sha256(
            json.dumps(self.embedding_identity, sort_keys=True).encode()
        ).hexdigest()


@lru_cache
def get_settings() -> Settings:
    return Settings()
