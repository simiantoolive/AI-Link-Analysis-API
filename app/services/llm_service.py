"""Multi-provider LLM client with native structured JSON output, retries, and fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from pydantic import ValidationError

from app.config import DEFAULT_MODELS, Settings
from app.schemas import ExtractedWebsiteContent, LLMAnalysisPayload

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior digital strategy, competitive intelligence, and SEO analyst.
Analyze the extracted content of a real website and return a grounded, concise, high-signal structured JSON analysis across five sections. Keep every description crisp (1 concise sentence) and specific to the website—no generic filler.

STRICT RULES:
1. Ground every insight in the actual extracted website content provided.
2. Competitor Analysis:
   - Identify 3 relevant competitors in the same business category/market.
   - Set source_basis="website_mentioned" ONLY if explicitly named on the website; otherwise ALWAYS set source_basis="ai_inferred".
   - Provide concise strengths, weaknesses_or_gaps, your brand's strengths, opportunities, and 5 comparison_summary rows (Brand Name, Main Service, Target Market, Key Strength, Opportunity).
3. Top 5 Target Search Queries:
   - Generate exactly 5 realistic customer search queries.
   - MUST cover ALL 5 supported platforms at least once across the 5 queries: Google, Facebook, Instagram, LinkedIn, and X.
   - NEVER invent search volume numbers, CPCs, or ranking positions.
4. SEO Improvements:
   - Recommend 1 primary keyword, 4-5 secondary keywords, 4-5 long-tail keywords, and 4-5 relevant business keywords.
   - Provide an optimized SEO title (<= 60 chars, with primary keyword) and human-friendly meta description (140-160 chars).
   - Suggest 5 diverse supporting content ideas.
5. Top 5 Services:
   - Identify the 5 most important services/products.
   - Label on-site services as source_type="extracted_from_website"; if fewer than 5 are explicit on-site, clearly label inferred additions as source_type="inferred_recommendation".
6. Target Audience Identification:
   - Identify primary_audience, secondary_audience, industries, business_types, 4 customer_profiles, geographical_focus ("Not explicitly specified on website" if absent), customer_needs, customer_challenges, potential_interests, and recommended_marketing_segments.

EXACT ENUM VALUES REQUIRED:
- competitor.source_basis: "ai_inferred" or "website_mentioned"
- target_search_queries[].platforms[]: ONLY values from ["Google", "Facebook", "Instagram", "LinkedIn", "X"]
- seo_improvements.supporting_content[].content_type: ONLY values from ["Service Landing Page", "Industry-Specific Page", "FAQ", "Blog Topic", "Location-Based Landing Page", "Supporting Business Article"]
- top_services[].source_type: "extracted_from_website" or "inferred_recommendation"
"""


def _simplify_schema_for_gemini(schema: dict) -> dict:
    """Dereference and simplify a Pydantic JSON schema so it stays well within Gemini's
    structured-output finite-state grammar limits while preserving exact JSON structure."""
    defs = schema.get("$defs", {})

    def _transform(node: dict) -> dict:
        if "$ref" in node:
            ref_name = node["$ref"].split("/")[-1]
            return _transform(defs[ref_name])

        # Simplify Optional[T] represented as anyOf: [T, {"type": "null"}]
        if "anyOf" in node:
            non_null = [branch for branch in node["anyOf"] if branch.get("type") != "null"]
            if len(non_null) == 1:
                result = _transform(non_null[0])
                result["nullable"] = True
                return result

        node_type = node.get("type")
        if node_type == "object":
            props = {k: _transform(v) for k, v in node.get("properties", {}).items()}
            required = [k for k in node.get("required", []) if k in props]
            out: dict = {"type": "object", "properties": props}
            if required:
                out["required"] = required
            return out

        if node_type == "array":
            return {
                "type": "array",
                "items": _transform(node.get("items", {"type": "string"})),
            }

        if node_type == "string":
            # Keep short enums (e.g. platform names); drop long multi-word enums to avoid
            # grammar state explosion (still strictly enforced in prompt + Pydantic validation).
            enum_vals = node.get("enum")
            if enum_vals and all(len(str(v)) <= 12 for v in enum_vals) and len(enum_vals) <= 6:
                return {"type": "string", "enum": enum_vals}
            return {"type": "string"}

        if node_type in {"integer", "number", "boolean"}:
            return {"type": node_type}

        return {"type": "string"}

    return _transform(schema)


