"""Late GitHub Actions runs (after 00:00 UTC) must grade the session that just closed.

Regression for runs 38489519804 (2026-10-14 00:19Z graded 2026-10-12) and
39347325235 (2026-10-20 00:23Z graded 2026-10-16 and re-sent QTUM's Friday ENTRY).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from finbot import cli
from finbot.notifier import WEBHOOK_SENT_LABEL, IssueRef, notify
from finbot.sessions import expected_session
from tests.test_notifier import _entry_result


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("now", "session"),
    [
        ("2026-10-20T00:23:13", date(2026, 10, 19)),  # Mon 8:23pm EDT (run 39347325235)
        ("2026-10-14T00:19:36", date(2026, 10, 13)),  # Tue 8:19pm EDT (run 38489519804)
        ("2026-10-16T23:37:45", date(2026, 10, 16)),  # Fri 7:37pm EDT, before midnight UTC
        ("2026-10-06T01:46:09", date(2026, 10, 5)),
        ("2026-10-19T19:00:00", date(2026, 10, 16)),  # Mon 3pm EDT: Monday not closed yet
        ("2026-10-19T20:00:00", date(2026, 10, 19)),  # Mon 4pm EDT close
        ("2026-10-18T02:00:00", date(2026, 10, 16)),  # weekend
        ("2026-11-03T00:30:00", date(2026, 11, 2)),  # Mon 7:30pm EST (winter offset)
        ("2026-11-27T01:00:00", date(2026, 11, 25)),  # Thanksgiving evening
        ("2026-04-04T00:30:00", date(2026, 4, 2)),  # Good Friday evening
        ("2026-06-20T00:30:00", date(2026, 6, 18)),  # Juneteenth (Fri) evening
        ("2027-01-02T01:00:00", date(2026, 12, 31)),  # New Year's Day 2027 (Fri)
    ],
)
def test_expected_session_uses_new_york_evening(now: str, session: date):
    assert expected_session(_utc(now)) == session


def _bars(last: str, n: int = 420) -> pd.DataFrame:
    idx = pd.bdate_range(end=last, periods=n)
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


LATE_RUN = _utc("2026-10-20T00:23:13")  # still Monday 2026-10-19 evening in New York


def _run(frames: list[pd.DataFrame], retries: int = 2):
    fetch = Mock(side_effect=frames)
    sleeps: list[float] = []
    with patch("finbot.cli.fetch_ohlcv", fetch), patch(
        "finbot.cli.fetch_earnings_dates", return_value=[]
    ), patch("finbot.cli.notify", return_value=None) as notify_mock:
        code = cli.run(
            ["--ticker", "QTUM", "--stale-retries", str(retries), "--stale-wait", "1"],
            now=lambda: LATE_RUN,
            sleep=sleeps.append,
        )
    return code, fetch, notify_mock, sleeps


def test_late_run_with_fresh_data_grades_that_day():
    code, fetch, notify_mock, sleeps = _run([_bars("2026-10-19")])
    assert code == 0
    assert fetch.call_count == 1
    assert sleeps == []
    (result,), _ = notify_mock.call_args
    assert result.signal_date == date(2026, 10, 19)


def test_late_run_with_stale_feed_does_not_regrade_previous_session():
    stale = _bars("2026-10-16")
    code, fetch, notify_mock, sleeps = _run([stale, stale, stale], retries=2)
    assert code == 1  # surfaced as a failure, not a silent re-alert of Friday
    assert fetch.call_count == 3
    assert sleeps == [1.0, 1.0]
    notify_mock.assert_not_called()


def test_late_run_recovers_when_feed_catches_up():
    code, fetch, notify_mock, sleeps = _run([_bars("2026-10-16"), _bars("2026-10-19")])
    assert code == 0
    assert sleeps == [1.0]
    assert notify_mock.call_count == 1
    (result,), _ = notify_mock.call_args
    assert result.signal_date == date(2026, 10, 19)


def _webhook_env(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_URL_IBM", "https://example.test/webhook")
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_SECRET_IBM", "crsr_test")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/finbot")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test")
    ok = Mock()
    ok.status_code = 200
    ok.text = "ok"
    return ok


def test_already_reported_session_does_not_resend_webhook(monkeypatch: pytest.MonkeyPatch):
    ok = _webhook_env(monkeypatch)
    issue = IssueRef(url="https://github.test/14", number=14, existed=True, webhook_sent=True)
    with patch("finbot.notifier.maybe_create_issue", return_value=issue), patch(
        "finbot.notifier.requests.post", return_value=ok
    ) as post, patch("finbot.notifier._request") as gh:
        assert notify(_entry_result()) == "https://github.test/14"
    post.assert_not_called()
    gh.assert_not_called()


def test_resend_overrides_already_reported(monkeypatch: pytest.MonkeyPatch):
    ok = _webhook_env(monkeypatch)
    issue = IssueRef(url="https://github.test/14", number=14, existed=True, webhook_sent=True)
    with patch("finbot.notifier.maybe_create_issue", return_value=issue), patch(
        "finbot.notifier.requests.post", return_value=ok
    ) as post, patch("finbot.notifier._request", return_value=ok):
        notify(_entry_result(), resend=True)
    post.assert_called_once()


@pytest.mark.parametrize("existed", [False, True])
def test_delivered_webhook_marks_issue(monkeypatch: pytest.MonkeyPatch, existed: bool):
    """New Issue, or an earlier run whose webhook failed: send, then label the Issue."""
    ok = _webhook_env(monkeypatch)
    issue = IssueRef(url="https://github.test/15", number=15, existed=existed, webhook_sent=False)
    with patch("finbot.notifier.maybe_create_issue", return_value=issue), patch(
        "finbot.notifier.requests.post", return_value=ok
    ) as post, patch("finbot.notifier._request", return_value=ok) as gh:
        notify(_entry_result())
    post.assert_called_once()
    method, path = gh.call_args[0]
    assert (method, path) == ("POST", "/repos/owner/finbot/issues/15/labels")
    assert gh.call_args[1]["json"] == {"labels": [WEBHOOK_SENT_LABEL]}


def test_failed_webhook_leaves_issue_unmarked(monkeypatch: pytest.MonkeyPatch):
    _webhook_env(monkeypatch)
    bad = Mock()
    bad.status_code = 400
    bad.text = "circuit breaker"
    issue = IssueRef(url="https://github.test/15", number=15, existed=False)
    with patch("finbot.notifier.maybe_create_issue", return_value=issue), patch(
        "finbot.notifier.requests.post", return_value=bad
    ), patch("finbot.notifier._request") as gh:
        with pytest.raises(Exception, match="400"):
            notify(_entry_result())
    gh.assert_not_called()


def test_existing_issue_reports_webhook_label(monkeypatch: pytest.MonkeyPatch):
    from finbot import notifier

    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/finbot")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test")
    existing = {
        "number": 14,
        "html_url": "https://github.test/14",
        "labels": [{"name": "entry-alert"}, {"name": WEBHOOK_SENT_LABEL}],
    }
    with patch("finbot.notifier.find_existing_issue", return_value=existing):
        ref = notifier.maybe_create_issue(_entry_result())
    assert ref == IssueRef(url="https://github.test/14", number=14, existed=True, webhook_sent=True)
