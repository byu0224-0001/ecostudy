"""DART (전자공시) adapter.

One real improvement over the dividend project: corp_code is resolved from
DART's own `corpCode.xml` bulk file rather than a hand-maintained
`krx_directory.json` loaded from the process cwd. That file silently returned
None depending on where you ran the script from, and it went stale for newly
listed companies.
"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

import pandas as pd

from .. import config
from ..http import FetchError
from ..series import Provenance, Table
from .base import DAY, Source, ymd

BASE = "https://opendart.fss.or.kr/api"
CORP_CODE_URL = f"{BASE}/corpCode.xml"
LIST_URL = f"{BASE}/list.json"
FINANCIALS_URL = f"{BASE}/fnlttSinglAcnt.json"
DIVIDEND_URL = f"{BASE}/alotMatter.json"

# DART reports the outcome in a status field; only "000" is success.
STATUS_MESSAGES = {
    "010": "등록되지 않은 API 키입니다",
    "011": "사용할 수 없는 API 키입니다. 오픈API에 등록되었는지 확인하세요",
    "012": "접근할 수 없는 IP입니다",
    "013": "조회된 데이터가 없습니다",
    "014": "파일이 존재하지 않습니다",
    "020": "요청 제한을 초과했습니다 (일 20,000건)",
    "021": "조회 가능한 회사 개수를 초과했습니다",
    "100": "필드 값이 부적절합니다",
    "101": "부적절한 접근입니다",
    "800": "시스템 점검 중입니다",
    "900": "정의되지 않은 오류입니다",
    "901": "사용자 계정의 개인정보 보유기간이 만료되었습니다",
}
NO_DATA = "013"

REPORT_CODES = {
    "annual": "11011",
    "half": "11012",
    "q1": "11013",
    "q3": "11014",
}

# list.json caps the search window when corp_code is not supplied.
MAX_WINDOW_DAYS = 90


class Dart(Source):
    name = "dart"
    citation = "금융감독원 전자공시시스템(DART) Open API"
    min_interval = 0.3

    def __init__(self):
        super().__init__()
        self._corp_index: pd.DataFrame | None = None

    def _auth(self) -> dict:
        return {"crtfc_key": config.require("DART_API_KEY", "DART Open API")}

    def _call(self, dataset: str, url: str, params: dict, *,
              max_age: float = DAY, refresh: bool = False) -> dict:
        fetched = self.get_json(
            dataset, url, params=params, auth=self._auth(),
            max_age=max_age, refresh=refresh,
        )
        data = fetched.data or {}
        status = str(data.get("status", ""))

        if status == NO_DATA:
            return {"_status": status, "list": [], "_cache_key": fetched.cache_key,
                    "_fetched_at": fetched.fetched_at}
        if status and status != "000":
            hint = STATUS_MESSAGES.get(status, data.get("message", ""))
            raise FetchError(f"dart/{dataset}: status {status} — {hint}")

        data["_cache_key"] = fetched.cache_key
        data["_fetched_at"] = fetched.fetched_at
        data["_status"] = status
        return data

    # --- corp code resolution ---------------------------------------------

    def _compact_index_path(self):
        return config.CACHE_DIR / self.name / "_listed_index.csv"

    def corp_index(self, *, refresh: bool = False) -> pd.DataFrame:
        """Listed companies with their DART corp_code.

        The upstream file covers ~110k entities including unlisted ones, and
        re-parsing 20MB of XML on every process start cost more than the
        network call it was meant to avoid. Only listed rows are needed for
        ticker resolution, so a compact index is kept alongside the raw cache.
        """
        if self._corp_index is not None and not refresh:
            return self._corp_index

        compact = self._compact_index_path()
        if compact.exists() and not refresh:
            import time

            if time.time() - compact.stat().st_mtime < 7 * DAY:
                frame = pd.read_csv(compact, dtype=str).fillna("")
                self._corp_index = frame
                return frame

        fetched = self.get_bytes(
            "corp_code",
            CORP_CODE_URL,
            params={},
            auth=self._auth(),
            max_age=7 * DAY,
            refresh=refresh,
        )
        body: bytes = fetched.data

        # An error comes back as XML or JSON rather than a ZIP, so check the
        # magic bytes before handing it to zipfile.
        if not body.startswith(b"PK"):
            snippet = body[:300].decode("utf-8", errors="replace")
            raise FetchError(f"dart/corp_code: ZIP 이 아닌 응답입니다 — {snippet}")

        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not xml_names:
                raise FetchError("dart/corp_code: ZIP 안에 XML 이 없습니다")
            raw = zf.read(xml_names[0])

        root = ElementTree.fromstring(raw.decode("utf-8", errors="ignore"))
        records = []
        for node in root.iter("list"):
            stock_code = (node.findtext("stock_code") or "").strip()
            if not stock_code:
                continue  # unlisted; cannot be reached from a ticker
            records.append({
                "corp_code": (node.findtext("corp_code") or "").strip(),
                "corp_name": (node.findtext("corp_name") or "").strip(),
                "stock_code": stock_code,
                "modify_date": (node.findtext("modify_date") or "").strip(),
            })

        frame = pd.DataFrame(records)
        compact.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(compact, index=False)
        self._corp_index = frame
        return frame

    def corp_code(self, ticker: str) -> str:
        """Resolve a 6-character KRX short code to a DART corp_code.

        Accepts "005930", "005930.KS", "A005930" — the suffix and prefix forms
        that show up across data providers.
        """
        code = str(ticker).strip().upper()
        code = code.split(".")[0]
        if code.startswith("A") and len(code) == 7:
            code = code[1:]

        index = self.corp_index()
        listed = index[index["stock_code"] == code]
        if listed.empty:
            raise FetchError(
                f"dart: 종목코드 {code} 에 해당하는 corp_code 를 찾을 수 없습니다. "
                "상장사가 아니거나 코드가 잘못되었을 수 있습니다."
            )
        # A company can appear more than once; the most recently modified row
        # is the live registration.
        return listed.sort_values("modify_date").iloc[-1]["corp_code"]

    def corp_name(self, ticker: str) -> str:
        code = str(ticker).strip().upper().split(".")[0]
        index = self.corp_index()
        listed = index[index["stock_code"] == code]
        return listed.iloc[-1]["corp_name"] if not listed.empty else code

    # --- disclosures -------------------------------------------------------

    def disclosures(
        self,
        *,
        ticker: str | None = None,
        start,
        end,
        types: str | None = None,
        max_age: float = 6 * 3600,
        refresh: bool = False,
    ) -> Table:
        """Disclosure filings in a date range.

        types is DART's pblntf_ty: A 정기공시, B 주요사항, C 발행, D 지분,
        E 기타, F 외부감사, I 거래소공시, J 공정위공시. Pass several as "IE".
        """
        code = self.corp_code(ticker) if ticker else None
        begin, finish = ymd(start), ymd(end)

        windows = self._windows(begin, finish, unlimited=bool(code))
        rows: list[dict] = []
        keys: list[str] = []
        fetched_at = None

        for w_start, w_end in windows:
            for type_code in (list(types) if types else [None]):
                page = 1
                while True:
                    params = {
                        "bgn_de": w_start,
                        "end_de": w_end,
                        "page_no": str(page),
                        "page_count": "100",
                    }
                    if code:
                        params["corp_code"] = code
                    if type_code:
                        params["pblntf_ty"] = type_code

                    data = self._call("disclosures", LIST_URL, params,
                                      max_age=max_age, refresh=refresh)
                    keys.append(data["_cache_key"])
                    fetched_at = data["_fetched_at"]

                    batch = data.get("list") or []
                    rows.extend(batch)

                    total_pages = int(data.get("total_page") or 1)
                    if page >= total_pages or not batch:
                        break
                    page += 1

        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame["date"] = pd.to_datetime(frame["rcept_dt"], format="%Y%m%d", errors="coerce")
            frame["viewer_url"] = (
                "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + frame["rcept_no"].astype(str)
            )
            frame = frame.sort_values("date").drop_duplicates("rcept_no").reset_index(drop=True)

        label = f"{self.corp_name(ticker)} 공시" if ticker else "전체 공시"
        return Table(
            id=f"dart_disclosures_{ticker or 'all'}_{begin}_{finish}",
            frame=frame,
            prov=Provenance(
                source=self.name,
                dataset="disclosures",
                label=f"{label} {begin}~{finish}",
                params={"ticker": ticker, "start": begin, "end": finish, "types": types},
                cache_keys=keys,
                fetched_at=fetched_at,
                citation=self.citation,
            ),
        )

    @staticmethod
    def _windows(begin: str, end: str, *, unlimited: bool) -> list[tuple[str, str]]:
        if unlimited:
            return [(begin, end)]
        start_ts, end_ts = pd.to_datetime(begin), pd.to_datetime(end)
        windows = []
        cursor = start_ts
        while cursor <= end_ts:
            stop = min(cursor + pd.Timedelta(days=MAX_WINDOW_DAYS - 1), end_ts)
            windows.append((cursor.strftime("%Y%m%d"), stop.strftime("%Y%m%d")))
            cursor = stop + pd.Timedelta(days=1)
        return windows

    # --- financials --------------------------------------------------------

    def financials(
        self,
        ticker: str,
        *,
        year: int,
        report: str = "annual",
        max_age: float = 7 * DAY,
        refresh: bool = False,
    ) -> Table:
        """주요계정 for one company and reporting period.

        report: annual | half | q1 | q3
        """
        reprt_code = REPORT_CODES.get(report, report)
        if reprt_code not in REPORT_CODES.values():
            raise ValueError(f"report 는 {sorted(REPORT_CODES)} 중 하나여야 합니다")

        params = {
            "corp_code": self.corp_code(ticker),
            "bsns_year": str(year),
            "reprt_code": reprt_code,
        }
        data = self._call("financials", FINANCIALS_URL, params,
                          max_age=max_age, refresh=refresh)

        frame = pd.DataFrame(data.get("list") or [])
        for col in ("thstrm_amount", "frmtrm_amount", "bfefrmtrm_amount"):
            if col in frame.columns:
                frame[col] = pd.to_numeric(
                    frame[col].astype(str).str.replace(",", "", regex=False),
                    errors="coerce",
                )

        return Table(
            id=f"dart_financials_{ticker}_{year}_{report}",
            frame=frame,
            prov=Provenance(
                source=self.name,
                dataset="financials",
                label=f"{self.corp_name(ticker)} {year} {report} 주요계정",
                params={"ticker": ticker, "year": year, "report": report},
                cache_keys=[data["_cache_key"]],
                fetched_at=data["_fetched_at"],
                frequency="quarterly" if report != "annual" else "annual",
                citation=self.citation,
            ),
        )

    def dividends(
        self,
        ticker: str,
        *,
        year: int,
        report: str = "annual",
        max_age: float = 7 * DAY,
        refresh: bool = False,
    ) -> Table:
        """배당에 관한 사항."""
        params = {
            "corp_code": self.corp_code(ticker),
            "bsns_year": str(year),
            "reprt_code": REPORT_CODES.get(report, report),
        }
        data = self._call("dividends", DIVIDEND_URL, params,
                          max_age=max_age, refresh=refresh)

        return Table(
            id=f"dart_dividends_{ticker}_{year}_{report}",
            frame=pd.DataFrame(data.get("list") or []),
            prov=Provenance(
                source=self.name,
                dataset="dividends",
                label=f"{self.corp_name(ticker)} {year} 배당 사항",
                params={"ticker": ticker, "year": year, "report": report},
                cache_keys=[data["_cache_key"]],
                fetched_at=data["_fetched_at"],
                citation=self.citation,
            ),
        )
