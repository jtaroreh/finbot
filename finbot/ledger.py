"""Which session each ticker was last graded and reported for.

The webhook goes out on every graded scan, but only ENTRY sessions leave an
Issue behind, so a NO_ENTRY session has no record in GitHub. This small JSON
file ({"QTUM": "2026-11-10", ...}) is that record: the workflow carries it
between runs with actions/cache, and a run skips any ticker whose latest
completed session is already in it. That lets the evening catch-up run fill
only the tickers an earlier run had to skip for stale Yahoo data, without
re-posting the ones it already reported.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def load(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        logger.info("No scan ledger at %s; grading every ticker", path)
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Unreadable scan ledger %s (%s); grading every ticker", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).upper(): str(v) for k, v in data.items()}


def already_handled(ledger: dict[str, str], symbol: str, session: date) -> bool:
    return ledger.get(symbol.upper(), "") >= session.isoformat()


def record(path: str | None, ledger: dict[str, str], symbol: str, session: date) -> None:
    """Mark ``symbol`` handled for ``session`` and write the file right away."""
    if not path:
        return
    key = symbol.upper()
    if ledger.get(key, "") >= session.isoformat():
        return
    ledger[key] = session.isoformat()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
