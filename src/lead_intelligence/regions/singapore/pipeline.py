from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from lead_intelligence.common.config import DATABASE_DIR, DATA_DIR
from lead_intelligence.common.models import CompanyInput
from lead_intelligence.enrichment.pipeline import EnrichmentSettings, enrich_company
from lead_intelligence.enrichment.recordowl import lookup_company_website
from lead_intelligence.regions.singapore.acra import iter_company_inputs, write_live_acra_csv


@dataclass(slots=True)
class SingaporePipelineSettings:
    input_csv: Path = DATA_DIR / "acra_live_companies_for_recordowl.csv"
    external_dir: Path = Path("external data/ACRAInformationonCorporateEntities")
    db_path: Path = DATABASE_DIR / "singapore_leads.db"
    prepare_acra: bool = False
    use_recordowl: bool = True
    limit: int | None = None
    recordowl_delay: float = 0.5
    recordowl_timeout: int = 20
    enrichment: EnrichmentSettings | None = None


def discover_website(company: CompanyInput, settings: SingaporePipelineSettings) -> CompanyInput:
    if company.website or not settings.use_recordowl:
        return company
    match = lookup_company_website(
        company.company_name,
        delay=settings.recordowl_delay,
        timeout=settings.recordowl_timeout,
    )
    if match.website and match.match_confidence >= 0.9:
        company.website = match.website
        company.source_url = match.recordowl_url
        company.raw["recordowl"] = asdict(match)
    return company


def run_singapore_pipeline(settings: SingaporePipelineSettings) -> dict[str, int]:
    if settings.prepare_acra:
        total, live, written = write_live_acra_csv(settings.external_dir, settings.input_csv)
        print(f"[acra] total={total} live={live} written={written} csv={settings.input_csv}")

    enrichment = settings.enrichment or EnrichmentSettings(db_path=settings.db_path)
    processed = saved = 0
    for company in iter_company_inputs(settings.input_csv, limit=settings.limit):
        processed += 1
        company = discover_website(company, settings)
        lead = enrich_company(company, enrichment)
        saved += 1
        print(f"[singapore] {processed}: {lead.company_name} -> {lead.website or lead.enrichment_status}")
    return {"processed": processed, "saved": saved}
