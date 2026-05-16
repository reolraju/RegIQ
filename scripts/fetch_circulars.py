"""Fetch the latest RBI and SEBI circulars and stage them for ingestion.

Strategy (in priority order):
  1. Parse official RSS feeds — stable, low-bandwidth, officially maintained.
  2. Fall back to scraping the HTML listing page if the RSS yields nothing.

Each RSS item either links directly to a PDF or to an HTML page that contains
a PDF link.  We follow one level of indirection to find the PDF.

Invoked by the daily GitHub Actions workflow:
  1. Discover new circulars via RSS (+ HTML fallback).
  2. Download PDFs not already in the manifest.
  3. Save under `ingestion/sample_docs/` for the next deploy to re-ingest.
  4. Persist the manifest so repeat runs are idempotent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "ingestion" / "sample_docs"
MANIFEST_PATH = Path(__file__).resolve().parent / "fetched_manifest.json"

USER_AGENT = "RegIQ-bot/1.0 (+https://github.com/reolraju/regiq)"
REQUEST_TIMEOUT = 30
MAX_PER_RUN = 10   # cap per regulator per run so the commit stays manageable
POLITE_DELAY = 1.5  # seconds between requests

# ---------------------------------------------------------------------------
# Source definitions
# ---------------------------------------------------------------------------

# RSS feeds are the primary discovery mechanism.
# Each entry can also carry a fallback `index_url` + `link_selector` for when
# the RSS feed is empty or unreachable.

SOURCES: list[dict] = [
    {
        "name": "RBI Notifications",
        "regulator": "RBI",
        # RBI publishes an Atom/RSS feed for all notifications/circulars.
        "rss_url": "https://www.rbi.org.in/rss.xml",
        # Fallback: the HTML listing page.  RBI's ASP.NET page requires
        # cookies/viewstate so we try the simpler direct PDF search instead.
        "index_url": "https://www.rbi.org.in/Scripts/BS_CircularIndexDisplay.aspx",
        "link_selector": "a[href$='.pdf'], a[href$='.PDF']",
        "base_url": "https://www.rbi.org.in",
    },
    {
        "name": "SEBI Circulars",
        "regulator": "SEBI",
        "rss_url": "https://www.sebi.gov.in/sebirss.xml",
        # Fallback: SEBI circular listing.
        "index_url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=6&smid=0",
        "link_selector": "a[href*='.pdf'], a[href*='.PDF']",
        "base_url": "https://www.sebi.gov.in",
    },
]


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "files": {}}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _http_get(session: requests.Session, url: str) -> requests.Response | None:
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        log.warning("GET %s failed: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# PDF link discovery
# ---------------------------------------------------------------------------

def _is_pdf_url(url: str) -> bool:
    return url.lower().endswith(".pdf")


def _extract_pdf_from_page(session: requests.Session, page_url: str, base_url: str) -> str | None:
    """Fetch an HTML page and return the first PDF link found on it."""
    resp = _http_get(session, page_url)
    if resp is None:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        absolute = urljoin(base_url, href)
        if _is_pdf_url(absolute):
            return absolute
    return None


def _discover_via_rss(session: requests.Session, source: dict) -> list[str]:
    """Parse the RSS feed and return a list of PDF URLs (may require one hop
    through an HTML landing page to find the actual PDF)."""
    rss_url = source.get("rss_url", "")
    if not rss_url:
        return []

    log.info("%s: fetching RSS %s", source["name"], rss_url)
    try:
        feed = feedparser.parse(rss_url, agent=USER_AGENT, request_headers={"User-Agent": USER_AGENT})
    except Exception as e:
        log.warning("%s: RSS parse error: %s", source["name"], e)
        return []

    if feed.bozo:
        log.warning("%s: RSS has parse warnings (bozo): %s", source["name"], feed.bozo_exception)

    pdf_urls: list[str] = []
    base_url = source.get("base_url", "")

    for entry in feed.entries:
        if len(pdf_urls) >= MAX_PER_RUN:
            break

        link = entry.get("link", "")
        if not link:
            continue

        if _is_pdf_url(link):
            pdf_urls.append(link)
        else:
            # Try to find a PDF on the linked HTML page
            time.sleep(POLITE_DELAY)
            pdf = _extract_pdf_from_page(session, link, base_url)
            if pdf:
                pdf_urls.append(pdf)
            else:
                log.debug("%s: no PDF found on %s", source["name"], link)

    # Deduplicate preserving order
    seen: set[str] = set()
    result: list[str] = []
    for u in pdf_urls:
        if u not in seen:
            seen.add(u)
            result.append(u)

    log.info("%s: RSS discovered %d PDF link(s)", source["name"], len(result))
    return result


def _discover_via_html(session: requests.Session, source: dict) -> list[str]:
    """Fall back to scraping the listing HTML page."""
    index_url = source.get("index_url", "")
    if not index_url:
        return []

    log.info("%s: falling back to HTML scrape %s", source["name"], index_url)
    resp = _http_get(session, index_url)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    raw: list[str] = []
    for a in soup.select(source.get("link_selector", "a[href$='.pdf']")):
        href = a.get("href")
        if not href:
            continue
        raw.append(urljoin(index_url, href))

    seen: set[str] = set()
    result: list[str] = []
    for u in raw:
        if u not in seen:
            seen.add(u)
            result.append(u)

    result = result[:MAX_PER_RUN]
    log.info("%s: HTML scrape found %d PDF link(s)", source["name"], len(result))
    return result


def _discover_pdfs(session: requests.Session, source: dict) -> list[str]:
    """Return PDF URLs for the source, trying RSS first then HTML fallback."""
    urls = _discover_via_rss(session, source)
    if not urls:
        urls = _discover_via_html(session, source)
    return urls


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _safe_filename(regulator: str, url: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1].split("?")[0])
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return f"{regulator.lower()}__{base}"


def _download_pdf(session: requests.Session, url: str, dest: Path) -> str | None:
    resp = _http_get(session, url)
    if resp is None:
        return None
    content = resp.content
    if len(content) < 1024:
        log.warning("Suspiciously small response (%d bytes) for %s — skipping", len(content), url)
        return None
    sha = hashlib.sha256(content).hexdigest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    log.info("Downloaded %s -> %s (%d bytes)", url, dest.name, len(content))
    return sha


# ---------------------------------------------------------------------------
# Per-source fetch
# ---------------------------------------------------------------------------

def fetch_for_source(session: requests.Session, source: dict, manifest: dict) -> list[str]:
    """Discover and download new PDFs for one source.  Returns new local paths."""
    pdf_urls = _discover_pdfs(session, source)
    new_files: list[str] = []

    for pdf_url in pdf_urls:
        if pdf_url in manifest["files"]:
            log.info("Skipping (already fetched): %s", pdf_url)
            continue

        dest = DOCS_DIR / _safe_filename(source["regulator"], pdf_url)
        time.sleep(POLITE_DELAY)
        sha = _download_pdf(session, pdf_url, dest)
        if sha is None:
            continue

        manifest["files"][pdf_url] = {
            "filename": dest.name,
            "regulator": source["regulator"],
            "sha256": sha,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        new_files.append(str(dest.relative_to(REPO_ROOT)))

    return new_files


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Discover PDF links only; do not download or update the manifest",
    )
    args = parser.parse_args(argv)

    manifest = _load_manifest()
    session = _make_session()

    if args.dry_run:
        for source in SOURCES:
            urls = _discover_pdfs(session, source)
            for u in urls:
                log.info("[dry-run] would fetch: %s", u)
        return 0

    new_files: list[str] = []
    for source in SOURCES:
        new_files.extend(fetch_for_source(session, source, manifest))

    _save_manifest(manifest)

    if new_files:
        log.info("Fetched %d new circular(s):", len(new_files))
        for f in new_files:
            log.info("  - %s", f)
    else:
        log.info("No new circulars this run.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
