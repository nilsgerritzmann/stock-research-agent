"""Talks to SEC EDGAR. The only module that makes requests to sec.gov.

Three endpoints are used, all free and official:
- company_tickers.json  : maps a ticker (MSFT) to the SEC's company id (CIK)
- submissions/CIK.json  : company profile (industry code, fiscal year end) and filing list
- companyfacts/CIK.json : every XBRL-tagged number the company has ever filed

Responses are cached on disk for a day, so re-running a company is fast
and does not put load on the SEC's servers.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / ".cache" / "sec"
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60

# SEC fair-access rule: max 10 requests per second. We stay well below.
_MIN_SECONDS_BETWEEN_REQUESTS = 0.15
_last_request_time = 0.0


class SecError(Exception):
    """Raised when EDGAR data cannot be retrieved or a ticker is unknown."""


def _user_agent() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent or "example.com" in agent:
        raise SecError(
            "SEC_USER_AGENT is not set. Copy .env.example to .env and add your name and email."
        )
    return agent


def _get_json(url: str, cache_name: str) -> dict:
    """GET a JSON document, using the disk cache when it is fresh."""
    global _last_request_time
    cache_file = CACHE_DIR / cache_name
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < CACHE_MAX_AGE_SECONDS:
        return json.loads(cache_file.read_text())

    wait = _MIN_SECONDS_BETWEEN_REQUESTS - (time.time() - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    try:
        response = requests.get(url, headers={"User-Agent": _user_agent()}, timeout=30)
    finally:
        _last_request_time = time.time()
    if response.status_code == 404:
        raise SecError(f"Not found on EDGAR: {url}")
    response.raise_for_status()
    data = response.json()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data))
    return data


def lookup_cik(ticker: str) -> tuple[int, str]:
    """Return (CIK, company name) for a ticker such as 'MSFT'."""
    data = _get_json("https://www.sec.gov/files/company_tickers.json", "company_tickers.json")
    ticker = ticker.upper().strip()
    for entry in data.values():
        if entry["ticker"].upper() == ticker:
            return int(entry["cik_str"]), entry["title"]
    raise SecError(f"Ticker '{ticker}' not found in the SEC's company list (US-listed companies only).")


def get_submissions(cik: int) -> dict:
    return _get_json(
        f"https://data.sec.gov/submissions/CIK{cik:010d}.json", f"submissions_{cik}.json"
    )


def get_company_facts(cik: int) -> dict:
    return _get_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", f"companyfacts_{cik}.json"
    )


def filing_url(cik: int, accession: str, primary_document: str | None = None) -> str:
    """Link to a filing. Points at the main document when known, else the filing index."""
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    if primary_document:
        return f"{folder}/{primary_document}"
    return f"{folder}/{accession}-index.htm"


def primary_documents(submissions: dict) -> dict[str, str]:
    """Map accession number -> main document file name, from the recent-filings list."""
    recent = submissions.get("filings", {}).get("recent", {})
    return dict(zip(recent.get("accessionNumber", []), recent.get("primaryDocument", [])))
