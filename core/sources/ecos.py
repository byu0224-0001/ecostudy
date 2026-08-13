"""한국은행 경제통계시스템(ECOS) 어댑터.

KRX and DART cover markets and filings; neither has 기준금리, 물가, 통화량,
환율, 국제수지. ECOS has all of it behind one interface where the only thing
that changes between indicators is a statistic code.

That shape suits this layer exactly: the adapter learns how to fetch and how
to search the catalogue, and never learns which statistic matters. Discovery
lives here (`tables`, `items`, `key_statistics`) so a session can name a code
it found rather than a code someone hardcoded.

Note the credential goes in the URL path, not a query string. Recorded URLs
are scrubbed in core.config before anything reaches the cache.
"""

from __future__ import annotations

import pandas as pd

from .. import config
from ..http import FetchError
from ..series import Provenance, Series, Table
from .base import DAY, Source

BASE = "https://ecos.bok.or.kr/api"
MAX_ROWS = 100000

CYCLES = {
    "A": "연",
    "S": "반기",
    "Q": "분기",
    "M": "월",
    "SM": "반월",
    "D": "일",
}

RESULT_MESSAGES = {
    "INFO-100": "인증키가 유효하지 않습니다. ECOS_API_KEY 를 확인하세요",
    "INFO-200": "해당하는 데이터가 없습니다. 통계코드·주기·기간·항목코드를 확인하세요",
    "ERROR-100": "필수 값이 누락되었습니다",
    "ERROR-101": "주기 형식이 올바르지 않습니다",
    "ERROR-300": "조회 건수가 100,000건을 초과했습니다. 기간을 좁히세요",
    "ERROR-500": "ECOS 서버 오류입니다",
    "ERROR-600": "ECOS DB 연결 오류입니다",
    "ERROR-602": "요청이 과도합니다. 잠시 후 다시 시도하세요",
}
NO_DATA = "INFO-200"


def _period_end(value: str, cycle: str) -> pd.Timestamp | None:
    """ECOS TIME strings to the last day of the period they describe.

    Aligning to period end rather than start matters downstream: the transform
    layer compares month-end series in period space, and a month-start index
    would silently fall back to calendar arithmetic.
    """
    text = str(value).strip().upper()
    digits = "".join(ch for ch in text if ch.isdigit())
    try:
        if cycle == "D" and len(digits) == 8:
            return pd.to_datetime(digits, format="%Y%m%d")
        if cycle in ("M", "SM") and len(digits) >= 6:
            return pd.Period(f"{digits[:4]}-{digits[4:6]}", freq="M").end_time.normalize()
        if cycle == "Q" and len(digits) >= 5:
            return pd.Period(f"{digits[:4]}Q{digits[4]}", freq="Q").end_time.normalize()
        if cycle == "S" and len(digits) >= 5:
            month = "06" if digits[4] == "1" else "12"
            return pd.Period(f"{digits[:4]}-{month}", freq="M").end_time.normalize()
        if cycle == "A" and len(digits) >= 4:
            return pd.Period(digits[:4], freq="Y").end_time.normalize()
    except (ValueError, KeyError):
        return None
    return None


