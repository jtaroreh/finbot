"""GitHub Issue + Grok Bot webhook alerts for an entry snapshot."""

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
WEBHOOK_URL_ENV = "FINBOT_GROK_WEBHOOK_URL"
WEBHOOK_SECRET_ENV = "FINBOT_GROK_WEBHOOK_SECRET"


class NotifyError(RuntimeError):
    """Raised when GitHub Issue or Grok Bot webhook notify fails."""


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


def notify(result: ScanResult, dry_run: bool = False) -> str | None:
    """Issue only on technical ENTRY; webhook on every successful scan when secrets exist."""
    issue_url: str | None = None
    issue_error: NotifyError | None = None
    try:
        issue_url = maybe_create_issue(result, dry_run=dry_run)
    except NotifyError as exc:
        issue_error = exc
        logger.error("%s: Issue notify failed: %s", result.ticker, exc)

    webhook_error: NotifyError | None = None
    try:
        maybe_post_webhook(result, issue_url=issue_url, dry_run=dry_run)
    except NotifyError as exc:
        webhook_error = exc
        logger.error("%s: webhook notify failed: %s", result.ticker, exc)

    if issue_error and webhook_error:
        raise NotifyError(f"{issue_error}; {webhook_error}") from webhook_error
    if issue_error:
        raise issue_error
    if webhook_error:
        raise webhook_error
    return issue_url


def alert_payload(result: ScanResult, issue_url: str | None = None) -> dict[str, Any]:
    snap = result.snapshot
    targets = dict(result.levels.targets) if result.levels is not None else {}
    confluence = bool(result.entry_triggered)
    payload: dict[str, Any] = {
        "ticker": result.ticker,
        "signal": "ENTRY" if confluence else "NO_ENTRY",
        "confluence": confluence,
        "signal_date": result.signal_date.isoformat(),
        "summary": result.summary,
        "entry": _json_num(result.levels.entry if result.levels else snap.close),
        "stop": _json_num(result.levels.stop if result.levels else None),
        "target_2r": _json_num(targets.get(2)),
        "target_3r": _json_num(targets.get(3)),
        "gates": {
            gate.name: {"passed": gate.passed, "detail": gate.detail} for gate in result.gates
        },
        "indicators": {
            "open": _json_num(snap.open),
            "high": _json_num(snap.high),
            "low": _json_num(snap.low),
            "close": _json_num(snap.close),
            "volume": _json_num(snap.volume),
            "volume_avg_20": _json_num(snap.volume_avg_20),
            "volume_multiple": _json_num(snap.volume_multiple),
            "sma_20": _json_num(snap.sma_20),
            "sma_50": _json_num(snap.sma_50),
            "sma_200": _json_num(snap.sma_200),
            "rsi_14": _json_num(snap.rsi_14),
            "atr_14": _json_num(snap.atr_14),
            "swing_support": _json_num(snap.swing_support),
            "swing_resistance": _json_num(snap.swing_resistance),
        },
        "earnings": {
            "next": result.next_earnings.isoformat() if result.next_earnings else None,
            "blackout_passed": result.gate("earnings").passed,
            "detail": result.gate("earnings").detail,
        },
    }
    if issue_url:
        payload["issue_url"] = issue_url
    return payload


def format_notify_plan(result: ScanResult, dry_run: bool = False) -> str:
    """Human-readable dual-path plan: daily webhook vs Issue-only-on-ENTRY."""
    confluence = bool(result.entry_triggered)
    signal = "ENTRY" if confluence else "NO_ENTRY"
    url, secret = _webhook_credentials(result.ticker)
    if url and secret:
        if dry_run:
            webhook_line = (
                f"Webhook: would POST {result.ticker} snapshot every run "
                f"(signal={signal}, confluence={str(confluence).lower()}) — not sent in dry-run"
            )
        else:
            webhook_line = (
                f"Webhook: POST {result.ticker} snapshot "
                f"(signal={signal}, confluence={str(confluence).lower()})"
            )
    else:
        webhook_line = (
            f"Webhook: skipped ({WEBHOOK_URL_ENV} or {WEBHOOK_SECRET_ENV} unset)"
        )
    if confluence:
        title = issue_title(result.ticker, result.signal_date)
        if dry_run:
            issue_line = f"Issue: would create {title} (technical ENTRY) — not created in dry-run"
        else:
            issue_line = f"Issue: create {title} (technical ENTRY)"
    else:
        issue_line = "Issue: skipped (technical confluence did not pass)"
    return f"{webhook_line}\n{issue_line}\n"


def maybe_post_webhook(
    result: ScanResult,
    issue_url: str | None = None,
    dry_run: bool = False,
) -> bool:
    """POST one JSON snapshot per ticker every successful scan. Skip quietly when secrets are missing."""
    if dry_run:
        url, secret = _webhook_credentials(result.ticker)
        if url and secret:
            logger.info(
                "%s %s: dry-run — webhook would POST (signal=%s, confluence=%s)",
                result.ticker,
                result.signal_date,
                "ENTRY" if result.entry_triggered else "NO_ENTRY",
                str(bool(result.entry_triggered)).lower(),
            )
        else:
            logger.info(
                "%s %s: dry-run — webhook skipped (%s or %s unset)",
                result.ticker,
                result.signal_date,
                WEBHOOK_URL_ENV,
                WEBHOOK_SECRET_ENV,
            )
        return False

    url, secret = _webhook_credentials(result.ticker)
    if not url or not secret:
        logger.info(
            "%s %s: webhook skipped (%s or %s unset)",
            result.ticker,
            result.signal_date,
            WEBHOOK_URL_ENV,
            WEBHOOK_SECRET_ENV,
        )
        return False

    payload = alert_payload(result, issue_url=issue_url)
    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    if response.status_code != 200:
        raise NotifyError(
            f"Grok Bot webhook failed ({response.status_code}): {response.text[:500]}"
        )
    logger.info(
        "%s %s: posted Grok Bot webhook (signal=%s, confluence=%s)",
        result.ticker,
        result.signal_date,
        payload["signal"],
        payload["confluence"],
    )
    return True


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


def _webhook_credentials(ticker: str) -> tuple[str | None, str | None]:
    symbol = ticker.upper()
    url = _env(f"{WEBHOOK_URL_ENV}_{symbol}") or _env(WEBHOOK_URL_ENV)
    secret = _env(f"{WEBHOOK_SECRET_ENV}_{symbol}") or _env(WEBHOOK_SECRET_ENV)
    return url, secret


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _json_num(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


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
