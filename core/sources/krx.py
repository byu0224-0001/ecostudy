"""KRX 정보데이터시스템 Open API (data-dbg.krx.co.kr).

Not to be confused with 공공데이터포털's 금융위원회_주식시세정보 on
apis.data.go.kr. They are separate services with separate keys: this one
authenticates with an AUTH_KEY header and a 40-character hex key.

The API returns one full market day per call. There is no date range and the
isuCd parameter is ignored, so a time series is assembled by fetching each
trading day and filtering. That sounds wasteful but works in our favour: the
cache unit is a whole market day, so studying twenty companies over a period
costs exactly as much as studying one.

Non-trading days return zero rows rather than an error, which is why no
holiday calendar is hardcoded here. Absence of data is the answer.
"""

from __future__ import annotations

import re
import sys

import pandas as pd

from .. import config
from ..http import FetchError
from ..series import Provenance, Series, Table
from .base import DAY, Source, ymd

BASE = "https://data-dbg.krx.co.kr/svc/apis"

# dataset -> (path, 한글 이름). Availability depends on which services are
# approved for the key; an unapproved one returns HTTP 401.
ENDPOINTS: dict[str, tuple[str, str]] = {
    "stock_kospi":   ("/sto/stk_bydd_trd",       "유가증권 일별매매정보"),
    "stock_kosdaq":  ("/sto/ksq_bydd_trd",       "코스닥 일별매매정보"),
    "info_kospi":    ("/sto/stk_isu_base_info",  "유가증권 종목기본정보"),
    "info_kosdaq":   ("/sto/ksq_isu_base_info",  "코스닥 종목기본정보"),
    "index_kospi":   ("/idx/kospi_dd_trd",       "KOSPI 시리즈 일별시세"),
    "index_kosdaq":  ("/idx/kosdaq_dd_trd",      "KOSDAQ 시리즈 일별시세"),
    "index_krx":     ("/idx/krx_dd_trd",         "KRX 시리즈 일별시세"),
    "index_bond":    ("/idx/bon_dd_trd",         "채권지수 시세정보"),
    "etf":           ("/etp/etf_bydd_trd",       "ETF 일별매매정보"),
    "bond_kts":      ("/bon/kts_bydd_trd",       "국채전문유통시장 일별매매정보"),
    "bond_general":  ("/bon/bnd_bydd_trd",       "일반채권시장 일별매매정보"),
    "bond_small":    ("/bon/smb_bydd_trd",       "소액채권시장 일별매매정보"),
    "gold":          ("/gen/gold_bydd_trd",      "금시장 일별매매정보"),
    "oil":           ("/gen/oil_bydd_trd",       "석유시장 일별매매정보"),
    "option_equity": ("/drv/eqsop_bydd_trd",     "주식옵션(유가) 일별매매정보"),
}

# Identifier and name columns must stay strings: "005930" is a ticker, not
# the number 5930. Everything else is numeric.
TEXT_SUFFIXES = ("_NM", "_CD", "_DD", "_CLSS")
TEXT_COLUMNS = {"ISU_ABBRV"}

# A finished trading day never changes, so it can be cached indefinitely.
# A recent one may still be filling in, so it is re-checked.
SETTLED_AFTER_DAYS = 7
SETTLED_TTL = 365 * DAY
RECENT_TTL = 6 * 3600

PROGRESS_THRESHOLD = 20


def _render(value) -> str:
    return value.pattern if isinstance(value, re.Pattern) else str(value)


def _match_suffix(match: dict | None) -> str:
    """A filesystem-safe fragment identifying a row filter."""
    if not match:
        return "all"
    parts = [re.sub(r"[^0-9A-Za-z가-힣]+", "", _render(v)) for v in match.values()]
    return "_".join(p for p in parts if p) or "all"


