"""Environment-driven configuration. Nothing is read at import time."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
PLACEHOLDER_USER_AGENT = "bnpl-metrics-demo (your-email@example.com)"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    base_url: str
    llm_model: str
    embedding_model: str
    llm_timeout: float
    llm_max_retries: int
    user_agent: str
    proxies: dict[str, str] | None
    cache_dir: Path
    data_dir: Path
    port: int
    share: bool

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        proxy = (
            os.getenv("HTTPS_PROXY")
            or os.getenv("https_proxy")
            or os.getenv("HTTP_PROXY")
            or os.getenv("http_proxy")
        )
        return cls(
            api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL),
            llm_model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
            embedding_model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
            llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            user_agent=os.getenv("SEC_USER_AGENT", PLACEHOLDER_USER_AGENT),
            proxies={"http": proxy, "https": proxy} if proxy else None,
            cache_dir=Path(os.getenv("BNPL_METRICS_CACHE_DIR", "cache")),
            data_dir=Path(os.getenv("BNPL_METRICS_DATA_DIR", "data")),
            port=int(os.getenv("BNPL_METRICS_PORT", "7862")),
            share=os.getenv("BNPL_METRICS_SHARE", "0") == "1",
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "LLM_API_KEY (or OPENAI_API_KEY) is not set. Add it to your environment or .env."
            )
        return self.api_key

    @property
    def has_real_user_agent(self) -> bool:
        return self.user_agent != PLACEHOLDER_USER_AGENT and "@" in self.user_agent

    def require_user_agent(self) -> str:
        """SEC EDGAR requires a descriptive User-Agent with real contact details."""
        if not self.has_real_user_agent:
            raise ConfigError(
                "Set SEC_USER_AGENT to a descriptive string with your contact email, "
                'e.g. "Jane Doe jane@example.com". SEC EDGAR requires this.'
            )
        return self.user_agent
