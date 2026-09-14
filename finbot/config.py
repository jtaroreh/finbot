"""Load watchlist YAML into typed ticker configs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "watchlist.yaml"

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "lookback_period": "2y",
    "earnings_blackout_days": 5,
    "rsi_period": 14,
    "rsi_pullback_min": 30.0,
    "rsi_pullback_max": 45.0,
    "rsi_freefall": 25.0,
    "atr_period": 14,
    "sma_fast": 20,
    "sma_mid": 50,
    "sma_slow": 200,
    "volume_avg_period": 20,
    "volume_min_multiple": 1.2,
    "support_atr_multiple": 0.5,
    "stop_atr_multiple": 1.5,
    "target_r_multiples": [2, 3],
    "swing_left": 5,
    "swing_right": 5,
    "golden_cross_lookback": 10,
}


@dataclass(frozen=True)
class TickerConfig:
    symbol: str
    enabled: bool
    lookback_period: str
    earnings_blackout_days: int
    rsi_period: int
    rsi_pullback_min: float
    rsi_pullback_max: float
    rsi_freefall: float
    atr_period: int
    sma_fast: int
    sma_mid: int
    sma_slow: int
    volume_avg_period: int
    volume_min_multiple: float
    support_atr_multiple: float
    stop_atr_multiple: float
    target_r_multiples: tuple[int, ...]
    swing_left: int
    swing_right: int
    golden_cross_lookback: int


@dataclass(frozen=True)
class AppConfig:
    path: Path
    defaults: dict[str, Any]
    tickers: tuple[TickerConfig, ...]

    def ticker(self, symbol: str) -> TickerConfig:
        key = symbol.upper()
        for item in self.tickers:
            if item.symbol == key:
                return item
        raise KeyError(f"Ticker {symbol!r} is not in {self.path}")


def resolve_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    cwd_path = Path.cwd() / "config" / "watchlist.yaml"
    if cwd_path.exists():
        return cwd_path.resolve()
    return DEFAULT_CONFIG_PATH


def default_ticker(symbol: str = "IBM", **overrides: Any) -> TickerConfig:
    merged = {**_DEFAULTS, **overrides}
    return _build_ticker(symbol, merged)


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = resolve_config_path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"Config root must be a mapping: {config_path}")

    defaults = {**_DEFAULTS, **(raw.get("defaults") or {})}
    tickers_raw = raw.get("tickers") or {}
    if not isinstance(tickers_raw, Mapping):
        raise ValueError("`tickers` must be a mapping of SYMBOL -> overrides")

    tickers: list[TickerConfig] = []
    for symbol, overrides in tickers_raw.items():
        merged = {**defaults}
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, Mapping):
            raise ValueError(f"Overrides for {symbol} must be a mapping")
        merged.update(dict(overrides))
        tickers.append(_build_ticker(str(symbol), merged))

    if not tickers:
        raise ValueError("Config must define at least one ticker")

    return AppConfig(path=config_path, defaults=defaults, tickers=tuple(tickers))


def _build_ticker(symbol: str, merged: Mapping[str, Any]) -> TickerConfig:
    multiples = merged.get("target_r_multiples") or (2, 3)
    return TickerConfig(
        symbol=symbol.upper(),
        enabled=bool(merged.get("enabled", True)),
        lookback_period=str(merged["lookback_period"]),
        earnings_blackout_days=int(merged["earnings_blackout_days"]),
        rsi_period=int(merged["rsi_period"]),
        rsi_pullback_min=float(merged["rsi_pullback_min"]),
        rsi_pullback_max=float(merged["rsi_pullback_max"]),
        rsi_freefall=float(merged["rsi_freefall"]),
        atr_period=int(merged["atr_period"]),
        sma_fast=int(merged["sma_fast"]),
        sma_mid=int(merged["sma_mid"]),
        sma_slow=int(merged["sma_slow"]),
        volume_avg_period=int(merged["volume_avg_period"]),
        volume_min_multiple=float(merged["volume_min_multiple"]),
        support_atr_multiple=float(merged["support_atr_multiple"]),
        stop_atr_multiple=float(merged["stop_atr_multiple"]),
        target_r_multiples=tuple(int(x) for x in multiples),
        swing_left=int(merged["swing_left"]),
        swing_right=int(merged["swing_right"]),
        golden_cross_lookback=int(merged["golden_cross_lookback"]),
    )
