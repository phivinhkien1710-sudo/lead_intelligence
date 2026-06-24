from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CompanyInput:
    region: str
    source: str
    source_id: str
    company_name: str
    status: str = ""
    website: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    hq_country: str = ""
    representative_name: str = ""
    position: str = ""
    description: str = ""
    industry: str = ""
    source_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebsiteSnapshot:
    website: str
    source_url: str
    pages_scraped: int
    text: str


@dataclass(slots=True)
class LeadRecord:
    region: str
    source: str
    source_id: str
    company_name: str
    normalized_company_name: str = ""
    website: str = ""
    domain: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    hq_country: str = ""
    representative_name: str = ""
    position: str = ""
    description: str = ""
    industry: str = ""
    source_url: str = ""
    raw_source_path: str = ""
    enrichment_status: str = "pending"
    last_enriched_at: str = ""
    missing_fields: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

