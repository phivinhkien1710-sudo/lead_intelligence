from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lead_intelligence.common.models import CompanyInput, LeadRecord
from lead_intelligence.common.storage import save_lead
from lead_intelligence.enrichment.fallbacks import enrich_with_tavily, structure_with_openai
from lead_intelligence.enrichment.rules import apply_rule_based_extraction, build_initial_lead, missing_fields
from lead_intelligence.enrichment.website import scrape_website


@dataclass(slots=True)
class EnrichmentSettings:
    db_path: Path
    tavily_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    use_tavily: bool = True
    use_openai: bool = True
    scrape_delay: float = 0.2
    scrape_timeout: int = 15
    max_pages: int = 5


def enrich_company(company: CompanyInput, settings: EnrichmentSettings) -> LeadRecord:
    lead = build_initial_lead(company)

    snapshot = scrape_website(
        lead.website,
        max_pages=settings.max_pages,
        delay=settings.scrape_delay,
        timeout=settings.scrape_timeout,
    )
    lead = apply_rule_based_extraction(lead, snapshot)

    if lead.missing_fields and settings.use_tavily:
        lead = enrich_with_tavily(lead, api_key=settings.tavily_api_key)
        lead.missing_fields = missing_fields(lead)

    if lead.missing_fields and settings.use_openai:
        lead = structure_with_openai(lead, api_key=settings.openai_api_key, model=settings.openai_model)
        lead.missing_fields = missing_fields(lead)

    lead.enrichment_status = "complete" if not lead.missing_fields else "partial"
    save_lead(settings.db_path, lead)
    return lead

