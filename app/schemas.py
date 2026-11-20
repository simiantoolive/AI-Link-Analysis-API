"""Pydantic request, domain, LLM structured-output, and API response schemas."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Request Schema
# ---------------------------------------------------------------------------


class LinkAnalysisRequest(BaseModel):
    """Input payload for POST /link-analysis."""

    url: str = Field(
        ...,
        description="Public HTTP/HTTPS website URL to analyze.",
        examples=["https://example.com"],
    )

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("URL must not be empty.")
        return cleaned


# ---------------------------------------------------------------------------
# Brand Summary Schema
# ---------------------------------------------------------------------------


class BrandDetails(BaseModel):
    """High-level brand summary extracted from website content."""

    name: str = Field(..., description="Brand or organization name.")
    category: str = Field(..., description="Main business category or industry.")
    summary: str = Field(..., description="Concise overview of what the brand does.")
    main_products_and_services: list[str] = Field(
        default_factory=list,
        description="Primary products or services offered on the website.",
    )


# ---------------------------------------------------------------------------
# Section 1: Competitor vs Your Brand Details
# ---------------------------------------------------------------------------


class CompetitorProfile(BaseModel):
    """Profile of a single relevant competitor."""

    name: str = Field(..., description="Competitor brand name.")
    website: str | None = Field(
        default=None,
        description="Competitor website URL if commonly known; otherwise null.",
    )
    main_service: str = Field(..., description="Competitor's core product or service.")
    target_market: str = Field(..., description="Primary customer segment targeted.")
    strengths: list[str] = Field(
        default_factory=list, description="Key strengths of this competitor."
    )
    weaknesses_or_gaps: list[str] = Field(
        default_factory=list, description="Notable weaknesses or market gaps."
    )
    source_basis: Literal["ai_inferred", "website_mentioned"] = Field(
        default="ai_inferred",
        description="Indicates whether the competitor was mentioned on-site or inferred by AI.",
    )


class BrandComparisonRow(BaseModel):
    """Side-by-side comparison attribute between the submitted brand and competitors."""

    dimension: str = Field(
        ...,
        description="Comparison criterion (e.g., Main Service, Target Market, Key Strength, Opportunity).",
    )
    your_brand: str = Field(..., description="Submitted brand positioning on this dimension.")
    competitor_benchmark: str = Field(
        ..., description="Typical competitor positioning on this dimension."
    )


class CompetitorAnalysis(BaseModel):
    """Section 1: Competitor vs Your Brand analysis."""

    brand_overview: str = Field(..., description="Detailed overview of the submitted brand.")
    main_business_category: str = Field(..., description="Primary business category.")
    main_products_and_services: list[str] = Field(
        default_factory=list, description="Main products and services identified."
    )
    competitors: list[CompetitorProfile] = Field(
        default_factory=list,
        description="Identified relevant competitors (minimum 3 when reliable info is available).",
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="Submitted brand's competitive advantages and strengths.",
    )
    opportunities: list[str] = Field(
        default_factory=list,
        description="Actionable opportunities to improve market positioning.",
    )
    comparison_summary: list[BrandComparisonRow] = Field(
        default_factory=list,
        description="Structured side-by-side comparison of your brand vs competitors.",
    )
    methodology_note: str = Field(
        default=(
            "Competitor profiles marked 'ai_inferred' are AI-generated suggestions based on "
            "extracted website positioning and should be validated with live market research."
        ),
        description="Transparency note on how competitor insights were derived.",
    )


# ---------------------------------------------------------------------------
# Section 2: Top 5 Target Search Queries
# ---------------------------------------------------------------------------


SupportedPlatform = Literal["Google", "Facebook", "Instagram", "LinkedIn", "X"]


class TargetSearchQuery(BaseModel):
    """Single customer search query targeting one or more platforms."""

    query: str = Field(..., description="Realistic search query customers would type.")
    platforms: list[SupportedPlatform] = Field(
        ...,
        min_length=1,
        description="Platforms where this query is relevant (Google, Facebook, Instagram, LinkedIn, X).",
    )
    intent: str = Field(
        ...,
        description="Search intent (e.g., Service Search, Business Discovery, Inspiration, B2B Search, Comparison).",
    )
    business_relevance: str = Field(
        ..., description="Why this query directly maps to the website's offerings."
    )
    target_audience: str = Field(
        ..., description="Customer segment likely to run this search."
    )


# ---------------------------------------------------------------------------
# Section 3: SEO Improvements
# ---------------------------------------------------------------------------


class SupportingContentIdea(BaseModel):
    """Recommended additional website page or content asset for search visibility."""

    content_type: Literal[
        "Service Landing Page",
        "Industry-Specific Page",
        "FAQ",
        "Blog Topic",
        "Location-Based Landing Page",
        "Supporting Business Article",
    ] = Field(..., description="Category of supporting content.")
    title: str = Field(..., description="Suggested page or article title.")
    purpose: str = Field(..., description="How this content improves SEO and conversions.")


class SEOImprovements(BaseModel):
    """Section 3: Actionable SEO recommendations grounded in site content."""

    primary_keyword: str = Field(..., description="Single highest-priority target keyword.")
    secondary_keywords: list[str] = Field(
        default_factory=list, description="Supporting secondary keywords."
    )
    long_tail_keywords: list[str] = Field(
        default_factory=list, description="Specific high-intent long-tail keywords."
    )
    relevant_business_keywords: list[str] = Field(
        default_factory=list, description="Domain and business-specific keywords."
    )
    current_title: str | None = Field(
        default=None, description="Existing HTML <title> extracted from the website."
    )
    recommended_title: str = Field(
        ..., description="Optimized SEO title incorporating the primary keyword (<= 60 chars ideal)."
    )
    title_improvement_notes: str = Field(
        ..., description="Why the recommended title structure improves click-through and rankings."
    )
    current_meta_description: str | None = Field(
        default=None, description="Existing meta description extracted from the website."
    )
    meta_description: str = Field(
        ...,
        description="Short, human-friendly meta description with relevant keywords (140-160 chars ideal).",
    )
    supporting_content: list[SupportingContentIdea] = Field(
        default_factory=list,
        description="Additional pages, FAQs, and articles to improve search visibility.",
    )


# ---------------------------------------------------------------------------
# Section 4: Top 5 Services
# ---------------------------------------------------------------------------


class ServiceItem(BaseModel):
    """One core service or product identified from the website."""

    service_name: str = Field(..., description="Name of the service.")
    description: str = Field(..., description="Short, concrete service explanation.")
    target_industry: str = Field(..., description="Relevant business industry.")
    target_customer: str = Field(..., description="Potential customer type.")
    main_benefit: str = Field(..., description="Primary value or outcome for customers.")
    source_type: Literal["extracted_from_website", "inferred_recommendation"] = Field(
        default="extracted_from_website",
        description="Clearly distinguishes services found on-site vs inferred recommendations.",
    )


# ---------------------------------------------------------------------------
# Section 5: Target Audience Identification
# ---------------------------------------------------------------------------


class CustomerProfile(BaseModel):
    """Structured customer persona/segment."""

    segment_name: str = Field(..., description="Audience segment label (e.g., Small Businesses).")
    business_type: str = Field(..., description="Type of organization or buyer.")
    requirement: str = Field(..., description="Core problem or requirement they are solving.")
    profile_summary: str = Field(..., description="Concise profile of this customer group.")


class TargetAudience(BaseModel):
    """Section 5: Comprehensive target audience breakdown."""

    primary_audience: list[str] = Field(
        default_factory=list, description="Core primary target audience groups."
    )
    secondary_audience: list[str] = Field(
        default_factory=list, description="Secondary or adjacent target audience groups."
    )
    industries: list[str] = Field(
        default_factory=list, description="Relevant industry segments."
    )
    business_types: list[str] = Field(
        default_factory=list, description="Business sizes/types (e.g., Startups, SMBs, Enterprise)."
    )
    customer_profiles: list[CustomerProfile] = Field(
        default_factory=list, description="Detailed customer profiles with requirements."
    )
    geographical_focus: str = Field(
        default="Not explicitly specified on website",
        description="Location or geographical focus if mentioned on-site.",
    )
    customer_needs: list[str] = Field(
        default_factory=list, description="Key customer needs and pain points."
    )
    customer_challenges: list[str] = Field(
        default_factory=list, description="Specific challenges customers face before buying."
    )
    potential_interests: list[str] = Field(
        default_factory=list, description="Topics and interests relevant for targeting."
    )
    recommended_marketing_segments: list[str] = Field(
        default_factory=list, description="Actionable audience segments for marketing campaigns."
    )


# ---------------------------------------------------------------------------
# Combined Analysis & Full API Response Schemas
# ---------------------------------------------------------------------------


class AnalysisSections(BaseModel):
    """All five functional analysis sections."""

    competitor_analysis: CompetitorAnalysis
    target_search_queries: list[TargetSearchQuery] = Field(
        ..., min_length=1, max_length=10
    )
    seo_improvements: SEOImprovements
    top_services: list[ServiceItem] = Field(..., min_length=1, max_length=10)
    target_audience: TargetAudience


class LLMAnalysisPayload(BaseModel):
    """Exact structured payload requested from the LLM."""

    brand: BrandDetails
    analysis: AnalysisSections


class TokenUsage(BaseModel):
    """Actual token counts reported by the LLM provider and estimated USD cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None


