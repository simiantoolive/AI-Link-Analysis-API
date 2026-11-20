"""Orchestration service combining validation, caching, extraction, LLM analysis, and enrichment."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict

from app.config import Settings, estimate_cost_usd
from app.schemas import (
    AnalysisMetadata,
    LinkAnalysisResponse,
    TokenUsage,
)
from app.services.competitor_service import CompetitorService
from app.services.llm_service import LLMService
from app.services.website_extractor import WebsiteExtractor
from app.utils.validators import cache_key_for_url, normalize_and_validate_url

logger = logging.getLogger(__name__)


class AnalysisTTLCache:
    """Simple bounded in-memory TTL cache to avoid redundant scraping & LLM calls."""

    def __init__(self, ttl_seconds: int, max_size: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._store: OrderedDict[str, tuple[float, LinkAnalysisResponse]] = OrderedDict()

    def get(self, key: str) -> LinkAnalysisResponse | None:
        if self.ttl_seconds <= 0:
            return None
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: LinkAnalysisResponse) -> None:
        if self.ttl_seconds <= 0:
            return
        self._store[key] = (time.monotonic() + self.ttl_seconds, value)
        self._store.move_to_end(key)
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()


class AnalysisService:
    """Coordinates the full 9-step website link analysis pipeline."""

    def __init__(
        self,
        settings: Settings,
        *,
        extractor: WebsiteExtractor | None = None,
        llm_service: LLMService | None = None,
        competitor_service: CompetitorService | None = None,
    ) -> None:
        self.settings = settings
        self.extractor = extractor or WebsiteExtractor(settings)
        self.llm_service = llm_service or LLMService(settings)
        self.competitor_service = competitor_service or CompetitorService()
        self.cache = AnalysisTTLCache(
            ttl_seconds=settings.cache_ttl_seconds,
            max_size=settings.cache_max_size,
        )

    async def analyze_url(self, raw_url: str) -> LinkAnalysisResponse:
        """Validate URL, fetch content, run LLM analysis, enrich, and return structured JSON."""
        request_start = time.perf_counter()

        # 1-2. Validate URL & apply SSRF protection
        normalized_url = normalize_and_validate_url(raw_url)
        cache_key = cache_key_for_url(normalized_url)

        # Cost optimization: return cached analysis when fresh
        cached_response = self.cache.get(cache_key)
        if cached_response is not None:
            latency_ms = int((time.perf_counter() - request_start) * 1000)
            logger.info("Cache hit for %s (%d ms)", normalized_url, latency_ms)
            updated_meta = cached_response.metadata.model_copy(
                update={
                    "cached": True,
                    "latency_ms": latency_ms,
                    "token_usage": TokenUsage(
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        estimated_cost_usd=0.0,
                    ),
                }
            )
            return cached_response.model_copy(update={"metadata": updated_meta})

        # 3-5. Fetch, parse, clean, and structure website content
        extracted = await self.extractor.extract(normalized_url)

        # 6-8. Generate & validate all 5 analysis sections via LLM
        llm_result = await self.llm_service.analyze_website(extracted)
        payload = llm_result.payload

        # Post-process: competitor provenance, extracted SEO baseline, list sizing
        refined_competitors = self.competitor_service.refine(
            payload.brand,
            payload.analysis.competitor_analysis,
            extracted,
        )
        refined_seo = payload.analysis.seo_improvements.model_copy(
            update={
                "current_title": extracted.title,
                "current_meta_description": extracted.meta_description,
            }
        )
        refined_analysis = payload.analysis.model_copy(
            update={
                "competitor_analysis": refined_competitors,
                "target_search_queries": payload.analysis.target_search_queries[:5],
                "seo_improvements": refined_seo,
                "top_services": payload.analysis.top_services[:5],
            }
        )

        total_tokens = llm_result.input_tokens + llm_result.output_tokens
        cost_usd = estimate_cost_usd(
            llm_result.model, llm_result.input_tokens, llm_result.output_tokens
        )
        total_latency_ms = int((time.perf_counter() - request_start) * 1000)

        # 9. Construct final structured API response
        response = LinkAnalysisResponse(
            success=True,
            website=extracted.final_url,
            brand=payload.brand,
            analysis=refined_analysis,
            metadata=AnalysisMetadata(
                provider=llm_result.provider,
                model=llm_result.model,
                cached=False,
                latency_ms=total_latency_ms,
                token_usage=TokenUsage(
                    input_tokens=llm_result.input_tokens,
                    output_tokens=llm_result.output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost_usd=cost_usd,
                ),
                extracted_pages=extracted.extracted_pages,
            ),
        )

        self.cache.set(cache_key, response)
        return response
