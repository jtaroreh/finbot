"""Load watchlist YAML into typed ticker configs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "watchlist.yaml"

# Long-term accumulation defaults (IBM-first; engine still loops every ticker).
_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "asset_type": "equity",
    "lookback_period": "2y",
    "earnings_blackout_days": 5,
    # Tier 1 parameters
    "tier1_enabled": True,
    "sma50_slope_lookback": 10,
    "sma50_proximity_pct": 3.0,
    "sma50_undershoot_pct": 1.5,
    "rsi_daily_dip_min": 35.0,
    "rsi_daily_dip_max": 48.0,
    # Tier 2 parameters
    "tier2_enabled": True,
    "rsi_period": 14,
    "rsi_weekly_max": 50.0,
    "rsi_daily_extreme": 30.0,
    "atr_period": 14,
    "sma_fast": 20,
    "sma_mid": 50,
    "sma_slow": 200,
    "sma200_slope_lookback": 20,
    "sma200_proximity_pct": 3.0,
    "sma200_undershoot_pct": 1.0,
    "sma200_washout_pct": 5.0,
    "sma200_washout_lookback": 60,
    "sma200_reclaim_recent_bars": 10,
    "volume_avg_period": 20,
    "volume_min_multiple": 0.0,
    "support_atr_multiple": 1.0,
    "tranche_spacing_atr": 2.25,
    "invalidation_atr_multiple": 3.5,
    "swing_left": 10,
    "swing_right": 10,
    "golden_cross_lookback": 10,
}


@dataclass(frozen=True)
class TickerConfig:
    symbol: str
    enabled: bool
    asset_type: str
    lookback_period: str
    earnings_blackout_days: int
    tier1_enabled: bool
    sma50_slope_lookback: int
    sma50_proximity_pct: float
    sma50_undershoot_pct: float
    rsi_daily_dip_min: float
    rsi_daily_dip_max: float
    tier2_enabled: bool
    rsi_period: int
    rsi_weekly_max: float
    rsi_daily_extreme: float
    atr_period: int
    sma_fast: int
    sma_mid: int
    sma_slow: int
    sma200_slope_lookback: int
    sma200_proximity_pct: float
    sma200_undershoot_pct: float
    sma200_washout_pct: float
    sma200_washout_lookback: int
    sma200_reclaim_recent_bars: int
    volume_avg_period: int
    volume_min_multiple: float
    support_atr_multiple: float
    tranche_spacing_atr: float
    invalidation_atr_multiple: float
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
    return TickerConfig(
        symbol=symbol.upper(),
        enabled=bool(merged.get("enabled", True)),
        asset_type=str(merged.get("asset_type", "equity")).lower(),
        lookback_period=str(merged["lookback_period"]),
        earnings_blackout_days=int(merged["earnings_blackout_days"]),
        tier1_enabled=bool(merged.get("tier1_enabled", True)),
        sma50_slope_lookback=int(merged.get("sma50_slope_lookback", 10)),
        sma50_proximity_pct=float(merged.get("sma50_proximity_pct", 3.0)),
        sma50_undershoot_pct=float(merged.get("sma50_undershoot_pct", 1.5)),
        rsi_daily_dip_min=float(merged.get("rsi_daily_dip_min", 35.0)),
        rsi_daily_dip_max=float(merged.get("rsi_daily_dip_max", 48.0)),
        tier2_enabled=bool(merged.get("tier2_enabled", True)),
        rsi_period=int(merged["rsi_period"]),
        rsi_weekly_max=float(merged["rsi_weekly_max"]),
        rsi_daily_extreme=float(merged["rsi_daily_extreme"]),
        atr_period=int(merged["atr_period"]),
        sma_fast=int(merged["sma_fast"]),
        sma_mid=int(merged["sma_mid"]),
        sma_slow=int(merged["sma_slow"]),
        sma200_slope_lookback=int(merged["sma200_slope_lookback"]),
        sma200_proximity_pct=float(merged["sma200_proximity_pct"]),
        sma200_undershoot_pct=float(merged["sma200_undershoot_pct"]),
        sma200_washout_pct=float(merged["sma200_washout_pct"]),
        sma200_washout_lookback=int(merged["sma200_washout_lookback"]),
        sma200_reclaim_recent_bars=int(merged["sma200_reclaim_recent_bars"]),
        volume_avg_period=int(merged["volume_avg_period"]),
        volume_min_multiple=float(merged["volume_min_multiple"]),
        support_atr_multiple=float(merged["support_atr_multiple"]),
        tranche_spacing_atr=float(merged["tranche_spacing_atr"]),
        invalidation_atr_multiple=float(merged["invalidation_atr_multiple"]),
        swing_left=int(merged["swing_left"]),
        swing_right=int(merged["swing_right"]),
        golden_cross_lookback=int(merged["golden_cross_lookback"]),
    )
