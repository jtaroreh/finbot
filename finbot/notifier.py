"""Create a dedicated GitHub Issue for an entry snapshot; skip duplicates."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from finbot.strategy import ScanResult, format_report, issue_marker, issue_title

logger = logging.getLogger(__name__)

GITHUB_API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
ENTRY_LABEL = "entry-alert"
API_VERSION = "2022-11-28"


class NotifyError(RuntimeError):
    """Raised when GitHub Issue creation or lookup fails."""


def maybe_create_issue(result: ScanResult, dry_run: bool = False) -> str | None:
    """Create one issue for a triggered entry. Returns the issue URL or None."""
    if not result.entry_triggered:
        logger.info("%s %s: no entry — skipping Issue", result.ticker, result.signal_date)
        return None
    if dry_run:
        logger.info("%s %s: dry-run — Issue not created", result.ticker, result.signal_date)
        return None

    title = issue_title(result.ticker, result.signal_date)
    existing = find_existing_issue(title, result.ticker, result.signal_date)
    if existing is not None:
        logger.info(
            "%s %s: existing Issue #%s (%s) — skipping",
            result.ticker,
            result.signal_date,
            existing.get("number"),
            existing.get("html_url"),
        )
        return str(existing.get("html_url") or "")

    body = format_report(result)
    labels = [ENTRY_LABEL, result.ticker.upper()]
    _ensure_labels(labels)
    payload = {"title": title, "body": body, "labels": labels}
    response = _request("POST", f"/repos/{_repo()}/issues", json=payload)
    if response.status_code >= 400:
        raise NotifyError(f"Create issue failed ({response.status_code}): {response.text}")
    data = response.json()
    url = data.get("html_url", "")
    logger.info("Created Issue #%s %s", data.get("number"), url)
    return str(url)


def find_existing_issue(title: str, ticker: str, signal_date: object) -> dict[str, Any] | None:
    marker = issue_marker(ticker, signal_date)  # type: ignore[arg-type]
    repo = _repo()

    search = _request(
        "GET",
        "/search/issues",
        params={"q": f'repo:{repo} is:issue in:title "{title}"'},
    )
    if search.status_code == 200:
        items = search.json().get("items") or []
        for item in items:
            if item.get("title") == title:
                return item
            body = item.get("body") or ""
            if marker in body:
                return item

    page = None
    url = f"/repos/{repo}/issues"
    params: dict[str, Any] = {"state": "all", "labels": ENTRY_LABEL, "per_page": 100}
    while True:
        listed = _request("GET", url if page is None else page, params=None if page else params)
        if listed.status_code >= 400:
            raise NotifyError(f"List issues failed ({listed.status_code}): {listed.text}")
        for item in listed.json():
            if item.get("pull_request"):
                continue
            if item.get("title") == title:
                return item
            body = item.get("body") or ""
            if marker in body:
                return item
        next_url = _next_link(listed.headers.get("Link", ""))
        if not next_url:
            break
        page = next_url
        if page.startswith(GITHUB_API):
            page = page[len(GITHUB_API) :]
    return None


def _ensure_labels(names: list[str]) -> None:
    repo = _repo()
    colors = {ENTRY_LABEL: "1f883d", names[-1]: "0b6e99"}
    descriptions = {
        ENTRY_LABEL: "Finbot high-confluence long entry snapshot",
    }
    for name in names:
        response = _request(
            "POST",
            f"/repos/{repo}/labels",
            json={
                "name": name,
                "color": colors.get(name, "0b6e99"),
                "description": descriptions.get(name, f"Finbot ticker {name}"),
            },
        )
        if response.status_code in (201, 422):
            continue
        if response.status_code >= 400:
            logger.warning("Could not ensure label %s (%s): %s", name, response.status_code, response.text)


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo or "/" not in repo:
        raise NotifyError("GITHUB_REPOSITORY is not set (expected owner/repo)")
    return repo


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise NotifyError("GITHUB_TOKEN is not set")
    return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _request(method: str, path: str, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> requests.Response:
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        url = f"{GITHUB_API}{path}"
    return requests.request(method, url, headers=_headers(), params=params, json=json, timeout=30)


def _next_link(header: str) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        section = part.strip()
        if 'rel="next"' in section:
            start = section.find("<")
            end = section.find(">")
            if start != -1 and end != -1:
                return section[start + 1 : end]
    return None
