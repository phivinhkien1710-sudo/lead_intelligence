from __future__ import annotations

import json
from typing import Any

import requests

from lead_intelligence.common.models import LeadRecord


def should_use_tavily(lead: LeadRecord) -> bool:
    return bool(set(lead.missing_fields) & {"website", "email", "representative_name"})


def enrich_with_tavily(lead: LeadRecord, *, api_key: str, timeout: int = 30) -> LeadRecord:
    if not api_key or not should_use_tavily(lead):
        return lead
    query = f"{lead.company_name} official website email founder director"
    response = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "search_depth": "basic", "max_results": 5},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    lead.raw.setdefault("fallbacks", {})["tavily"] = payload
    for item in payload.get("results", []):
        url = item.get("url") or ""
        if "website" in lead.missing_fields and url.startswith(("http://", "https://")):
            lead.website = url
            break
    return lead


def should_use_openai(lead: LeadRecord) -> bool:
    return bool(lead.missing_fields) and bool(lead.raw)


def structure_with_openai(
    lead: LeadRecord,
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
    timeout: int = 30,
) -> LeadRecord:
    if not api_key or not should_use_openai(lead):
        return lead

    prompt = {
        "company_name": lead.company_name,
        "known_fields": {
            "website": lead.website,
            "email": lead.email,
            "representative_name": lead.representative_name,
            "position": lead.position,
            "description": lead.description[:1000],
        },
        "missing_fields": lead.missing_fields,
        "raw_context": json.dumps(lead.raw, ensure_ascii=False)[:6000],
    }
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "Extract only facts supported by the context. "
                        "Return strict JSON with keys website, email, representative_name, "
                        "position, description, confidence, notes."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "text": {"format": {"type": "json_object"}},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    text = payload.get("output_text", "")
    try:
        extracted = json.loads(text) if text else {}
    except json.JSONDecodeError:
        extracted = {}
    lead.raw.setdefault("fallbacks", {})["openai"] = extracted or payload
    for field in ("website", "email", "representative_name", "position", "description"):
        value = extracted.get(field) if isinstance(extracted, dict) else ""
        if value and not getattr(lead, field):
            setattr(lead, field, str(value))
    if isinstance(extracted, dict) and extracted.get("confidence"):
        try:
            lead.confidence = max(lead.confidence, float(extracted["confidence"]))
        except ValueError:
            pass
    return lead

