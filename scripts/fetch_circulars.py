"""Fetch the latest RBI and SEBI circulars and stage them for ingestion.

Designed to be invoked by the weekly GitHub Actions workflow:

  1. Scrape the official notification/circular index pages.
  2. Download PDFs that we haven't already stored.
  3. Save them under `ingestion/sample_docs/` so the next deploy re-ingests.
  4. Maintain a manifest (`scripts/fetched_manifest.json`) so repeat runs are
     idempotent and the workflow only commits when something is genuinely new.

The actual page DOM and download URLs on the regulators' sites change
periodically. We isolate them in `RBI_SOURCES` / `SEBI_SOURCES` and keep the
scraper defensive — if a source 404s or yields no matches we log and move on
rather than failing the workflow.
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

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "ingestion" / "sample_docs"
MANIFEST_PATH = Path(__file__).resolve().parent / "fetched_manifest.json"

USER_AGENT = "RegIQ-bot/1.0 (+https://github.com/reolraju/regiq)"
REQUEST_TIMEOUT = 30
MAX_PER_SOURCE = 5  # cap weekly intake per regulator so PRs stay reviewable

RBI_SOURCES = [
    {
        "name": "RBI Notifications",
        "regulator": "RBI",
        "index_url": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "link_selector": "a[href$='.PDF'], a[href$='.pdf']",
    },
]

SEBI_SOURCES = [
    {
        "name": "SEBI Circulars",
        "regulator": "SEBI",
        "index_url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=6&smid=0",
        "link_selector": "a[href*='.pdf'], a[href*='.PDF']",
    },
]


def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "files": {}}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _safe_filename(regulator: str, url: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1])
    if not base.lower().endswith(".pdf"):
        base = base + ".pdf"
    return f"{regulator.lower()}__{base}"


def _http_get(session: requests.Session, url: str) -> requests.Response | None:
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        log.warning("GET %s failed: %s", url, e)
        return None


def _discover_pdf_links(session: requests.Session, source: dict) -> list[str]:
    resp = _http_get(session, source["index_url"])
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    raw_links = []
    for a in soup.select(source["link_selector"]):
        href = a.get("href")
        if not href:
            continue
        absolute = urljoin(source["index_url"], href)
        raw_links.append(absolute)
    # de-duplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for url in raw_links:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    log.info("%s: found %d unique PDF links", source["name"], len(deduped))
    return deduped[:MAX_PER_SOURCE]


def _download_pdf(session: requests.Session, url: str, dest: Path) -> str | None:
    resp = _http_get(session, url)
    if resp is None:
        return None
    content = resp.content
    sha = hashlib.sha256(content).hexdigest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    log.info("Downloaded %s -> %s (%d bytes)", url, dest.name, len(content))
    return sha


def fetch_for_source(session: requests.Session, source: dict, manifest: dict) -> list[str]:
    """Download new PDFs for one source. Returns the list of new local file paths."""
    pdf_urls = _discover_pdf_links(session, source)
    new_files: list[str] = []
    for pdf_url in pdf_urls:
        if pdf_url in manifest["files"]:
            log.info("Skipping (already fetched): %s", pdf_url)
            continue
        dest = DOCS_DIR / _safe_filename(source["regulator"], pdf_url)
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
        time.sleep(1)  # be polite
    return new_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Discover only, don't download")
    args = parser.parse_args(argv)

    manifest = _load_manifest()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    if args.dry_run:
        for source in RBI_SOURCES + SEBI_SOURCES:
            _discover_pdf_links(session, source)
        return 0

    new_files: list[str] = []
    for source in RBI_SOURCES + SEBI_SOURCES:
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
