"""Transform primitives.

These are safe to hardcode because they are arithmetic: they encode HOW to
compute a change rate, never WHICH series is worth computing it on. That
choice belongs in a session spec, because it changes with the regime.

Year-over-year and month-over-month use calendar offsets rather than a fixed
number of rows, so the same function is correct for daily and monthly data.
"""

from __future__ import annotations

import pandas as pd

from .series import Provenance, Series


def _is_month_aligned(index: pd.DatetimeIndex) -> bool:
    """True when every timestamp sits on a month end.

    Monthly and quarterly macro series are published this way, and calendar
    subtraction misbehaves on them: 2024-04-30 minus one month is 2024-03-30,
    which is not the previous observation date.
    """
    return len(index) > 0 and bool(index.is_month_end.all())


def _shifted(values: pd.Series, *, years: int = 0, months: int = 0, days: int = 0) -> pd.Series:
    """Value as of one offset earlier, aligned to the series' own calendar.

    Month-end series are compared in monthly period space, which is exact and
    leaves a genuine gap as NaN. Everything else falls back to calendar
    subtraction with carry-forward, never carry-backward, so a comparison can
    only use information that already existed.
    """
    index = values.index

    if days == 0 and (years or months) and _is_month_aligned(index):
        periods = years * 12 + months
        month_index = index.to_period("M")
        if not month_index.has_duplicates:
            source = pd.Series(values.to_numpy(), index=month_index)
            prev = source.reindex(month_index - periods)
            prev.index = index
            return prev

    offset = pd.DateOffset(years=years, months=months, days=days)
    prev = values.reindex(index - offset, method="ffill")
    prev.index = index
    return prev


def level(s: Series) -> Series:
    return s.with_values(s.values, "level")


def diff(s: Series, *, years: int = 0, months: int = 0, days: int = 0) -> Series:
    out = s.values - _shifted(s.values, years=years, months=months, days=days)
    label = f"diff({years}y{months}m{days}d)"
    unit = f"{s.prov.unit} 변화" if s.prov.unit else None
    return s.with_values(out, label, unit=unit, suffix="chg")


def pct(s: Series, *, years: int = 0, months: int = 0, days: int = 0) -> Series:
    out = (s.values / _shifted(s.values, years=years, months=months, days=days) - 1) * 100
    return s.with_values(out, f"pct({years}y{months}m{days}d)", unit="%", suffix="pct")


def yoy(s: Series) -> Series:
    out = (s.values / _shifted(s.values, years=1) - 1) * 100
    return s.with_values(out, "yoy", unit="%", suffix="yoy")


def mom(s: Series) -> Series:
    out = (s.values / _shifted(s.values, months=1) - 1) * 100
    return s.with_values(out, "mom", unit="%", suffix="mom")


def annualized(s: Series, *, months: int = 3) -> Series:
    """Compound the change over `months` up to an annual rate.

    This is the "3-month annualized" framing used for inflation momentum.
    """
    prev = _shifted(s.values, months=months)
    growth = s.values / prev
    out = (growth.pow(12.0 / months) - 1) * 100
    return s.with_values(out, f"annualized({months}m)", unit="%", suffix=f"{months}ma")


