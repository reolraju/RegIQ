"""Fetch the latest RBI and SEBI circulars and stage them for ingestion.

Strategy (in priority order):
  1. Parse official RSS / Atom feeds with the stdlib XML parser
     (stable, no compiled deps, officially maintained by the regulators).
  2. Fall back to scraping the HTML listing page if the RSS yields nothing.

Retention policy: keeps the MAX_KEPT_PER_REGULATOR most recent circulars per
regulator and deletes older ones from disk + the manifest.  This caps git repo
size, ChromaDB size, and embedding costs permanently — good for demo use.
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

import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "ingestion" / "sample_docs"
MANIFEST_PATH = Path(__file__).resolve().parent / "fetched_manifest.json"

USER_AGENT = "RegIQ-bot/1.0 (+https://github.com/reolraju/regiq)"
REQUEST_TIMEOUT = 30
MAX_PER_RUN = 5              # max new downloads per regulator per daily run
MAX_KEPT_PER_REGULATOR = 30  # oldest files pruned when this cap is exceeded
POLITE_DELAY = 1.5           # seconds between HTTP requests

SOURCES: list[dict] = [
    {
        "name": "RBI Notifications",
        "regulator": "RBI",
        "rss_url": "https://rbi.org.in/notifications_rss.xml",
        "index_url": "https://www.rbi.org.in/Scripts/BS_CircularIndexDisplay.aspx",
        "link_selector": "a[href$='.pdf'], a[href$='.PDF']",
        "base_url": "https://www.rbi.org.in",
    },
    {
        "name": "SEBI Circulars",
        "regulator": "SEBI",
        "rss_url": "https://www.sebi.gov.in/sebirss.xml",
        "index_url": "https://www.sebi.gov.in/legal/circulars.html",
        "link_selector": "a[href*='.pdf'], a[href*='.PDF'], a[href*='sebi_data']",
        "base_url": "https://www.sebi.gov.in",
    },
]


# ---------------------------------------------------------------------------
# Manifest
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
# Retention — prune oldest files beyond the per-regulator cap
# ---------------------------------------------------------------------------

def _prune_old_files(manifest: dict, regulator: str) -> list[str]:
    """Delete the oldest circulars for *regulator* that exceed MAX_KEPT_PER_REGULATOR.

    Returns the list of pruned filenames (so callers can log them).
    """
    reg_entries = [
        (url, info)
        for url, info in manifest["files"].items()
        if info.get("regulator") == regulator
    ]
    # Sort oldest-first by fetch timestamp
    reg_entries.sort(key=lambda kv: kv[1].get("fetched_at", ""))

    excess = len(reg_entries) - MAX_KEPT_PER_REGULATOR
    if excess <= 0:
        return []

    pruned: list[str] = []
    for url, info in reg_entries[:excess]:
        filename = info.get("filename", "")
        filepath = DOCS_DIR / filename
        if filepath.exists():
            filepath.unlink()
            log.info("Pruned (retention cap): %s", filename)
        del manifest["files"][url]
        pruned.append(filename)

    return pruned


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
    return url.lower().split("?")[0].endswith(".pdf")


def _is_pdf_response(session: requests.Session, url: str) -> bool:
    """HEAD request to check if the URL serves PDF content regardless of its extension."""
    try:
        resp = session.head(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        ct = resp.headers.get("Content-Type", "")
        return "pdf" in ct.lower()
    except requests.RequestException:
        return False


def _extract_pdf_from_page(session: requests.Session, page_url: str, base_url: str) -> str | None:
    # Some regulators (e.g. SEBI) serve PDFs at .html URLs — check Content-Type first.
    if _is_pdf_response(session, page_url):
        return page_url
    resp = _http_get(session, page_url)
    if resp is None:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        absolute = urljoin(base_url, a["href"])
        if _is_pdf_url(absolute):
            return absolute
    return None


def _parse_rss_links(xml_bytes: bytes) -> list[str]:
    """Return URLs from an RSS/Atom feed: direct PDFs first, then HTML item links.

    Checks (in order per item):
      1. <enclosure url="..."/> — some feeds attach the PDF directly
      2. <link> text or href — the canonical item URL (may be an HTML page)
    Also scans <description> for embedded PDF hrefs.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("RSS XML parse error: %s", e)
        return []

    pdf_direct: list[str] = []
    item_links: list[str] = []

    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue

        item_link: str = ""
        for child in elem:
            child_tag = child.tag.rsplit("}", 1)[-1]
            if child_tag == "enclosure":
                url = child.get("url", "").strip()
                if url and _is_pdf_url(url):
                    pdf_direct.append(url)
            elif child_tag == "link" and not item_link:
                href = (child.get("href") or (child.text or "")).strip()
                if href:
                    item_link = href
            elif child_tag == "description":
                desc = child.text or ""
                for href in re.findall(r'href=["\']([^"\']+\.pdf)["\']', desc, re.IGNORECASE):
                    pdf_direct.append(href)

        if item_link:
            item_links.append(item_link)

    # Return direct PDF URLs first so we skip HTML resolution for those
    return pdf_direct + item_links


