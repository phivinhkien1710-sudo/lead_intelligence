from __future__ import annotations

import re

from lead_intelligence.common.models import CompanyInput, LeadRecord, WebsiteSnapshot


EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
TITLE_WORDS = (
    "chief executive officer",
    "managing director",
    "general manager",
    "director",
    "founder",
    "co-founder",
    "owner",
    "partner",
    "principal",
    "president",
    "chairman",
)


def first_email(text: str) -> str:
    for match in EMAIL_RE.findall(text or ""):
        if not match.lower().startswith(("noreply@", "no-reply@")):
            return match
    return ""


def first_phone(text: str) -> str:
    match = PHONE_RE.search(text or "")
    return " ".join(match.group(0).split()) if match else ""


def possible_title(text: str) -> str:
    lower = (text or "").lower()
    for title in TITLE_WORDS:
        if title in lower:
            return title.title()
    return ""


def missing_fields(lead: LeadRecord) -> list[str]:
    important = ["website", "email", "representative_name", "description"]
    return [field for field in important if not getattr(lead, field)]


def build_initial_lead(company: CompanyInput) -> LeadRecord:
    return LeadRecord(
        region=company.region,
        source=company.source,
        source_id=company.source_id,
        company_name=company.company_name,
        website=company.website,
        email=company.email,
        phone=company.phone,
        representative_name=company.representative_name,
        position=company.position,
        description=company.description,
        industry=company.industry,
        source_url=company.source_url,
        enrichment_status="loaded",
        confidence=0.5,
        raw={"input": company.raw},
    )


def apply_rule_based_extraction(lead: LeadRecord, snapshot: WebsiteSnapshot | None) -> LeadRecord:
    if not snapshot:
        lead.missing_fields = missing_fields(lead)
        lead.enrichment_status = "missing_website" if not lead.website else "website_unreadable"
        return lead

    text = snapshot.text
    lead.website = lead.website or snapshot.website
    lead.source_url = lead.source_url or snapshot.source_url
    lead.email = lead.email or first_email(text)
    lead.phone = lead.phone or first_phone(text)
    lead.position = lead.position or possible_title(text)
    if not lead.description and text:
        lead.description = text[:600]
    lead.raw["website_snapshot"] = {
        "website": snapshot.website,
        "source_url": snapshot.source_url,
        "pages_scraped": snapshot.pages_scraped,
    }
    lead.missing_fields = missing_fields(lead)
    lead.enrichment_status = "rules_complete" if not lead.missing_fields else "rules_partial"
    lead.confidence = 0.75 if not lead.missing_fields else 0.6
    return lead

