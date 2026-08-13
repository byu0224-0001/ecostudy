#!/usr/bin/env python3
"""study-os CLI (Layer 1: data).

    python3 cli.py doctor
    python3 cli.py price 005930 --start 2025-01-01 --end 2026-08-13 --save
    python3 cli.py disclosures 005930 --start 2026-05-01 --end 2026-08-13 --types A
    python3 cli.py financials 005930 --year 2025
    python3 cli.py snapshot 2026-08-12
    python3 cli.py transform krx krx_close_005930 yoy --save
    python3 cli.py cache
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from core import cache, config, sources, transform
from core.http import FetchError
from core.series import Series

CREDENTIALS = [
    ("DART_API_KEY", "DART 공시·재무", True),
    ("KRX_OPENAPI_KEY", "KRX 시세·지수·채권", True),
    ("ECOS_API_KEY", "한국은행 금리·물가·통화·환율", True),
    ("KRX_ID", "KRX MDC (ETF 구성종목)", False),
    ("KRX_PW", "KRX MDC (ETF 구성종목)", False),
]


def cmd_doctor(_args) -> int:
    print(f"repo         {config.ROOT}")
    print(f"cache        {config.CACHE_DIR}")
    print(f"series       {config.SERIES_DIR}")
    env_file = config.ROOT / ".env"
    print(f".env         {'있음' if env_file.exists() else '없음 (.env.example 복사하세요)'}")
    print()

    missing_required = []
    for var, what, required in CREDENTIALS:
        present = bool(os.getenv(var))
        mark = "OK  " if present else ("없음" if required else "미설정")
        tag = "" if required else "  (선택)"
        print(f"  {mark}  {var:<18} {what}{tag}")
        if required and not present:
            missing_required.append(var)

    print()
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        probe = config.CACHE_DIR / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print("  OK    캐시 디렉터리 쓰기 가능")
    except OSError as exc:
        print(f"  없음  캐시 디렉터리에 쓸 수 없습니다: {exc}")
        return 1

    if missing_required:
        print(f"\n{', '.join(missing_required)} 를 .env 에 넣어야 데이터를 가져올 수 있습니다.")
        return 1
    print("\n준비됐습니다.")
    return 0


def _report(obj, *, save: bool, rows: int = 8) -> None:
    if isinstance(obj, Series):
        print(obj.describe())
        print(obj.values.dropna().tail(rows).to_string())
    else:
        print(f"{obj.id}  rows={len(obj)}  <- {obj.prov.label}")
        print(f"출처: {obj.prov.citation}   수집: {obj.prov.fetched_at}")
        if len(obj):
            print(obj.frame.tail(rows).to_string(index=False))
    if save:
        print(f"\n저장: {obj.save()}")


def cmd_price(args) -> int:
    krx = sources.get("krx")
    if args.close_only:
        _report(krx.close(args.ticker, start=args.start, end=args.end,
                          refresh=args.refresh), save=args.save)
    else:
        _report(krx.price_history(args.ticker, start=args.start, end=args.end,
                                 refresh=args.refresh), save=args.save)
    return 0


def cmd_disclosures(args) -> int:
    dart = sources.get("dart")
    table = dart.disclosures(ticker=args.ticker, start=args.start, end=args.end,
                             types=args.types, refresh=args.refresh)
    if len(table):
        show = table.frame[["date", "report_nm", "flr_nm", "rcept_no"]].tail(args.rows)
        print(f"{table.id}  rows={len(table)}")
        print(f"출처: {table.prov.citation}")
        print(show.to_string(index=False))
        if args.save:
            print(f"\n저장: {table.save()}")
    else:
        print("해당 기간에 공시가 없습니다.")
    return 0


def cmd_financials(args) -> int:
    dart = sources.get("dart")
    table = dart.financials(args.ticker, year=args.year, report=args.report,
                            refresh=args.refresh)
    if len(table):
        cols = [c for c in ("sj_nm", "account_nm", "thstrm_amount", "frmtrm_amount")
                if c in table.frame.columns]
        print(f"{table.id}  rows={len(table)}  <- {table.prov.label}")
        print(table.frame[cols].to_string(index=False))
        if args.save:
            print(f"\n저장: {table.save()}")
    else:
        print("데이터가 없습니다. 연도나 보고서 종류를 확인하세요.")
    return 0


def cmd_snapshot(args) -> int:
    krx = sources.get("krx")
    _report(krx.snapshot(args.dataset, args.date, refresh=args.refresh),
            save=args.save, rows=args.rows)
    return 0


def _parse_match(pairs) -> dict:
    """COLUMN=값 은 완전 일치, COLUMN~패턴 은 정규식."""
    import re

    match = {}
    for pair in pairs or []:
        if "~" in pair and ("=" not in pair or pair.index("~") < pair.index("=")):
            col, _, pattern = pair.partition("~")
            match[col.strip()] = re.compile(pattern.strip())
        elif "=" in pair:
            col, _, value = pair.partition("=")
            match[col.strip()] = value.strip()
        else:
            raise ValueError(f"--match 는 'COLUMN=값' 또는 'COLUMN~정규식' 형식입니다: {pair}")
    return match


def cmd_series(args) -> int:
    krx = sources.get("krx")
    s = krx.series(
        args.dataset, args.field,
        start=args.start, end=args.end,
        match=_parse_match(args.match),
        unit=args.unit, refresh=args.refresh,
    )
    _report(s, save=args.save)
    return 0


def cmd_datasets(_args) -> int:
    from core.sources.krx import ENDPOINTS

    print("KRX 데이터셋 (키에 승인된 서비스만 조회 가능)\n")
    for key in sorted(ENDPOINTS):
        path, label = ENDPOINTS[key]
        print(f"  {key:<15} {label:<28} {path}")
    print("\n예시:")
    print("  python3 cli.py snapshot index_kospi 2026-08-12")
    print("  python3 cli.py series index_kospi CLSPRC_IDX --match 'IDX_NM=코스피' \\")
    print("      --start 2026-06-01 --end 2026-08-12")
    return 0


def cmd_transform(args) -> int:
    s = Series.load(args.source, args.series_id)
    kwargs = json.loads(args.kwargs) if args.kwargs else {}
    out = transform.apply_named(s, args.name, **kwargs)
    _report(out, save=args.save)
    return 0


def cmd_ecos_tables(args) -> int:
    frame = sources.get("ecos").tables(search=args.search, refresh=args.refresh)
    if frame.empty:
        print("검색 결과가 없습니다.")
        return 0
    print(f"{len(frame)}개 통계표\n")
    print(frame.head(args.rows).to_string(index=False))
    if len(frame) > args.rows:
        print(f"\n… {len(frame) - args.rows}개 더 있습니다 (--rows 로 조정)")
    return 0


def cmd_ecos_items(args) -> int:
    frame = sources.get("ecos").items(args.stat_code, refresh=args.refresh)
    if frame.empty:
        print("항목이 없습니다. 통계코드를 확인하세요.")
        return 0
    if args.search and "ITEM_NAME" in frame.columns:
        frame = frame[frame["ITEM_NAME"].str.contains(args.search, na=False)]
    print(f"{len(frame)}개 항목\n")
    print(frame.head(args.rows).to_string(index=False))
    return 0


def cmd_ecos_keystat(args) -> int:
    frame = sources.get("ecos").key_statistics(refresh=args.refresh)
    if args.search:
        mask = frame["KEYSTAT_NAME"].str.contains(args.search, na=False) | \
               frame["CLASS_NAME"].str.contains(args.search, na=False)
        frame = frame[mask]
    print(frame.to_string(index=False))
    return 0


def cmd_ecos_series(args) -> int:
    s = sources.get("ecos").series(
        args.stat_code, start=args.start, end=args.end, cycle=args.cycle,
        items=args.items, refresh=args.refresh,
    )
    _report(s, save=args.save)
    return 0


def cmd_cache(_args) -> int:
    if not config.CACHE_DIR.exists():
        print("캐시가 비어 있습니다.")
        return 0
    total_bytes = 0
    counts: dict[str, int] = {}
    for meta_file in config.CACHE_DIR.rglob("meta.json"):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        label = f"{meta.get('source')}/{meta.get('dataset')}"
        counts[label] = counts.get(label, 0) + 1
        total_bytes += meta.get("bytes", 0)

    if not counts:
        print("캐시가 비어 있습니다.")
        return 0
    print(f"{config.CACHE_DIR}\n")
    for label in sorted(counts):
        print(f"  {counts[label]:>4}건  {label}")
    print(f"\n총 {sum(counts.values())}건, {total_bytes/1_048_576:.1f} MB")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="study", description="study-os data layer")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--save", action="store_true", help="series/ 에 저장")
        sp.add_argument("--refresh", action="store_true", help="캐시 무시하고 재요청")
        return sp

    sp = sub.add_parser("doctor", help="설정과 자격 증명 점검")
    sp.set_defaults(func=cmd_doctor)

    sp = common(sub.add_parser("price", help="일별 시세"))
    sp.add_argument("ticker")
    sp.add_argument("--start", required=True)
    sp.add_argument("--end", required=True)
    sp.add_argument("--close-only", action="store_true", help="종가 Series 만")
    sp.set_defaults(func=cmd_price)

    sp = common(sub.add_parser("disclosures", help="공시 목록"))
    sp.add_argument("ticker")
    sp.add_argument("--start", required=True)
    sp.add_argument("--end", required=True)
    sp.add_argument("--types", help="pblntf_ty, 예: A 또는 IE")
    sp.add_argument("--rows", type=int, default=15)
    sp.set_defaults(func=cmd_disclosures)

    sp = common(sub.add_parser("financials", help="주요계정"))
    sp.add_argument("ticker")
    sp.add_argument("--year", type=int, required=True)
    sp.add_argument("--report", default="annual", choices=["annual", "half", "q1", "q3"])
    sp.set_defaults(func=cmd_financials)

    sp = common(sub.add_parser("snapshot", help="특정일 하루치 전체"))
    sp.add_argument("dataset", help="datasets 명령으로 목록 확인")
    sp.add_argument("date")
    sp.add_argument("--rows", type=int, default=10)
    sp.set_defaults(func=cmd_snapshot)

    sp = common(sub.add_parser("series", help="임의 데이터셋에서 시계열 추출"))
    sp.add_argument("dataset")
    sp.add_argument("field", help="예: CLSPRC_IDX, TDD_CLSPRC, CLSPRC_YD")
    sp.add_argument("--match", action="append", required=True,
                    help="'COLUMN=값'(완전일치) 또는 'COLUMN~정규식', 여러 번 지정 가능")
    sp.add_argument("--start", required=True)
    sp.add_argument("--end", required=True)
    sp.add_argument("--unit")
    sp.set_defaults(func=cmd_series)

    sp = sub.add_parser("datasets", help="KRX 데이터셋 목록")
    sp.set_defaults(func=cmd_datasets)

    sp = common(sub.add_parser("transform", help="저장된 series 에 변환 적용"))
    sp.add_argument("source")
    sp.add_argument("series_id")
    sp.add_argument("name", help=f"사용 가능: {sorted(transform.REGISTRY)}")
    sp.add_argument("--kwargs", help='JSON, 예: \'{"months": 3}\'')
    sp.set_defaults(func=cmd_transform)

    ecos = sub.add_parser("ecos", help="한국은행 ECOS — 금리·물가·통화·환율")
    esub = ecos.add_subparsers(dest="ecos_command", required=True)

    sp = esub.add_parser("tables", help="통계표 검색 (통계코드 찾기)")
    sp.add_argument("--search", help="통계표명 키워드")
    sp.add_argument("--rows", type=int, default=30)
    sp.add_argument("--refresh", action="store_true")
    sp.set_defaults(func=cmd_ecos_tables)

    sp = esub.add_parser("items", help="통계표의 세부항목 (항목코드 찾기)")
    sp.add_argument("stat_code")
    sp.add_argument("--search", help="항목명 키워드")
    sp.add_argument("--rows", type=int, default=30)
    sp.add_argument("--refresh", action="store_true")
    sp.set_defaults(func=cmd_ecos_items)

    sp = esub.add_parser("keystat", help="100대 통계지표 최신값")
    sp.add_argument("--search", help="지표명·분류 키워드")
    sp.add_argument("--refresh", action="store_true")
    sp.set_defaults(func=cmd_ecos_keystat)

    sp = esub.add_parser("series", help="시계열 조회")
    sp.add_argument("stat_code")
    sp.add_argument("--items", action="append", help="항목코드, 여러 번 지정 가능")
    sp.add_argument("--cycle", default="M", choices=["A", "S", "Q", "M", "SM", "D"])
    sp.add_argument("--start", required=True, help="주기에 맞춰: 202401 / 20240131 / 2024")
    sp.add_argument("--end", required=True)
    sp.add_argument("--save", action="store_true")
    sp.add_argument("--refresh", action="store_true")
    sp.set_defaults(func=cmd_ecos_series)

    sp = sub.add_parser("cache", help="캐시 현황")
    sp.set_defaults(func=cmd_cache)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except config.MissingCredential as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2
    except FetchError as exc:
        print(f"수집 실패: {exc}", file=sys.stderr)
        return 3
    except (ValueError, KeyError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
