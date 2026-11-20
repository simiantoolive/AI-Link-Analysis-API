"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


LLMProvider = Literal["gemini", "openai", "anthropic"]

DEFAULT_MODELS: dict[str, str] = {
    "gemini": "gemini-3.1-flash-lite",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
}

# Published standard pay-as-you-go API pricing (USD per 1M tokens)
# Used for transparent per-request cost estimation in response metadata.
MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    # (input_usd_per_1m, output_usd_per_1m)
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "claude-3-5-haiku-latest": (0.80, 4.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
}


class Settings(BaseSettings):
    """Typed runtime settings loaded from .env and process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM Provider & Model Selection
    llm_provider: LLMProvider = Field(default="gemini")
    llm_model: str | None = Field(default=None)
    fallback_llm_provider: str | None = Field(default=None)
    fallback_llm_model: str | None = Field(default=None)

    # Provider API Credentials (never hardcode secrets)
    gemini_api_key: str | None = Field(default=None)
    google_api_key: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)
    anthropic_api_key: str | None = Field(default=None)

    # Website Fetching & Content Extraction
    request_timeout_seconds: float = Field(default=15.0, ge=2.0, le=60.0)
    max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=65_536)
    max_content_chars: int = Field(default=6_500, ge=1_000, le=50_000)
    max_redirects: int = Field(default=5, ge=0, le=10)
    fetch_secondary_pages: bool = Field(default=True)

    # LLM Execution Parameters
    llm_timeout_seconds: float = Field(default=45.0, ge=5.0, le=120.0)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    gemini_thinking_level: str | None = Field(default="MINIMAL")

    # In-memory TTL Cache for Cost Optimization
    cache_ttl_seconds: int = Field(default=3600, ge=0)
    cache_max_size: int = Field(default=256, ge=1)

    # Runtime Environment
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")

    @property
    def resolved_model(self) -> str:
        """Return configured model or provider default."""
        if self.llm_model and self.llm_model.strip():
            return self.llm_model.strip()
        return DEFAULT_MODELS[self.llm_provider]

    def get_api_key(self, provider: str) -> str | None:
        """Fetch API key for the given provider."""
        if provider == "gemini":
            key = self.gemini_api_key or self.google_api_key
        elif provider == "openai":
            key = self.openai_api_key
        elif provider == "anthropic":
            key = self.anthropic_api_key
        else:
            key = None

        if key and key.strip() and not key.strip().startswith("your_"):
            return key.strip()
        return None


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimate USD cost using published per-1M-token rates when known."""
    pricing = MODEL_PRICING_PER_1M.get(model)
    if pricing is None:
        # Try prefix match for dated snapshots (e.g., gpt-4o-mini-2024-07-18)
        for prefix, rates in MODEL_PRICING_PER_1M.items():
            if model.startswith(prefix):
                pricing = rates
                break
    if pricing is None:
        return None

    in_rate, out_rate = pricing
    cost = (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
    return round(cost, 6)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
