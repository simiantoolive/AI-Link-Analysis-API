"""Comprehensive unit and API tests for URL validation, extraction, enrichment, and endpoints."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import (
    AnalysisSections,
    BrandDetails,
    CompetitorAnalysis,
    CompetitorProfile,
    CustomerProfile,
    ExtractedWebsiteContent,
    LLMAnalysisPayload,
    SEOImprovements,
    ServiceItem,
    SupportingContentIdea,
    TargetAudience,
    TargetSearchQuery,
)
from app.services.analysis_service import AnalysisService
from app.services.competitor_service import CompetitorService
from app.services.llm_service import LLMResult, LLMServiceError
from app.services.website_extractor import WebsiteExtractionError, WebsiteExtractor
from app.utils.validators import URLValidationError, normalize_and_validate_url


def _sample_llm_payload() -> LLMAnalysisPayload:
    return LLMAnalysisPayload(
        brand=BrandDetails(
            name="Acme Web Studio",
            category="Web Development & Digital Solutions",
            summary="A digital agency building fast web applications and e-commerce platforms for SMBs.",
            main_products_and_services=[
                "Custom Web Development",
                "E-commerce Storefronts",
                "UI/UX Design",
            ],
        ),
        analysis=AnalysisSections(
            competitor_analysis=CompetitorAnalysis(
                brand_overview="Acme Web Studio delivers tailored websites and SaaS interfaces for growing businesses.",
                main_business_category="Web Development Services",
                main_products_and_services=["Custom Web Development", "E-commerce Development"],
                competitors=[
                    CompetitorProfile(
                        name="Acme Web Studio",  # Self-duplicate should be filtered out
                        website="https://acmewebstudio.example",
                        main_service="Web Development",
                        target_market="SMBs",
                        strengths=["Fast delivery"],
                        weaknesses_or_gaps=["Small team"],
                        source_basis="ai_inferred",
                    ),
                    CompetitorProfile(
                        name="Webflow Agencies Network",
                        website="https://webflow.com",
                        main_service="No-code & Custom Web Design",
                        target_market="Startups and Marketing Teams",
                        strengths=["Strong brand recognition", "Large template ecosystem"],
                        weaknesses_or_gaps=["Less tailored custom backend engineering"],
                        source_basis="website_mentioned",  # Not actually on site -> downgraded to ai_inferred
                    ),
                    CompetitorProfile(
                        name="Toptal",
                        website="https://www.toptal.com",
                        main_service="On-demand Software & Web Engineering",
                        target_market="Mid-market and Enterprise",
                        strengths=["Global talent pool", "Enterprise credibility"],
                        weaknesses_or_gaps=["Higher pricing for small businesses"],
                        source_basis="ai_inferred",
                    ),
                    CompetitorProfile(
                        name="BairesDev",
                        website="https://www.bairesdev.com",
                        main_service="Nearshore Custom Software Development",
                        target_market="Enterprise",
                        strengths=["Large engineering teams"],
                        weaknesses_or_gaps=["Less accessible for early-stage SMB budgets"],
                        source_basis="ai_inferred",
                    ),
                ],
                strengths=["Affordable transparent pricing for SMBs", "Fast turnaround times"],
                opportunities=["Publish industry case studies", "Expand organic SEO landing pages"],
                comparison_summary=[],  # Should be auto-populated by CompetitorService
            ),
            target_search_queries=[
                TargetSearchQuery(
                    query="custom web development agency for small businesses",
                    platforms=["Google"],
                    intent="Service Search",
                    business_relevance="Directly matches core web development service offering.",
                    target_audience="Small Business Owners",
                ),
                TargetSearchQuery(
                    query="affordable website redesign services",
                    platforms=["Facebook"],
                    intent="Business Discovery",
                    business_relevance="Captures SMB owners looking to modernize outdated websites.",
                    target_audience="Local & Retail Businesses",
                ),
                TargetSearchQuery(
                    query="modern ecommerce website design ideas",
                    platforms=["Instagram"],
                    intent="Inspiration",
                    business_relevance="Showcases visual UI/UX and storefront design capabilities.",
                    target_audience="D2C Brand Founders",
                ),
                TargetSearchQuery(
                    query="web application development partner for startups",
                    platforms=["LinkedIn"],
                    intent="B2B Search",
                    business_relevance="Targets startup founders evaluating engineering partners.",
                    target_audience="Startup Founders & CTOs",
                ),
                TargetSearchQuery(
                    query="fast SaaS frontend development studio",
                    platforms=["X"],
                    intent="Service Discovery",
                    business_relevance="Reaches tech founders seeking rapid MVP delivery.",
                    target_audience="Tech Founders",
                ),
            ],
            seo_improvements=SEOImprovements(
                primary_keyword="custom web development agency",
                secondary_keywords=[
                    "small business web development",
                    "ecommerce website design",
                    "UI UX design agency",
                    "responsive web applications",
                ],
                long_tail_keywords=[
                    "affordable custom web development for startups",
                    "fast ecommerce storefront agency for SMBs",
                    "custom SaaS frontend development services",
                    "website redesign agency for growing businesses",
                ],
                relevant_business_keywords=[
                    "digital agency",
                    "web engineering",
                    "conversion optimization",
                    "website maintenance",
                ],
                recommended_title="Custom Web Development Agency for SMBs | Acme Web Studio",
                title_improvement_notes="Places primary keyword upfront and specifies target market within 60 characters.",
                meta_description=(
                    "Acme Web Studio builds fast custom websites, SaaS frontends, and e-commerce "
                    "storefronts for growing SMBs and startups. Request a project quote today."
                ),
                supporting_content=[
                    SupportingContentIdea(
                        content_type="Service Landing Page",
                        title="Custom E-commerce Website Development for Growing Brands",
                        purpose="Captures high-intent commercial searches for online store builds.",
                    ),
                    SupportingContentIdea(
                        content_type="FAQ",
                        title="Web Development Pricing, Timelines & Process FAQ",
                        purpose="Answers buyer objections and targets long-tail informational queries.",
                    ),
                    SupportingContentIdea(
                        content_type="Blog Topic",
                        title="How to Choose a Web Development Partner for Your Startup MVP",
                        purpose="Builds topical authority and attracts early-stage founders.",
                    ),
                ],
            ),
            top_services=[
                ServiceItem(
                    service_name="Custom Web Development",
                    description="End-to-end responsive websites engineered for speed and reliability.",
                    target_industry="Cross-industry SMBs",
                    target_customer="Small Business Owners",
                    main_benefit="Higher online credibility and lead conversion.",
                    source_type="extracted_from_website",
                ),
                ServiceItem(
                    service_name="E-commerce Storefront Development",
                    description="Conversion-focused online stores with seamless checkout flows.",
                    target_industry="Retail & D2C",
                    target_customer="E-commerce Merchants",
                    main_benefit="Increased online sales and lower cart abandonment.",
                    source_type="extracted_from_website",
                ),
                ServiceItem(
                    service_name="UI/UX Design",
                    description="User research, wireframing, and modern visual design systems.",
                    target_industry="SaaS & Digital Products",
                    target_customer="Product Managers & Founders",
                    main_benefit="Intuitive interfaces that improve customer retention.",
                    source_type="extracted_from_website",
                ),
                ServiceItem(
                    service_name="Website Performance & SEO Optimization",
                    description="Technical Core Web Vitals tuning and on-page SEO architecture.",
                    target_industry="B2B & Local Services",
                    target_customer="Marketing Managers",
                    main_benefit="Stronger organic search visibility and faster page loads.",
                    source_type="extracted_from_website",
                ),
                ServiceItem(
                    service_name="Ongoing Website Maintenance & Support",
                    description="Proactive security updates, backups, and feature iterations.",
                    target_industry="Small & Mid-Market Businesses",
                    target_customer="Operations Leads",
                    main_benefit="Reliable uptime without hiring an in-house web team.",
                    source_type="inferred_recommendation",
                ),
            ],
            target_audience=TargetAudience(
                primary_audience=["Small and Medium Business Owners", "Startup Founders"],
                secondary_audience=["Marketing Directors", "E-commerce Operations Managers"],
                industries=["Technology & SaaS", "Retail & E-commerce", "Professional Services"],
                business_types=["Small Businesses", "Startups", "Growing Mid-Market Brands"],
                customer_profiles=[
                    CustomerProfile(
                        segment_name="Small Businesses",
                        business_type="Local & Regional SMBs",
                        requirement="Modern lead-generating website",
                        profile_summary="Owners replacing outdated sites to attract qualified leads.",
                    ),
                    CustomerProfile(
                        segment_name="Startups",
                        business_type="Seed & Series A Startups",
                        requirement="Fast MVP and marketing site launch",
                        profile_summary="Lean founders needing reliable engineering without full-time overhead.",
                    ),
                ],
                geographical_focus="Not explicitly specified on website",
                customer_needs=[
                    "Fast, mobile-friendly website launch",
                    "Predictable pricing and clear timelines",
                ],
                customer_challenges=[
                    "Slow legacy websites hurting conversions",
                    "Lack of in-house design and frontend expertise",
                ],
                potential_interests=["Conversion rate optimization", "Modern web frameworks", "SEO growth"],
                recommended_marketing_segments=[
                    "LinkedIn targeting for Startup Founders",
                    "Google Search ads for SMB web redesign intent",
                ],
            ),
        ),
    )


def _sample_extracted_content() -> ExtractedWebsiteContent:
    return ExtractedWebsiteContent(
        url="https://acmewebstudio.example/",
        final_url="https://acmewebstudio.example/",
        title="Acme Web Studio - Home",
        meta_description="We build websites for growing businesses.",
        headings=["H1: Custom Web Development for Growing Businesses", "H2: E-commerce & UI/UX"],
        navigation_links=["Services", "About", "Portfolio", "Contact"],
        main_text=(
            "Acme Web Studio builds fast custom websites, e-commerce storefronts, and UI/UX designs "
            "for small businesses and startups."
        ),
        extracted_pages=["https://acmewebstudio.example/"],
    )


# ---------------------------------------------------------------------------
# 1. URL Validation & SSRF Protection Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url,expected_code",
    [
        ("ftp://example.com", "INVALID_URL"),
        ("javascript:alert(1)", "INVALID_URL"),
        ("http://user:pass@example.com", "INVALID_URL"),
        ("http://localhost:4000/admin", "SSRF_BLOCKED"),
        ("http://127.0.0.1/secret", "SSRF_BLOCKED"),
        ("http://169.254.169.254/latest/meta-data/", "SSRF_BLOCKED"),
        ("http://10.0.0.5/", "SSRF_BLOCKED"),
        ("http://192.168.1.1/", "SSRF_BLOCKED"),
        ("http://[::1]/", "SSRF_BLOCKED"),
        ("https://service.internal/api", "SSRF_BLOCKED"),
        ("https://example.com:22/", "SSRF_BLOCKED"),
    ],
)
def test_ssrf_and_invalid_urls_are_blocked(bad_url: str, expected_code: str) -> None:
    with pytest.raises(URLValidationError) as exc_info:
        normalize_and_validate_url(bad_url)
    assert exc_info.value.error_code == expected_code


def test_dns_rebinding_to_private_ip_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(_host: str, _port: object, proto: int = 0) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", ("10.20.30.40", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(URLValidationError) as exc_info:
        normalize_and_validate_url("https://rebind.example.com")
    assert exc_info.value.error_code == "SSRF_BLOCKED"


def test_public_url_normalizes_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(_host: str, _port: object, proto: int = 0) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    normalized = normalize_and_validate_url("HTTPS://Example.COM:443/about#team")
    assert normalized == "https://example.com/about"


# ---------------------------------------------------------------------------
# 2. HTML Extraction & Competitor Refinement Tests
# ---------------------------------------------------------------------------


def test_html_parser_strips_noise_and_extracts_signals() -> None:
    html = """
    <html lang="en">
      <head>
        <title>  Acme   Web Studio  </title>
        <meta name="description" content="Custom web apps for startups." />
        <meta property="og:site_name" content="Acme Studio" />
        <script>const secret = "should be stripped";</script>
        <style>.hidden { display: none; }</style>
        <script type="application/ld+json">
          {"@type": "Organization", "name": "Acme Web Studio", "description": "Web agency"}
        </script>
      </head>
      <body>
        <header><nav><a href="/about">About Us</a><a href="/services">Services</a></nav></header>
        <main>
          <h1>Build Faster Websites</h1>
          <p>We craft scalable web platforms.</p>
          <p>We craft scalable web platforms.</p>
        </main>
      </body>
    </html>
    """
    extractor = WebsiteExtractor(Settings())
    extracted, secondary = extractor._parse_html(
        html, "https://acme.example/", "https://acme.example/"
    )

    assert extracted.title == "Acme Web Studio"
    assert extracted.meta_description == "Custom web apps for startups."
    assert extracted.og_site_name == "Acme Studio"
    assert extracted.language == "en"
    assert "H1: Build Faster Websites" in extracted.headings
    assert "should be stripped" not in extracted.main_text
    assert extracted.main_text.count("We craft scalable web platforms.") == 1
    assert len(extracted.structured_data_snippets) == 1
    assert "https://acme.example/about" in secondary
    assert "https://acme.example/services" in secondary


def test_competitor_service_filters_self_and_enforces_provenance() -> None:
    payload = _sample_llm_payload()
    extracted = _sample_extracted_content()
    refined = CompetitorService().refine(
        payload.brand, payload.analysis.competitor_analysis, extracted
    )

    names = [c.name for c in refined.competitors]
    assert "Acme Web Studio" not in names
    assert len(refined.competitors) == 3
    assert all(c.source_basis == "ai_inferred" for c in refined.competitors)
    assert len(refined.comparison_summary) == 5
    assert "AI-generated suggestions" in refined.methodology_note


# ---------------------------------------------------------------------------
# 3. FastAPI Endpoint & Caching Integration Tests
# ---------------------------------------------------------------------------


def test_health_and_root_endpoints() -> None:
    app = create_app(Settings())
    client = TestClient(app)

    root_res = client.get("/")
    assert root_res.status_code == 200
    assert root_res.json()["docs"] == "/docs"

    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"


def test_post_link_analysis_success_and_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(_host: str, _port: object, proto: int = 0) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    settings = Settings(cache_ttl_seconds=300)
    mock_extractor = AsyncMock(spec=WebsiteExtractor)
    mock_extractor.extract.return_value = _sample_extracted_content()

    mock_llm = AsyncMock()
    mock_llm.analyze_website.return_value = LLMResult(
        payload=_sample_llm_payload(),
        provider="gemini",
        model="gemini-3.1-flash-lite",
        input_tokens=1500,
        output_tokens=1200,
        latency_ms=1420,
    )

    app = create_app(settings)
    app.state.analysis_service = AnalysisService(
        settings, extractor=mock_extractor, llm_service=mock_llm
    )
    client = TestClient(app)

    # First call -> cache miss
    res1 = client.post("/link-analysis", json={"url": "https://acmewebstudio.example"})
    assert res1.status_code == 200
    body1 = res1.json()
    assert body1["success"] is True
    assert body1["brand"]["name"] == "Acme Web Studio"
    assert len(body1["analysis"]["competitor_analysis"]["competitors"]) == 3
    assert len(body1["analysis"]["target_search_queries"]) == 5
    assert len(body1["analysis"]["top_services"]) == 5
    assert body1["analysis"]["seo_improvements"]["current_title"] == "Acme Web Studio - Home"
    assert body1["metadata"]["cached"] is False
    assert body1["metadata"]["token_usage"]["total_tokens"] == 2700
    assert body1["metadata"]["token_usage"]["estimated_cost_usd"] == pytest.approx(0.002175)

    # Second call -> cache hit (no extra extraction or LLM call)
    res2 = client.post("/link-analysis", json={"url": "https://acmewebstudio.example/"})
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["metadata"]["cached"] is True
    assert body2["metadata"]["token_usage"]["total_tokens"] == 0
    assert mock_extractor.extract.await_count == 1
    assert mock_llm.analyze_website.await_count == 1


def test_post_link_analysis_handles_errors_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(_host: str, _port: object, proto: int = 0) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    settings = Settings(cache_ttl_seconds=0)
    mock_extractor = AsyncMock(spec=WebsiteExtractor)
    mock_llm = AsyncMock()

    app = create_app(settings)
    app.state.analysis_service = AnalysisService(
        settings, extractor=mock_extractor, llm_service=mock_llm
    )
    client = TestClient(app)

    # SSRF attempt -> 403
    ssrf_res = client.post("/link-analysis", json={"url": "http://127.0.0.1:8000"})
    assert ssrf_res.status_code == 403
    assert ssrf_res.json()["error_code"] == "SSRF_BLOCKED"

    # Malformed payload -> 422
    bad_body_res = client.post("/link-analysis", json={})
    assert bad_body_res.status_code == 422
    assert bad_body_res.json()["error_code"] == "VALIDATION_ERROR"

    # Website restricted (403 upstream) -> 422
    mock_extractor.extract.side_effect = WebsiteExtractionError(
        "Access restricted", error_code="WEBSITE_ACCESS_RESTRICTED", status_code=422
    )
    restricted_res = client.post("/link-analysis", json={"url": "https://blocked.example"})
    assert restricted_res.status_code == 422
    assert restricted_res.json()["error_code"] == "WEBSITE_ACCESS_RESTRICTED"

    # LLM not configured -> 503
    mock_extractor.extract.side_effect = None
    mock_extractor.extract.return_value = _sample_extracted_content()
    mock_llm.analyze_website.side_effect = LLMServiceError(
        "Missing API key", error_code="LLM_NOT_CONFIGURED", status_code=503
    )
    llm_err_res = client.post("/link-analysis", json={"url": "https://acmewebstudio.example"})
    assert llm_err_res.status_code == 503
    assert llm_err_res.json()["error_code"] == "LLM_NOT_CONFIGURED"
