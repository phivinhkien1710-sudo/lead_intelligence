from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lead_intelligence.common.config import DATABASE_DIR
from lead_intelligence.enrichment.pipeline import EnrichmentSettings, enrich_company
from lead_intelligence.regions.vietnam.factlink import iter_factlink_companies


@dataclass(slots=True)
class VietnamPipelineSettings:
    input_csv: Path = Path("data/factlink_companies_all.csv")
    db_path: Path = DATABASE_DIR / "vietnam_leads.db"
    limit: int | None = None
    enrichment: EnrichmentSettings | None = None


def run_vietnam_pipeline(settings: VietnamPipelineSettings) -> dict[str, int]:
    enrichment = settings.enrichment or EnrichmentSettings(db_path=settings.db_path)
    processed = saved = 0
    for company in iter_factlink_companies(settings.input_csv, limit=settings.limit):
        processed += 1
        lead = enrich_company(company, enrichment)
        saved += 1
        print(f"[vietnam] {processed}: {lead.company_name} -> {lead.website or lead.enrichment_status}")
    return {"processed": processed, "saved": saved}

