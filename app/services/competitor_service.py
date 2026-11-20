"""Competitor post-processing, provenance verification, and brand comparison builder."""

from __future__ import annotations

from urllib.parse import urlparse

from app.schemas import (
    BrandComparisonRow,
    BrandDetails,
    CompetitorAnalysis,
    CompetitorProfile,
    ExtractedWebsiteContent,
)


class CompetitorService:
    """Validates, deduplicates, and enriches LLM-generated competitor analysis."""

    def refine(
        self,
        brand: BrandDetails,
        competitor_analysis: CompetitorAnalysis,
        extracted: ExtractedWebsiteContent,
    ) -> CompetitorAnalysis:
        """Ensure provenance transparency, remove self-duplicates, and populate comparison rows."""
        site_host = urlparse(extracted.final_url).netloc.lower().removeprefix("www.")
        brand_lower = brand.name.strip().lower()
        searchable_text = (
            f"{extracted.title or ''} {extracted.meta_description or ''} {extracted.main_text}"
        ).lower()

        cleaned_competitors: list[CompetitorProfile] = []
        seen_names: set[str] = set()

        for comp in competitor_analysis.competitors:
            comp_name_norm = comp.name.strip().lower()
            if not comp_name_norm or comp_name_norm == brand_lower or comp_name_norm in seen_names:
                continue

            # Exclude accidental self-listing via domain match
            if comp.website:
                comp_host = urlparse(comp.website).netloc.lower().removeprefix("www.")
                if comp_host and comp_host == site_host:
                    continue

            seen_names.add(comp_name_norm)

            # Enforce honest provenance: only mark 'website_mentioned' if name truly appears on site
            verified_basis = (
                "website_mentioned"
                if comp.source_basis == "website_mentioned" and comp_name_norm in searchable_text
                else "ai_inferred"
            )
            cleaned_competitors.append(comp.model_copy(update={"source_basis": verified_basis}))

        comparison_rows = list(competitor_analysis.comparison_summary)
        if not comparison_rows:
            comparison_rows = self._build_default_comparison_rows(
                brand, competitor_analysis, cleaned_competitors
            )

        inferred_count = sum(1 for c in cleaned_competitors if c.source_basis == "ai_inferred")
        if inferred_count > 0:
            methodology_note = (
                f"{inferred_count} of {len(cleaned_competitors)} identified competitor(s) are "
                "AI-generated suggestions based on extracted website positioning "
                "(source_basis='ai_inferred') and should be validated with live market research."
            )
        else:
            methodology_note = (
                "Competitors listed were directly referenced in the extracted website content."
            )

        return competitor_analysis.model_copy(
            update={
                "competitors": cleaned_competitors,
                "comparison_summary": comparison_rows,
                "methodology_note": methodology_note,
            }
        )

    @staticmethod
    def _build_default_comparison_rows(
        brand: BrandDetails,
        analysis: CompetitorAnalysis,
        competitors: list[CompetitorProfile],
    ) -> list[BrandComparisonRow]:
        lead_competitor = competitors[0] if competitors else None
        return [
            BrandComparisonRow(
                dimension="Brand Name",
                your_brand=brand.name,
                competitor_benchmark=lead_competitor.name if lead_competitor else "Category Leaders",
            ),
            BrandComparisonRow(
                dimension="Main Service",
                your_brand=", ".join(brand.main_products_and_services[:2]) or brand.category,
                competitor_benchmark=(
                    lead_competitor.main_service if lead_competitor else analysis.main_business_category
                ),
            ),
            BrandComparisonRow(
                dimension="Target Market",
                your_brand=brand.category,
                competitor_benchmark=(
                    lead_competitor.target_market if lead_competitor else "Broad Category Buyers"
                ),
            ),
            BrandComparisonRow(
                dimension="Key Strength",
                your_brand=analysis.strengths[0] if analysis.strengths else "Focused value proposition",
                competitor_benchmark=(
                    lead_competitor.strengths[0]
                    if lead_competitor and lead_competitor.strengths
                    else "Established market recognition"
                ),
            ),
            BrandComparisonRow(
                dimension="Opportunity",
                your_brand=(
                    analysis.opportunities[0]
                    if analysis.opportunities
                    else "Strengthen SEO and differentiated positioning"
                ),
                competitor_benchmark=(
                    lead_competitor.weaknesses_or_gaps[0]
                    if lead_competitor and lead_competitor.weaknesses_or_gaps
                    else "Differentiation in niche segments"
                ),
            ),
        ]