class Krx(Source):
    name = "krx"
    citation = "KRX 정보데이터시스템 Open API"
    min_interval = 0.15

    def __init__(self):
        super().__init__()
        self._market_of: dict[str, str] = {}

    def _auth_headers(self) -> dict:
        return {"AUTH_KEY": config.require("KRX_OPENAPI_KEY", "KRX Open API")}

    @staticmethod
    def _ttl(day: str) -> float:
        age_days = (pd.Timestamp.today().normalize() - pd.to_datetime(day)).days
        return SETTLED_TTL if age_days > SETTLED_AFTER_DAYS else RECENT_TTL

    @staticmethod
    def _numeric(frame: pd.DataFrame) -> pd.DataFrame:
        for col in frame.columns:
            if col in TEXT_COLUMNS or col.endswith(TEXT_SUFFIXES):
                continue
            frame[col] = pd.to_numeric(
                frame[col].astype(str).str.replace(",", "", regex=False).replace("", None),
                errors="coerce",
            )
        return frame

    def _explain(self, exc: FetchError, dataset: str) -> FetchError:
        if exc.status == 401:
            label = ENDPOINTS[dataset][1]
            return FetchError(
                f"krx: '{label}' 서비스가 이 키에 승인되어 있지 않습니다.\n"
                "  KRX 정보데이터시스템(data.krx.co.kr) > 오픈API > 이용현황에서 "
                "해당 서비스를 신청하세요.",
                status=401,
            )
        return exc

    # --- single day --------------------------------------------------------

    def day(self, dataset: str, date, *, refresh: bool = False) -> pd.DataFrame:
        """One trading day of one dataset. Empty frame on a non-trading day."""
        if dataset not in ENDPOINTS:
            raise ValueError(
                f"'{dataset}' 데이터셋은 없습니다. 사용 가능: {sorted(ENDPOINTS)}"
            )
        path, _ = ENDPOINTS[dataset]
        basDd = ymd(date)

        try:
            fetched = self.get_json(
                dataset,
                BASE + path,
                params={"basDd": basDd},
                auth_headers=self._auth_headers(),
                max_age=self._ttl(basDd),
                refresh=refresh,
            )
        except FetchError as exc:
            raise self._explain(exc, dataset) from exc

        block = next(
            (v for v in (fetched.data or {}).values() if isinstance(v, list)), []
        )
        if not block:
            return pd.DataFrame()

        frame = self._numeric(pd.DataFrame(block))
        frame.attrs["cache_key"] = fetched.cache_key
        frame.attrs["fetched_at"] = fetched.fetched_at
        return frame

    def snapshot(self, dataset: str, date, *, refresh: bool = False) -> Table:
        """One trading day as a Table with provenance."""
        basDd = ymd(date)
        frame = self.day(dataset, basDd, refresh=refresh)
        if frame.empty:
            raise FetchError(
                f"krx/{dataset}: {basDd} 에 데이터가 없습니다. "
                "휴장일이거나 아직 집계 전일 수 있습니다."
            )
        return Table(
            id=f"krx_{dataset}_{basDd}",
            frame=frame,
            prov=Provenance(
                source=self.name,
                dataset=dataset,
                label=f"{ENDPOINTS[dataset][1]} {basDd}",
                params={"basDd": basDd},
                cache_keys=[frame.attrs.get("cache_key")],
                fetched_at=frame.attrs.get("fetched_at"),
                frequency="daily",
                citation=f"{self.citation} — {ENDPOINTS[dataset][1]}",
            ),
        )

    # --- across days -------------------------------------------------------

    def history(
        self,
        dataset: str,
        *,
        start,
        end,
        match: dict | None = None,
        refresh: bool = False,
        progress: bool = True,
    ) -> Table:
        """Concatenate trading days in a range, optionally filtered.

        `match` selects rows by column value. A plain string matches exactly;
        a compiled regex matches by pattern, which is needed when a category
        is not its own column. The 10-year benchmark, for instance, contains
        both the nominal and the inflation-linked issue, separable only by
        name: {"BND_EXP_TP_NM": "10", "ISU_NM": re.compile(r"^국고")}.

        Filtering happens after the fetch because the API always returns the
        whole market.
        """
        days = pd.bdate_range(pd.to_datetime(start), pd.to_datetime(end))
        if len(days) == 0:
            raise ValueError("start 와 end 사이에 영업일이 없습니다")

        show = progress and len(days) >= PROGRESS_THRESHOLD
        collected, keys, fetched_at, skipped = [], [], None, 0

        for i, day in enumerate(days, 1):
            basDd = day.strftime("%Y%m%d")
            frame = self.day(dataset, basDd, refresh=refresh)
            if frame.empty:
                skipped += 1
            else:
                if match:
                    for col, value in match.items():
                        if col not in frame.columns:
                            raise KeyError(
                                f"krx/{dataset}: '{col}' 컬럼이 없습니다. "
                                f"사용 가능: {list(frame.columns)}"
                            )
                        column = frame[col].astype(str).str.strip()
                        if isinstance(value, re.Pattern):
                            frame = frame[column.str.contains(value)]
                        else:
                            frame = frame[column == str(value)]
                if not frame.empty:
                    collected.append(frame)
                    keys.append(frame.attrs.get("cache_key"))
                    fetched_at = frame.attrs.get("fetched_at")
            if show and (i % 20 == 0 or i == len(days)):
                print(f"  krx/{dataset} {i}/{len(days)}일 ({basDd})",
                      end="\r", file=sys.stderr, flush=True)

        if show:
            print(" " * 60, end="\r", file=sys.stderr)

        if not collected:
            hint = f" (조건: {match})" if match else ""
            raise FetchError(
                f"krx/{dataset}: {ymd(start)}~{ymd(end)} 에 해당하는 데이터가 없습니다{hint}. "
                f"영업일 {len(days)}일 중 {skipped}일은 휴장이었습니다."
            )

        frame = pd.concat(collected, ignore_index=True)
        frame["date"] = pd.to_datetime(frame["BAS_DD"], format="%Y%m%d", errors="coerce")
        frame = frame.sort_values("date").reset_index(drop=True)

        suffix = _match_suffix(match)
        return Table(
            id=f"krx_{dataset}_{suffix}_{ymd(start)}_{ymd(end)}",
            frame=frame,
            prov=Provenance(
                source=self.name,
                dataset=dataset,
                label=f"{ENDPOINTS[dataset][1]} {match or '전체'}",
                params={"start": ymd(start), "end": ymd(end),
                        "match": {k: _render(v) for k, v in (match or {}).items()}},
                cache_keys=keys,
                fetched_at=fetched_at,
                frequency="daily",
                citation=f"{self.citation} — {ENDPOINTS[dataset][1]}",
            ),
        )

    def series(
        self,
        dataset: str,
        field: str,
        *,
        start,
        end,
        match: dict,
        series_id: str | None = None,
        unit: str | None = None,
        **kw,
    ) -> Series:
        """One numeric field of one matched row, as a time series.

        This is the generic accessor. It knows how to extract any field for any
        row; it does not know which field is worth looking at.
        """
        table = self.history(dataset, start=start, end=end, match=match, **kw)
        suffix = _match_suffix(match)
        return table.column(
            field,
            series_id=series_id or f"krx_{dataset}_{suffix}_{field.lower()}",
            unit=unit,
        )

    # --- ticker helpers ----------------------------------------------------

    def _recent_trading_day(self, *, lookback: int = 10) -> str:
        cursor = pd.Timestamp.today().normalize()
        for _ in range(lookback):
            if cursor.weekday() < 5:
                basDd = cursor.strftime("%Y%m%d")
                if not self.day("stock_kospi", basDd).empty:
                    return basDd
            cursor -= pd.Timedelta(days=1)
        raise FetchError("krx: 최근 영업일을 찾지 못했습니다")

    def market_of(self, ticker: str) -> str:
        """Return 'kospi' or 'kosdaq' for a ticker, resolved once and reused."""
        code = str(ticker).strip().upper().split(".")[0]
        if code in self._market_of:
            return self._market_of[code]

        basDd = self._recent_trading_day()
        for market in ("kospi", "kosdaq"):
            frame = self.day(f"stock_{market}", basDd)
            if not frame.empty and (frame["ISU_CD"].astype(str).str.strip() == code).any():
                self._market_of[code] = market
                return market
        raise FetchError(
            f"krx: {code} 를 {basDd} 기준 유가증권·코스닥 어디에서도 찾지 못했습니다. "
            "상장폐지되었거나 코드가 잘못되었을 수 있습니다."
        )

    def price_history(self, ticker: str, *, start, end, **kw) -> Table:
        code = str(ticker).strip().upper().split(".")[0]
        dataset = f"stock_{self.market_of(code)}"
        return self.history(dataset, start=start, end=end,
                            match={"ISU_CD": code}, **kw)

    def close(self, ticker: str, *, start, end, **kw) -> Series:
        code = str(ticker).strip().upper().split(".")[0]
        table = self.price_history(code, start=start, end=end, **kw)
        name = str(table.frame["ISU_NM"].iloc[-1]).strip()
        s = table.column("TDD_CLSPRC", series_id=f"krx_close_{code}", unit="원")
        s.prov.label = f"{name}({code}) 종가"
        return s