class Ecos(Source):
    name = "ecos"
    citation = "한국은행 경제통계시스템(ECOS)"
    min_interval = 0.3

    def _key(self) -> str:
        return config.require("ECOS_API_KEY", "한국은행 ECOS")

    def _call(self, service: str, segments: list[str], *, cache_params: dict,
              max_age: float, refresh: bool) -> list[dict]:
        """ECOS puts everything in the path: /service/key/json/kr/start/end/..."""
        url = "/".join([BASE, service, self._key(), "json", "kr", *segments])
        fetched = self.get_json(
            service, url, params=cache_params, query={},
            max_age=max_age, refresh=refresh,
        )
        data = fetched.data or {}

        if "RESULT" in data:
            code = str(data["RESULT"].get("CODE", ""))
            if code == NO_DATA:
                return []
            hint = RESULT_MESSAGES.get(code, data["RESULT"].get("MESSAGE", ""))
            raise FetchError(f"ecos/{service}: {code} — {hint}")

        block = data.get(service) or {}
        rows = block.get("row") or []
        for row in rows:
            row["_cache_key"] = fetched.cache_key
            row["_fetched_at"] = fetched.fetched_at
        return rows

    # --- discovery ---------------------------------------------------------

    def tables(self, *, search: str | None = None, refresh: bool = False) -> pd.DataFrame:
        """통계표 목록. Use this to find a statistic code instead of guessing."""
        rows = self._call(
            "StatisticTableList", ["1", "10000", ""],
            cache_params={"service": "StatisticTableList"},
            max_age=30 * DAY, refresh=refresh,
        )
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        keep = [c for c in ("STAT_CODE", "STAT_NAME", "CYCLE", "SRCH_YN", "ORG_NAME")
                if c in frame.columns]
        frame = frame[keep]
        if search:
            frame = frame[frame["STAT_NAME"].str.contains(search, na=False)]
        return frame.reset_index(drop=True)

    def items(self, stat_code: str, *, refresh: bool = False) -> pd.DataFrame:
        """통계 세부항목. A statistic code alone is rarely enough; most tables
        need an item code to pin down a single series."""
        rows = self._call(
            "StatisticItemList", ["1", "10000", stat_code],
            cache_params={"service": "StatisticItemList", "stat_code": stat_code},
            max_age=30 * DAY, refresh=refresh,
        )
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        keep = [c for c in ("STAT_CODE", "STAT_NAME", "GRP_CODE", "GRP_NAME",
                            "ITEM_CODE", "ITEM_NAME", "CYCLE", "UNIT_NAME",
                            "START_TIME", "END_TIME", "DATA_CNT")
                if c in frame.columns]
        return frame[keep].reset_index(drop=True)

    def key_statistics(self, *, refresh: bool = False) -> pd.DataFrame:
        """100대 통계지표. A quick way to see what the Bank publishes."""
        rows = self._call(
            "KeyStatisticList", ["1", "200"],
            cache_params={"service": "KeyStatisticList"},
            max_age=DAY, refresh=refresh,
        )
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        keep = [c for c in ("CLASS_NAME", "KEYSTAT_NAME", "DATA_VALUE",
                            "CYCLE", "UNIT_NAME") if c in frame.columns]
        return frame[keep].reset_index(drop=True)

    # --- data --------------------------------------------------------------

    def statistic(
        self,
        stat_code: str,
        *,
        start,
        end,
        cycle: str = "M",
        items: list[str] | None = None,
        max_age: float = DAY,
        refresh: bool = False,
    ) -> Table:
        """One statistic over a period.

        `start` and `end` follow the cycle: 2025 for A, 2025Q1 for Q,
        202501 for M, 20250131 for D.
        """
        cycle = cycle.upper()
        if cycle not in CYCLES:
            raise ValueError(f"cycle 은 {sorted(CYCLES)} 중 하나여야 합니다")

        item_codes = list(items or [])
        while len(item_codes) < 4:
            item_codes.append("")

        segments = [
            "1", str(MAX_ROWS), stat_code, cycle,
            str(start).replace("-", ""), str(end).replace("-", ""),
            *item_codes,
        ]
        cache_params = {
            "service": "StatisticSearch", "stat_code": stat_code, "cycle": cycle,
            "start": str(start), "end": str(end), "items": items or [],
        }
        rows = self._call("StatisticSearch", segments,
                          cache_params=cache_params, max_age=max_age, refresh=refresh)

        if not rows:
            raise FetchError(
                f"ecos: {stat_code} ({cycle}, {start}~{end}) 데이터가 없습니다. "
                f"items() 로 항목코드와 제공 기간을 확인하세요."
            )

        frame = pd.DataFrame(rows)
        frame["date"] = frame["TIME"].map(lambda t: _period_end(t, cycle))
        frame["value"] = pd.to_numeric(
            frame["DATA_VALUE"].astype(str).str.replace(",", "", regex=False),
            errors="coerce",
        )
        frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

        name = str(frame.get("STAT_NAME", pd.Series([stat_code])).iloc[0]).strip()
        unit = str(frame.get("UNIT_NAME", pd.Series([""])).iloc[0]).strip() or None
        item_label = " / ".join(
            str(frame[c].iloc[0]).strip()
            for c in ("ITEM_NAME1", "ITEM_NAME2", "ITEM_NAME3")
            if c in frame.columns and str(frame[c].iloc[0]).strip()
        )

        return Table(
            id=f"ecos_{stat_code}_{'_'.join(items or [])}".rstrip("_"),
            frame=frame,
            prov=Provenance(
                source=self.name,
                dataset=stat_code,
                label=f"{name}{' — ' + item_label if item_label else ''}",
                params=cache_params,
                cache_keys=[rows[0].get("_cache_key")],
                fetched_at=rows[0].get("_fetched_at"),
                unit=unit,
                frequency=CYCLES[cycle],
                citation=f"{self.citation} — {name}",
            ),
        )

    def series(
        self,
        stat_code: str,
        *,
        start,
        end,
        cycle: str = "M",
        items: list[str] | None = None,
        series_id: str | None = None,
        **kw,
    ) -> Series:
        table = self.statistic(stat_code, start=start, end=end,
                               cycle=cycle, items=items, **kw)
        suffix = "_".join(items or []) or "all"
        s = table.column(
            "value",
            series_id=series_id or f"ecos_{stat_code}_{suffix}",
            unit=table.prov.unit,
        )
        s.prov.label = table.prov.label
        s.prov.frequency = table.prov.frequency
        return s