class AnalysisMetadata(BaseModel):
    """Operational telemetry for transparency, benchmarking, and cost tracking."""

    provider: str
    model: str
    cached: bool = False
    latency_ms: int
    token_usage: TokenUsage
    extracted_pages: list[str] = Field(default_factory=list)


class LinkAnalysisResponse(BaseModel):
    """Top-level JSON response for POST /link-analysis."""

    success: bool = True
    website: str
    brand: BrandDetails
    analysis: AnalysisSections
    metadata: AnalysisMetadata


class ErrorResponse(BaseModel):
    """Structured error response returned on validation, extraction, or LLM failures."""

    success: bool = False
    error_code: str
    message: str
    details: str | None = None


# ---------------------------------------------------------------------------
# Internal Extraction Data Model
# ---------------------------------------------------------------------------


class ExtractedWebsiteContent(BaseModel):
    """Cleaned, structured representation of scraped website content."""

    url: str
    final_url: str
    title: str | None = None
    meta_description: str | None = None
    meta_keywords: list[str] = Field(default_factory=list)
    og_site_name: str | None = None
    og_title: str | None = None
    og_description: str | None = None
    language: str | None = None
    headings: list[str] = Field(default_factory=list)
    navigation_links: list[str] = Field(default_factory=list)
    structured_data_snippets: list[str] = Field(default_factory=list)
    main_text: str = ""
    extracted_pages: list[str] = Field(default_factory=list)
