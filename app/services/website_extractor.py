"""Async website fetcher and HTML content extractor with redirect-safe SSRF checks."""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.config import Settings
from app.schemas import ExtractedWebsiteContent
from app.utils.validators import URLValidationError, normalize_and_validate_url

logger = logging.getLogger(__name__)

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; LinkAnalysisBot/1.0; +https://github.com/link-analysis-api)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

NOISE_TAGS: tuple[str, ...] = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",
    "template",
    "audio",
    "video",
    "object",
    "embed",
    "head",
)

SECONDARY_PATH_KEYWORDS: tuple[str, ...] = (
    "about",
    "services",
    "solutions",
    "products",
    "what-we-do",
    "offerings",
)


class WebsiteExtractionError(Exception):
    """Typed error raised when fetching or parsing a website fails."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "WEBSITE_EXTRACTION_FAILED",
        status_code: int = 502,
        details: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details


class WebsiteExtractor:
    """Fetches and extracts clean, high-signal business & SEO content from websites."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def extract(self, normalized_url: str) -> ExtractedWebsiteContent:
        """Fetch a URL safely and return cleaned structured website content."""
        timeout = httpx.Timeout(
            connect=min(8.0, self.settings.request_timeout_seconds),
            read=self.settings.request_timeout_seconds,
            write=5.0,
            pool=5.0,
        )

        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            html, final_url = await self._fetch_html_safe(client, normalized_url)
            extracted, secondary_candidates = self._parse_html(html, normalized_url, final_url)

            # If homepage text is sparse (e.g. minimalist hero page), enrich with 1-2
            # high-value internal pages like /about or /services.
            if (
                self.settings.fetch_secondary_pages
                and len(extracted.main_text) < 1200
                and secondary_candidates
            ):
                extra_chunks: list[str] = []
                for candidate_url in secondary_candidates[:2]:
                    try:
                        safe_sub_url = normalize_and_validate_url(candidate_url)
                        sub_html, sub_final = await self._fetch_html_safe(client, safe_sub_url)
                        sub_parsed, _ = self._parse_html(sub_html, safe_sub_url, sub_final)
                        if sub_parsed.main_text:
                            extra_chunks.append(
                                f"\n--- Content from {sub_final} ---\n{sub_parsed.main_text}"
                            )
                            extracted.extracted_pages.append(sub_final)
                    except (URLValidationError, WebsiteExtractionError) as exc:
                        logger.debug("Skipping secondary page %s: %s", candidate_url, exc)

                if extra_chunks:
                    combined = f"{extracted.main_text}\n" + "\n".join(extra_chunks)
                    extracted.main_text = self._truncate_text(
                        combined, self.settings.max_content_chars
                    )

        has_signal = any(
            [
                extracted.title,
                extracted.meta_description,
                extracted.headings,
                extracted.structured_data_snippets,
                len(extracted.main_text.strip()) >= 30,
            ]
        )
        if not has_signal:
            raise WebsiteExtractionError(
                "The website returned no readable text or metadata (it may require JavaScript rendering or block automated access).",
                error_code="EMPTY_WEBSITE_CONTENT",
                status_code=422,
            )

        return extracted

    async def _fetch_html_safe(
        self, client: httpx.AsyncClient, start_url: str
    ) -> tuple[str, str]:
        """Fetch URL with manual redirect handling so every hop passes SSRF validation."""
        current_url = start_url

        for hop in range(self.settings.max_redirects + 1):
            try:
                async with client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise WebsiteExtractionError(
                                "Website returned an invalid redirect without a Location header.",
                                error_code="INVALID_REDIRECT",
                                status_code=502,
                            )
                        if hop >= self.settings.max_redirects:
                            raise WebsiteExtractionError(
                                "Website exceeded maximum allowed redirects.",
                                error_code="TOO_MANY_REDIRECTS",
                                status_code=502,
                            )
                        next_url = urljoin(current_url, location)
                        try:
                            current_url = normalize_and_validate_url(next_url)
                        except URLValidationError as exc:
                            raise WebsiteExtractionError(
                                f"Redirect target was blocked: {exc}",
                                error_code=exc.error_code,
                                status_code=400,
                            ) from exc
                        continue

                    if response.status_code in {401, 403}:
                        raise WebsiteExtractionError(
                            f"Access to the website was restricted (HTTP {response.status_code}).",
                            error_code="WEBSITE_ACCESS_RESTRICTED",
                            status_code=422,
                            details=f"Target URL {current_url} returned HTTP {response.status_code}.",
                        )

                    if response.status_code == 404:
                        raise WebsiteExtractionError(
                            "The requested webpage was not found (HTTP 404).",
                            error_code="WEBSITE_NOT_FOUND",
                            status_code=422,
                            details=f"Target URL {current_url} returned HTTP 404.",
                        )

                    if response.status_code == 429:
                        raise WebsiteExtractionError(
                            "The target website rate-limited our request (HTTP 429).",
                            error_code="WEBSITE_RATE_LIMITED",
                            status_code=502,
                        )

                    if response.status_code >= 400:
                        raise WebsiteExtractionError(
                            f"Target website returned an error (HTTP {response.status_code}).",
                            error_code="WEBSITE_HTTP_ERROR",
                            status_code=502,
                        )

                    content_type = response.headers.get("content-type", "").lower()
                    if content_type and not any(
                        allowed in content_type
                        for allowed in ("text/html", "application/xhtml+xml", "text/plain")
                    ):
                        raise WebsiteExtractionError(
                            f"Unsupported content type '{content_type}'. Only HTML/text web pages can be analyzed.",
                            error_code="UNSUPPORTED_CONTENT_TYPE",
                            status_code=422,
                        )

                    chunks: list[bytes] = []
                    total_bytes = 0
                    async for chunk in response.aiter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self.settings.max_response_bytes:
                            remaining = self.settings.max_response_bytes - (
                                total_bytes - len(chunk)
                            )
                            if remaining > 0:
                                chunks.append(chunk[:remaining])
                            break
                        chunks.append(chunk)

                    raw_bytes = b"".join(chunks)
                    encoding = response.encoding or "utf-8"
                    html_text = raw_bytes.decode(encoding, errors="replace")
                    return html_text, current_url

            except WebsiteExtractionError:
                raise
            except httpx.TimeoutException as exc:
                raise WebsiteExtractionError(
                    "Timed out while connecting to the target website.",
                    error_code="WEBSITE_TIMEOUT",
                    status_code=504,
                    details=str(exc),
                ) from exc
            except httpx.ConnectError as exc:
                raise WebsiteExtractionError(
                    "Could not connect to the target website. Verify the domain is reachable.",
                    error_code="WEBSITE_UNREACHABLE",
                    status_code=502,
                    details=str(exc),
                ) from exc
            except httpx.HTTPError as exc:
                raise WebsiteExtractionError(
                    "HTTP error while fetching the target website.",
                    error_code="WEBSITE_FETCH_ERROR",
                    status_code=502,
                    details=str(exc),
                ) from exc

        raise WebsiteExtractionError(
            "Website exceeded maximum allowed redirects.",
            error_code="TOO_MANY_REDIRECTS",
            status_code=502,
        )

    def _parse_html(
        self, html: str, original_url: str, final_url: str
    ) -> tuple[ExtractedWebsiteContent, list[str]]:
        """Parse HTML and extract SEO metadata, structured data, headings, and clean body text."""
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        language = None
        if soup.html and isinstance(soup.html, Tag):
            lang_attr = soup.html.get("lang")
            if isinstance(lang_attr, str) and lang_attr.strip():
                language = lang_attr.strip()

        title = self._clean_inline(soup.title.get_text()) if soup.title else None
        meta_description = self._get_meta(soup, name="description")
        raw_keywords = self._get_meta(soup, name="keywords")
        meta_keywords = (
            [kw.strip() for kw in raw_keywords.split(",") if kw.strip()][:20]
            if raw_keywords
            else []
        )

        og_site_name = self._get_meta(soup, prop="og:site_name")
        og_title = self._get_meta(soup, prop="og:title")
        og_description = self._get_meta(soup, prop="og:description")

        structured_data = self._extract_json_ld(soup)
        navigation_links, secondary_candidates = self._extract_navigation(soup, final_url)

        # Strip non-content elements before extracting headings and body text
        for tag in soup.find_all(NOISE_TAGS):
            tag.decompose()

        headings: list[str] = []
        seen_headings: set[str] = set()
        for h_tag in soup.find_all(["h1", "h2", "h3"]):
            text = self._clean_inline(h_tag.get_text(" ", strip=True))
            if text and len(text) <= 200:
                key = text.lower()
                if key not in seen_headings:
                    seen_headings.add(key)
                    headings.append(f"{h_tag.name.upper()}: {text}")
            if len(headings) >= 35:
                break

        # Prefer semantic <main> / <article> if substantial, else fall back to full body
        content_root = soup.find("main") or soup.find("article") or soup.body or soup
        raw_text = content_root.get_text("\n", strip=True)

        # If <main> had very little text, fall back to full body
        if len(raw_text) < 400 and soup.body is not None and content_root is not soup.body:
            raw_text = soup.body.get_text("\n", strip=True)

        cleaned_text = self._deduplicate_and_clean_lines(raw_text)
        truncated_text = self._truncate_text(cleaned_text, self.settings.max_content_chars)

        extracted = ExtractedWebsiteContent(
            url=original_url,
            final_url=final_url,
            title=title,
            meta_description=meta_description,
            meta_keywords=meta_keywords,
            og_site_name=og_site_name,
            og_title=og_title,
            og_description=og_description,
            language=language,
            headings=headings,
            navigation_links=navigation_links,
            structured_data_snippets=structured_data,
            main_text=truncated_text,
            extracted_pages=[final_url],
        )
        return extracted, secondary_candidates

    @staticmethod
    def _get_meta(
        soup: BeautifulSoup, *, name: str | None = None, prop: str | None = None
    ) -> str | None:
        attrs = {}
        if name:
            attrs["name"] = re.compile(f"^{re.escape(name)}$", re.IGNORECASE)
        if prop:
            attrs["property"] = re.compile(f"^{re.escape(prop)}$", re.IGNORECASE)
        tag = soup.find("meta", attrs=attrs)
        if tag and isinstance(tag, Tag):
            content = tag.get("content")
            if isinstance(content, str) and content.strip():
                return WebsiteExtractor._clean_inline(content)
        return None

    @staticmethod
    def _extract_json_ld(soup: BeautifulSoup) -> list[str]:
        """Extract concise snippets from JSON-LD structured data blocks."""
        snippets: list[str] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict):
                    continue
                summary = {
                    k: item[k]
                    for k in (
                        "@type",
                        "name",
                        "description",
                        "serviceType",
                        "areaServed",
                        "address",
                        "slogan",
                    )
                    if k in item
                }
                if summary:
                    serialized = json.dumps(summary, ensure_ascii=False)[:600]
                    snippets.append(serialized)
                if len(snippets) >= 5:
                    return snippets
        return snippets

    @staticmethod
    def _extract_navigation(
        soup: BeautifulSoup, base_url: str
    ) -> tuple[list[str], list[str]]:
        """Collect concise nav link labels and candidate same-origin about/services URLs."""
        base_host = urlparse(base_url).netloc.lower()
        nav_labels: list[str] = []
        seen_labels: set[str] = set()
        secondary_urls: list[str] = []
        seen_urls: set[str] = set()

        nav_containers = soup.find_all(["nav", "header"]) or [soup]
        for container in nav_containers:
            for anchor in container.find_all("a", href=True):
                label = WebsiteExtractor._clean_inline(anchor.get_text(" ", strip=True))
                href = str(anchor.get("href", "")).strip()
                if label and 2 <= len(label) <= 50:
                    key = label.lower()
                    if key not in seen_labels and len(nav_labels) < 25:
                        seen_labels.add(key)
                        nav_labels.append(label)

                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                absolute = urljoin(base_url, href).split("#")[0]
                parsed = urlparse(absolute)
                if parsed.netloc.lower() != base_host or absolute.rstrip("/") == base_url.rstrip("/"):
                    continue
                path_lower = parsed.path.lower()
                if any(kw in path_lower for kw in SECONDARY_PATH_KEYWORDS):
                    canonical = absolute.rstrip("/")
                    if canonical not in seen_urls:
                        seen_urls.add(canonical)
                        secondary_urls.append(absolute)

        return nav_labels, secondary_urls

    @staticmethod
    def _clean_inline(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _deduplicate_and_clean_lines(text: str) -> str:
        """Remove duplicate boilerplate lines and excessive whitespace to save LLM tokens."""
        lines: list[str] = []
        seen: set[str] = set()
        for raw_line in text.splitlines():
            cleaned = re.sub(r"\s+", " ", raw_line).strip()
            if len(cleaned) < 2:
                continue
            dedup_key = cleaned.lower()
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            lines.append(cleaned)
        return "\n".join(lines)

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars]
        last_newline = truncated.rfind("\n")
        if last_newline > int(max_chars * 0.7):
            truncated = truncated[:last_newline]
        return truncated.rstrip() + "\n[Content truncated for token efficiency]"