def rolling_mean(s: Series, *, window: int) -> Series:
    out = s.values.rolling(window, min_periods=max(1, window // 2)).mean()
    return s.with_values(out, f"rolling_mean({window})", suffix=f"ma{window}")


def zscore(s: Series, *, window: int | None = None) -> Series:
    if window:
        mean = s.values.rolling(window, min_periods=max(2, window // 2)).mean()
        std = s.values.rolling(window, min_periods=max(2, window // 2)).std()
        step = f"zscore(window={window})"
    else:
        mean, std = s.values.expanding(min_periods=2).mean(), s.values.expanding(min_periods=2).std()
        step = "zscore(expanding)"
    out = (s.values - mean) / std
    return s.with_values(out, step, unit="σ", suffix="z")


def percentile_rank(s: Series, *, window: int | None = None) -> Series:
    """Where does the current value sit in its own history, 0-100.

    Useful for "is this level unusual?" without asserting what unusual means.
    """
    if window:
        out = s.values.rolling(window, min_periods=max(2, window // 2)).apply(
            lambda w: pd.Series(w).rank(pct=True).iloc[-1] * 100, raw=False
        )
        step = f"percentile_rank(window={window})"
    else:
        out = s.values.expanding(min_periods=2).apply(
            lambda w: pd.Series(w).rank(pct=True).iloc[-1] * 100, raw=False
        )
        step = "percentile_rank(expanding)"
    return s.with_values(out, step, unit="pctile", suffix="pctile")


def rebase(s: Series, *, to: float = 100.0, at=None) -> Series:
    clean = s.values.dropna()
    if clean.empty:
        return s.with_values(s.values, "rebase(empty)")
    base = clean.iloc[0] if at is None else s.asof(at)
    if not base:
        raise ValueError(f"{s.id}: rebase 기준값을 찾을 수 없습니다 (at={at})")
    out = s.values / base * to
    return s.with_values(out, f"rebase(to={to}, at={at or 'first'})",
                         unit=f"index({to})", suffix="idx")


def resample(s: Series, *, freq: str = "ME", how: str = "last") -> Series:
    """Downsample, e.g. daily to month-end. how: last | mean | sum | max | min."""
    grouped = s.values.resample(freq)
    if not hasattr(grouped, how):
        raise ValueError(f"resample how='{how}' 를 지원하지 않습니다")
    out = getattr(grouped, how)()
    prov_freq = {"ME": "monthly", "QE": "quarterly", "YE": "annual", "W": "weekly"}.get(freq)
    result = s.with_values(out, f"resample({freq}, {how})")
    if prov_freq:
        result.prov.frequency = prov_freq
    return result


# --- two-series operations -------------------------------------------------

def _combined_prov(a: Series, b: Series, step: str) -> Provenance:
    sources = sorted({a.prov.source, b.prov.source})
    citations = [c for c in {a.prov.citation, b.prov.citation} if c]
    return Provenance(
        source="+".join(sources),
        dataset=f"{a.prov.dataset}+{b.prov.dataset}",
        label=f"{a.prov.label} / {b.prov.label}",
        cache_keys=[*a.prov.cache_keys, *b.prov.cache_keys],
        fetched_at=max(filter(None, [a.prov.fetched_at, b.prov.fetched_at]), default=None),
        citation="; ".join(citations) or None,
        frequency=a.prov.frequency,
        lineage=[f"{step}({a.id}, {b.id})"],
    )


def _align(a: Series, b: Series) -> tuple[pd.Series, pd.Series]:
    index = a.values.index.union(b.values.index)
    return (
        a.values.reindex(index).ffill(),
        b.values.reindex(index).ffill(),
    )


def spread(a: Series, b: Series, *, series_id: str | None = None) -> Series:
    """a - b, aligned on the union of both indexes with carry-forward."""
    left, right = _align(a, b)
    prov = _combined_prov(a, b, "spread")
    prov.unit = a.prov.unit
    return Series(id=series_id or f"{a.id}_minus_{b.id}", values=left - right, prov=prov)


def ratio(a: Series, b: Series, *, series_id: str | None = None) -> Series:
    left, right = _align(a, b)
    prov = _combined_prov(a, b, "ratio")
    prov.unit = "ratio"
    return Series(id=series_id or f"{a.id}_over_{b.id}", values=left / right, prov=prov)


REGISTRY = {
    "level": level,
    "yoy": yoy,
    "mom": mom,
    "annualized": annualized,
    "rolling_mean": rolling_mean,
    "zscore": zscore,
    "percentile_rank": percentile_rank,
    "rebase": rebase,
    "resample": resample,
    "diff": diff,
    "pct": pct,
}


def apply_named(s: Series, name: str, **kwargs) -> Series:
    """Apply a transform by name, so a session spec can declare it as text.

    This is the bridge from Layer 2 (declarative session yaml) into Layer 1.
    """
    fn = REGISTRY.get(name)
    if fn is None:
        raise ValueError(f"'{name}' 변환은 없습니다. 사용 가능: {sorted(REGISTRY)}")
    return fn(s, **kwargs)
