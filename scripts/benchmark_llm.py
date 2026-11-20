"""Optional benchmark script to measure real response time, token usage, and cost.

Usage:
    python scripts/benchmark_llm.py https://stripe.com https://fastapi.tiangolo.com
"""

from __future__ import annotations

import asyncio
import sys

from app.config import get_settings
from app.services.analysis_service import AnalysisService


async def run_benchmark(urls: list[str]) -> None:
    settings = get_settings().model_copy(update={"cache_ttl_seconds": 0})
    service = AnalysisService(settings)

    print(f"Benchmarking provider={settings.llm_provider} model={settings.resolved_model}\n")
    print(f"{'URL':<38} | {'Latency (ms)':>12} | {'In Tok':>7} | {'Out Tok':>7} | {'Est Cost ($)':>12}")
    print("-" * 88)

    for url in urls:
        try:
            result = await service.analyze_url(url)
            meta = result.metadata
            cost = (
                f"{meta.token_usage.estimated_cost_usd:.6f}"
                if meta.token_usage.estimated_cost_usd is not None
                else "N/A"
            )
            print(
                f"{url[:38]:<38} | {meta.latency_ms:>12} | "
                f"{meta.token_usage.input_tokens:>7} | {meta.token_usage.output_tokens:>7} | {cost:>12}"
            )
        except Exception as exc:
            print(f"{url[:38]:<38} | ERROR: {exc}")


if __name__ == "__main__":
    target_urls = sys.argv[1:] or ["https://fastapi.tiangolo.com", "https://stripe.com"]
    asyncio.run(run_benchmark(target_urls))
