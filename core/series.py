"""Data carriers that keep their provenance.

The point of this module: a number must never become anonymous. When a series
is transformed, the lineage travels with it, so a chart caption, a briefing
footnote, and the verifier all read the same origin record.

Two carriers:
    Table   a tabular fetch result (e.g. OHLCV rows)
    Series  a single indexed value column, which is what transforms operate on
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config


@dataclass
class Provenance:
    source: str                     # "dart", "krx"
    dataset: str                    # "stock_price", "disclosures"
    label: str                      # human-readable, used in chart captions
    params: dict = field(default_factory=dict)
    cache_keys: list[str] = field(default_factory=list)
    fetched_at: str | None = None
    unit: str | None = None
    frequency: str | None = None    # "daily", "monthly", "quarterly"
    citation: str | None = None     # official source name for the 출처 section
    lineage: list[str] = field(default_factory=list)

    def derived(self, step: str) -> "Provenance":
        """Copy with one more transform recorded."""
        return Provenance(
            source=self.source,
            dataset=self.dataset,
            label=self.label,
            params=dict(self.params),
            cache_keys=list(self.cache_keys),
            fetched_at=self.fetched_at,
            unit=self.unit,
            frequency=self.frequency,
            citation=self.citation,
            lineage=[*self.lineage, step],
        )

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "dataset": self.dataset,
            "label": self.label,
            "params": self.params,
            "cache_keys": self.cache_keys,
            "fetched_at": self.fetched_at,
            "unit": self.unit,
            "frequency": self.frequency,
            "citation": self.citation,
            "lineage": self.lineage,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Provenance":
        return cls(**{k: d.get(k) for k in (
            "source", "dataset", "label", "params", "cache_keys",
            "fetched_at", "unit", "frequency", "citation", "lineage",
        ) if d.get(k) is not None})


@dataclass
class Series:
    id: str
    values: pd.Series
    prov: Provenance

    def __post_init__(self):
        if not isinstance(self.values.index, pd.DatetimeIndex):
            self.values.index = pd.to_datetime(self.values.index)
        self.values = self.values.sort_index()
        self.values.name = self.id
        self._reject_duplicate_dates()

    def _reject_duplicate_dates(self):
        """A date must map to one value, or the series is silently two series.

        This guard exists because an under-specified row filter produced a
        10-year government bond series that interleaved the nominal and the
        inflation-linked issue. Both were plausible numbers, and the resulting
        spread looked reasonable while being meaningless.
        """
        duplicated = self.values.index.duplicated()
        if not duplicated.any():
            return
        dates = self.values.index[duplicated].unique()[:3]
        samples = []
        for date in dates:
            values = self.values.loc[date].tolist()
            samples.append(f"{pd.Timestamp(date).date()}: {values}")
        raise ValueError(
            f"'{self.id}': 같은 날짜에 값이 여러 개입니다 "
            f"({duplicated.sum()}건 중복). 조건이 한 행으로 좁혀지지 않았습니다.\n  "
            + "\n  ".join(samples)
        )

    def __len__(self) -> int:
        return len(self.values)

    def with_values(self, values: pd.Series, step: str, *, unit: str | None = None,
                    suffix: str | None = None) -> "Series":
        prov = self.prov.derived(step)
        if unit is not None:
            prov.unit = unit
        return Series(
            id=f"{self.id}_{suffix}" if suffix else self.id,
            values=values,
            prov=prov,
        )

    @property
    def latest(self) -> tuple[pd.Timestamp, float] | None:
        clean = self.values.dropna()
        if clean.empty:
            return None
        return clean.index[-1], float(clean.iloc[-1])

    def asof(self, when) -> float | None:
        clean = self.values.dropna()
        clean = clean[clean.index <= pd.to_datetime(when)]
        return float(clean.iloc[-1]) if not clean.empty else None

    def describe(self) -> str:
        n = len(self.values.dropna())
        span = ""
        if n:
            clean = self.values.dropna()
            span = f"  {clean.index[0].date()} ~ {clean.index[-1].date()}"
        unit = f" [{self.prov.unit}]" if self.prov.unit else ""
        chain = (" <- " + " <- ".join(reversed(self.prov.lineage))) if self.prov.lineage else ""
        return f"{self.id}{unit}  n={n}{span}{chain}"

    # --- persistence -------------------------------------------------------

    def path(self) -> Path:
        return config.SERIES_DIR / self.prov.source / f"{self.id}.csv"

    def save(self) -> Path:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        frame = self.values.rename("value").to_frame()
        frame.index.name = "date"
        frame.to_csv(p, date_format="%Y-%m-%d")
        p.with_suffix(".meta.json").write_text(
            json.dumps(self.prov.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p

    @classmethod
    def load(cls, source: str, series_id: str) -> "Series":
        p = config.SERIES_DIR / source / f"{series_id}.csv"
        frame = pd.read_csv(p, parse_dates=["date"], index_col="date")
        meta_path = p.with_suffix(".meta.json")
        prov = (
            Provenance.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
            if meta_path.exists()
            else Provenance(source=source, dataset="unknown", label=series_id)
        )
        return cls(id=series_id, values=frame["value"], prov=prov)


@dataclass
class Table:
    id: str
    frame: pd.DataFrame
    prov: Provenance

    def __len__(self) -> int:
        return len(self.frame)

    def column(self, name: str, *, index: str = "date", series_id: str | None = None,
               unit: str | None = None) -> Series:
        """Extract one column as a provenance-carrying Series."""
        if name not in self.frame.columns:
            raise KeyError(f"{self.id}: '{name}' 컬럼이 없습니다. 사용 가능: {list(self.frame.columns)}")
        indexed = self.frame.set_index(index)[name]
        prov = self.prov.derived(f"column({name})")
        if unit is not None:
            prov.unit = unit
        return Series(id=series_id or f"{self.id}_{name}", values=indexed, prov=prov)

    def save(self) -> Path:
        p = config.SERIES_DIR / self.prov.source / f"{self.id}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        self.frame.to_csv(p, index=False)
        p.with_suffix(".meta.json").write_text(
            json.dumps(self.prov.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p