def _discover_via_rss(session: requests.Session, source: dict) -> list[str]:
    rss_url = source.get("rss_url", "")
    if not rss_url:
        return []

    log.info("%s: fetching RSS %s", source["name"], rss_url)
    resp = _http_get(session, rss_url)
    if resp is None:
        return []

    rss_links = _parse_rss_links(resp.content)
    log.info("%s: RSS feed has %d item(s)", source["name"], len(rss_links))

    base_url = source.get("base_url", "")
    pdf_urls: list[str] = []

    for link in rss_links:
        if len(pdf_urls) >= MAX_PER_RUN:
            break
        if _is_pdf_url(link):
            pdf_urls.append(link)
        else:
            time.sleep(POLITE_DELAY)
            pdf = _extract_pdf_from_page(session, link, base_url)
            if pdf:
                pdf_urls.append(pdf)

    seen: set[str] = set()
    result = [u for u in pdf_urls if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]
    log.info("%s: RSS resolved %d PDF link(s)", source["name"], len(result))
    return result


def _discover_via_html(session: requests.Session, source: dict) -> list[str]:
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
        if href:
            raw.append(urljoin(index_url, href))

    seen: set[str] = set()
    result = [u for u in raw if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]
    result = result[:MAX_PER_RUN]
    log.info("%s: HTML scrape found %d PDF link(s)", source["name"], len(result))
    return result


def _discover_pdfs(session: requests.Session, source: dict) -> list[str]:
    urls = _discover_via_rss(session, source)
    if not urls:
        urls = _discover_via_html(session, source)
    return urls


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _safe_filename(regulator: str, url: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1].split("?")[0])
    base = re.sub(r"\.html?$", "", base, flags=re.IGNORECASE)
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
# Per-source fetch + prune
# ---------------------------------------------------------------------------

def fetch_for_source(session: requests.Session, source: dict, manifest: dict) -> tuple[list[str], list[str]]:
    """Download new PDFs, then prune old ones beyond the cap.

    Returns (new_files, pruned_files) as lists of relative paths / filenames.
    """
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

    pruned = _prune_old_files(manifest, source["regulator"])
    return new_files, pruned


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

    total_new: list[str] = []
    total_pruned: list[str] = []
    for source in SOURCES:
        new, pruned = fetch_for_source(session, source, manifest)
        total_new.extend(new)
        total_pruned.extend(pruned)

    _save_manifest(manifest)

    if total_new:
        log.info("Fetched %d new circular(s):", len(total_new))
        for f in total_new:
            log.info("  + %s", f)
    if total_pruned:
        log.info("Pruned %d old circular(s):", len(total_pruned))
        for f in total_pruned:
            log.info("  - %s", f)
    if not total_new and not total_pruned:
        log.info("No changes this run.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
