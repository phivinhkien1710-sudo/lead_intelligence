from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from lead_intelligence.common.http import fetch_html, make_session
from lead_intelligence.enrichment.website import normalize_website


BASE_URL = "https://recordowl.com"


@dataclass(frozen=True, slots=True)
class RecordOwlMatch:
    input_company: str
    matched_company: str = ""
    uen: str = ""
    website: str = ""
    recordowl_url: str = ""
    match_confidence: float = 0.0
    status: str = "not_found"


def normalize_company_name(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"\b(pte|ltd|limited|llp|llc|inc|corp|corporation|co|company)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def confidence(input_name: str, candidate_name: str) -> float:
    left = normalize_company_name(input_name)
    right = normalize_company_name(candidate_name)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.92
    return round(SequenceMatcher(None, left, right).ratio(), 4)


def json_ld_blocks(page_html: str) -> list[object]:
    soup = BeautifulSoup(page_html, "html.parser")
    blocks: list[object] = []
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or script.get_text("", strip=True)
        if not text:
            continue
        try:
            blocks.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return blocks


def extract_candidates(page_html: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for block in json_ld_blocks(page_html):
        entities = block.get("mainEntity", []) if isinstance(block, dict) else []
        if isinstance(entities, dict):
            entities = [entities]
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            url = entity.get("url") or ""
            name = entity.get("name") or ""
            identifier = entity.get("identifier") or {}
            uen = str(identifier.get("value") or "") if isinstance(identifier, dict) else ""
            if name and url and "/company/" in url and url not in seen_urls:
                candidates.append({"name": name, "uen": uen, "recordowl_url": url})
                seen_urls.add(url)
    if candidates:
        return candidates

    soup = BeautifulSoup(page_html, "html.parser")
    for link in soup.select('a[href*="/company/"]'):
        href = link.get("href", "")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        name = link.get_text(" ", strip=True)
        if name and url not in seen_urls:
            candidates.append({"name": name, "uen": "", "recordowl_url": url})
            seen_urls.add(url)
    return candidates


def extract_website_from_profile(page_html: str) -> str:
    for block in json_ld_blocks(page_html):
        if not isinstance(block, dict):
            continue
        same_as = block.get("sameAs")
        values = same_as if isinstance(same_as, list) else [same_as]
        for value in values:
            if isinstance(value, str):
                website = normalize_website(html.unescape(value))
                if website:
                    return website

    soup = BeautifulSoup(page_html, "html.parser")
    for label in soup.find_all(string=re.compile(r"^\s*Website\s*$", re.IGNORECASE)):
        container = label.find_parent()
        if not container:
            continue
        link = container.find_next("a", href=True)
        if link:
            website = normalize_website(link["href"])
            if website:
                return website
    return ""


def lookup_company_website(company_name: str, *, delay: float = 0.5, timeout: int = 20) -> RecordOwlMatch:
    session = make_session()
    search_url = f"{BASE_URL}/search?name={quote_plus(company_name)}"
    page_html = fetch_html(session, search_url, delay=delay, timeout=timeout)
    candidates = extract_candidates(page_html)
    if not candidates:
        return RecordOwlMatch(input_company=company_name, recordowl_url=search_url)

    candidates.sort(key=lambda row: confidence(company_name, row["name"]), reverse=True)
    best = candidates[0]
    score = confidence(company_name, best["name"])
    website = ""
    try:
        time.sleep(delay)
        profile_html = fetch_html(session, best["recordowl_url"], timeout=timeout)
        website = extract_website_from_profile(profile_html)
    except Exception:
        website = ""
    return RecordOwlMatch(
        input_company=company_name,
        matched_company=best["name"],
        uen=best["uen"],
        website=website,
        recordowl_url=best["recordowl_url"],
        match_confidence=score,
        status="matched_with_website" if website else "matched_no_website",
    )