def build_user_prompt(content: ExtractedWebsiteContent) -> str:
    """Build a compact, high-signal prompt from extracted website data."""
    sections = [
        f"Website URL: {content.final_url}",
        f"Language: {content.language or 'Unknown'}",
        f"HTML Title: {content.title or 'None'}",
        f"Meta Description: {content.meta_description or 'None'}",
        f"Meta Keywords: {', '.join(content.meta_keywords) if content.meta_keywords else 'None'}",
        f"OG Site Name: {content.og_site_name or 'None'}",
        f"OG Title: {content.og_title or 'None'}",
        f"OG Description: {content.og_description or 'None'}",
    ]
    if content.navigation_links:
        sections.append("Navigation Items: " + " | ".join(content.navigation_links))
    if content.headings:
        sections.append("Key Headings:\n" + "\n".join(content.headings))
    if content.structured_data_snippets:
        sections.append("Structured Data (JSON-LD):\n" + "\n".join(content.structured_data_snippets))
    sections.append("Extracted Website Body Text:\n" + content.main_text)

    return (
        "Analyze the following extracted website content and produce the complete 5-section "
        "structured business, competitor, search query, SEO, service, and audience analysis.\n\n"
        + "\n\n".join(sections)
    )


class LLMServiceError(Exception):
    """Typed error raised when LLM configuration or generation fails."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "LLM_ERROR",
        status_code: int = 502,
        details: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details


@dataclass(slots=True)
class LLMResult:
    """Structured output and measured telemetry from an LLM invocation."""

    payload: LLMAnalysisPayload
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMService:
    """Calls Gemini, OpenAI, or Anthropic with strict Pydantic structured outputs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze_website(self, content: ExtractedWebsiteContent) -> LLMResult:
        """Analyze extracted website content using primary provider with optional fallback."""
        user_prompt = build_user_prompt(content)

        primary_provider = self.settings.llm_provider
        primary_model = self.settings.resolved_model

        try:
            return await self._invoke_with_retries(primary_provider, primary_model, user_prompt)
        except LLMServiceError as primary_exc:
            fallback_provider = (self.settings.fallback_llm_provider or "").strip().lower()
            if not fallback_provider:
                raise

            fallback_model = (
                (self.settings.fallback_llm_model or "").strip()
                or DEFAULT_MODELS.get(fallback_provider, "")
            )
            if not fallback_model:
                raise

            logger.warning(
                "Primary LLM (%s/%s) failed (%s). Falling back to %s/%s.",
                primary_provider,
                primary_model,
                primary_exc.error_code,
                fallback_provider,
                fallback_model,
            )
            return await self._invoke_with_retries(fallback_provider, fallback_model, user_prompt)

    async def _invoke_with_retries(
        self, provider: str, model: str, user_prompt: str
    ) -> LLMResult:
        api_key = self.settings.get_api_key(provider)
        if not api_key:
            raise LLMServiceError(
                f"API key for LLM provider '{provider}' is not configured. "
                f"Set the appropriate environment variable in .env.",
                error_code="LLM_NOT_CONFIGURED",
                status_code=503,
            )

        last_error: Exception | None = None
        attempts = self.settings.llm_max_retries + 1

        for attempt in range(1, attempts + 1):
            start = time.perf_counter()
            try:
                if provider == "gemini":
                    payload, in_tok, out_tok = await self._call_gemini(api_key, model, user_prompt)
                elif provider == "openai":
                    payload, in_tok, out_tok = await self._call_openai(api_key, model, user_prompt)
                elif provider == "anthropic":
                    payload, in_tok, out_tok = await self._call_anthropic(
                        api_key, model, user_prompt
                    )
                else:
                    raise LLMServiceError(
                        f"Unsupported LLM provider '{provider}'. Use gemini, openai, or anthropic.",
                        error_code="UNSUPPORTED_LLM_PROVIDER",
                        status_code=500,
                    )

                latency_ms = int((time.perf_counter() - start) * 1000)
                return LLMResult(
                    payload=payload,
                    provider=provider,
                    model=model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    latency_ms=latency_ms,
                )
            except LLMServiceError:
                raise
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning(
                    "Structured output validation failed on attempt %d/%d (%s/%s): %s",
                    attempt,
                    attempts,
                    provider,
                    model,
                    exc,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "LLM request failed on attempt %d/%d (%s/%s): %s",
                    attempt,
                    attempts,
                    provider,
                    model,
                    exc,
                )

            if attempt < attempts:
                await asyncio.sleep(1.0 * attempt)

        raise LLMServiceError(
            f"LLM analysis failed after {attempts} attempts using {provider}/{model}.",
            error_code="LLM_REQUEST_FAILED",
            status_code=502,
            details=str(last_error) if last_error else None,
        )

    async def _call_gemini(
        self, api_key: str, model: str, user_prompt: str
    ) -> tuple[LLMAnalysisPayload, int, int]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        compact_schema = _simplify_schema_for_gemini(LLMAnalysisPayload.model_json_schema())
        thinking_cfg = (
            types.ThinkingConfig(thinking_level=self.settings.gemini_thinking_level)
            if self.settings.gemini_thinking_level
            else None
        )
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=self.settings.llm_temperature,
            response_mime_type="application/json",
            response_json_schema=compact_schema,
            thinking_config=thinking_cfg,
        )
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model,
                contents=user_prompt,
                config=config,
            ),
            timeout=self.settings.llm_timeout_seconds,
        )

        raw_text = response.text or ""
        payload = LLMAnalysisPayload.model_validate_json(raw_text)

        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        candidates_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        thoughts_tokens = int(getattr(usage, "thoughts_token_count", 0) or 0)
        output_tokens = candidates_tokens + thoughts_tokens
        return payload, input_tokens, output_tokens

    async def _call_openai(
        self, api_key: str, model: str, user_prompt: str
    ) -> tuple[LLMAnalysisPayload, int, int]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=api_key,
            timeout=self.settings.llm_timeout_seconds,
        )
        completion = await client.chat.completions.parse(
            model=model,
            temperature=self.settings.llm_temperature,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format=LLMAnalysisPayload,
        )

        message = completion.choices[0].message
        if message.parsed is not None:
            payload = message.parsed
        elif message.content:
            payload = LLMAnalysisPayload.model_validate_json(message.content)
        else:
            raise LLMServiceError(
                "OpenAI returned an empty structured response.",
                error_code="LLM_EMPTY_RESPONSE",
                status_code=502,
            )

        usage = completion.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return payload, input_tokens, output_tokens

    async def _call_anthropic(
        self, api_key: str, model: str, user_prompt: str
    ) -> tuple[LLMAnalysisPayload, int, int]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            api_key=api_key,
            timeout=self.settings.llm_timeout_seconds,
        )
        tool_name = "submit_link_analysis"
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=self.settings.llm_temperature,
            system=SYSTEM_PROMPT,
            tools=[
                {
                    "name": tool_name,
                    "description": "Submit the complete structured 5-section website link analysis.",
                    "input_schema": LLMAnalysisPayload.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_prompt}],
        )

        tool_input = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                tool_input = block.input
                break

        if tool_input is None:
            raise LLMServiceError(
                "Anthropic did not return the structured analysis tool payload.",
                error_code="LLM_EMPTY_RESPONSE",
                status_code=502,
            )

        payload = LLMAnalysisPayload.model_validate(tool_input)
        usage = response.usage
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return payload, input_tokens, output_tokens
