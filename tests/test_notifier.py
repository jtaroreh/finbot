"""Webhook notify: POST when env is set, skip when unset."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import Mock, patch
import os

import pytest

from finbot.config import default_ticker
from finbot.indicators import Snapshot
from finbot.notifier import (
    NotifyError,
    alert_payload,
    maybe_post_webhook,
    notify,
)
from finbot.strategy import evaluate
from tests.test_strategy import _passing_snapshot


def _entry_result():
    snap: Snapshot = _passing_snapshot()
    return evaluate(snap, [snap.signal_date + timedelta(days=30)], default_ticker())


def _clear_webhook_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("FINBOT_GROK_WEBHOOK_"):
            monkeypatch.delenv(key, raising=False)


def test_webhook_skipped_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    result = _entry_result()
    assert result.entry_triggered
    _clear_webhook_env(monkeypatch)
    with patch("finbot.notifier.requests.post") as post:
        assert maybe_post_webhook(result) is False
        post.assert_not_called()


def test_webhook_skipped_when_only_url_set(monkeypatch: pytest.MonkeyPatch):
    result = _entry_result()
    _clear_webhook_env(monkeypatch)
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_URL", "https://example.test/webhook")
    with patch("finbot.notifier.requests.post") as post:
        assert maybe_post_webhook(result) is False
        post.assert_not_called()


def test_webhook_skipped_when_only_secret_set(monkeypatch: pytest.MonkeyPatch):
    result = _entry_result()
    _clear_webhook_env(monkeypatch)
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_SECRET", "crsr_test")
    with patch("finbot.notifier.requests.post") as post:
        assert maybe_post_webhook(result) is False
        post.assert_not_called()


def test_webhook_skipped_on_dry_run_even_with_env(monkeypatch: pytest.MonkeyPatch):
    result = _entry_result()
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_SECRET", "crsr_test")
    with patch("finbot.notifier.requests.post") as post:
        assert maybe_post_webhook(result, dry_run=True) is False
        post.assert_not_called()


def test_webhook_skipped_when_no_entry(monkeypatch: pytest.MonkeyPatch):
    snap = _passing_snapshot(rsi_14=70.0, rsi_14_prev=68.0)
    result = evaluate(snap, [date(2024, 7, 3)], default_ticker())
    assert not result.entry_triggered
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_SECRET", "crsr_test")
    with patch("finbot.notifier.requests.post") as post:
        assert maybe_post_webhook(result) is False
        post.assert_not_called()


def test_webhook_posts_json_with_bearer_when_env_set(monkeypatch: pytest.MonkeyPatch):
    result = _entry_result()
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_URL", "https://example.test/automations/webhook/abc")
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_SECRET", "crsr_test_key")
    response = Mock()
    response.status_code = 200
    response.text = "ok"
    with patch("finbot.notifier.requests.post", return_value=response) as post:
        assert maybe_post_webhook(result, issue_url="https://github.com/jtaroreh/finbot/issues/1") is True
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://example.test/automations/webhook/abc"
    assert kwargs["headers"]["Authorization"] == "Bearer crsr_test_key"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    body = kwargs["json"]
    assert body["ticker"] == "IBM"
    assert body["signal"] == "ENTRY"
    assert body["signal_date"] == "2024-06-03"
    assert body["entry"] == 171.0
    assert body["stop"] == 168.0
    assert body["target_2r"] == 177.0
    assert body["target_3r"] == 180.0
    assert body["gates"]["trend"]["passed"] is True
    assert body["indicators"]["rsi_14"] == 38.0
    assert body["earnings"]["next"] == "2024-07-03"
    assert body["issue_url"] == "https://github.com/jtaroreh/finbot/issues/1"


def test_webhook_uses_per_ticker_env_override(monkeypatch: pytest.MonkeyPatch):
    result = _entry_result()
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_URL", "https://example.test/global")
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_SECRET", "crsr_global")
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_URL_IBM", "https://example.test/ibm")
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_SECRET_IBM", "crsr_ibm")
    response = Mock()
    response.status_code = 200
    response.text = "ok"
    with patch("finbot.notifier.requests.post", return_value=response) as post:
        assert maybe_post_webhook(result) is True
    assert post.call_args[0][0] == "https://example.test/ibm"
    assert post.call_args[1]["headers"]["Authorization"] == "Bearer crsr_ibm"


def test_webhook_non_200_raises(monkeypatch: pytest.MonkeyPatch):
    result = _entry_result()
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_SECRET", "crsr_test")
    response = Mock()
    response.status_code = 401
    response.text = "unauthenticated"
    with patch("finbot.notifier.requests.post", return_value=response):
        with pytest.raises(NotifyError, match="401"):
            maybe_post_webhook(result)


def test_notify_dry_run_skips_issue_and_webhook(monkeypatch: pytest.MonkeyPatch):
    result = _entry_result()
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setenv("FINBOT_GROK_WEBHOOK_SECRET", "crsr_test")
    with patch("finbot.notifier.requests.post") as post, patch(
        "finbot.notifier.maybe_create_issue", return_value=None
    ) as create:
        assert notify(result, dry_run=True) is None
        create.assert_called_once_with(result, dry_run=True)
        post.assert_not_called()


def test_alert_payload_omits_issue_url_when_missing():
    result = _entry_result()
    body = alert_payload(result)
    assert "issue_url" not in body
    assert body["signal"] == "ENTRY"
    assert set(body["gates"]) >= {"earnings", "trend", "pullback", "support", "volume"}
