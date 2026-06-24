from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from lead_intelligence.common.http import fetch_html, html_to_text, make_session
from lead_intelligence.common.models import WebsiteSnapshot


DEFAULT_PATHS = ("/", "/about", "/about-us", "/team", "/leadership", "/management", "/contact", "/contact-us")


def normalize_website(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        return ""
    return value.rstrip("/")


def scrape_website(website: str, *, max_pages: int = 5, delay: float = 0.2, timeout: int = 15) -> WebsiteSnapshot | None:
    website = normalize_website(website)
    if not website:
        return None

    session = make_session()
    texts: list[str] = []
    visited: set[str] = set()
    base_host = urlparse(website).netloc.lower()
    candidates = [urljoin(website + "/", path.lstrip("/")) for path in DEFAULT_PATHS]

    for url in candidates:
        if len(visited) >= max_pages or url in visited:
            continue
        if urlparse(url).netloc.lower() != base_host:
            continue
        visited.add(url)
        try:
            html = fetch_html(session, url, delay=delay, timeout=timeout)
        except Exception:
            continue
        texts.append(html_to_text(html))
        if len(visited) == 1:
            soup = BeautifulSoup(html, "html.parser")
            for link in soup.select("a[href]"):
                href = link.get("href", "")
                label = link.get_text(" ", strip=True).lower()
                if any(word in label for word in ("about", "team", "leadership", "contact", "management")):
                    candidates.append(urljoin(url, href))

    if not texts:
        return None
    return WebsiteSnapshot(website=website, source_url=website, pages_scraped=len(visited), text=" ".join(texts))

