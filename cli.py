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
import datetime as _dt
import json
import os
import sys

from pathlib import Path

from core import (config, events, feeds, golden, llm, newsletters, sessions,
                  sources, topics, transform)
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


READY_MARK = {"full": "완비", "partial": "보강", "external": "외부"}


def cmd_topics_list(args) -> int:
    backlog = topics.load()
    rows = backlog.select(axis=args.axis, ready=args.ready, status=args.status,
                          books=True if args.books else (False if args.no_books else None))
    if not rows:
        print("해당하는 후보가 없습니다.")
        return 0

    for t in rows:
        axis = t.get("axis")
        ready = (t.get("data") or {}).get("ready", "-")
        tag = READY_MARK.get(ready, ready if t.get("data") else "독서")
        print(f"  {t['id']:<6} {axis}축 {topics.AXES.get(axis, ''):<14} "
              f"[{tag:<2}] {t['title']}")

    print(f"\n{len(rows)}건")
    if args.axis is None and not args.books:
        cov = topics.coverage(backlog)
        bar = "  ".join(f"{a}축 {n}" for a, n in cov.items())
        print(f"축별 후보: {bar}   독서 {len(backlog.books)}")
    return 0


def cmd_topics_show(args) -> int:
    t = topics.load().get(args.topic_id)
    if not t:
        print(f"{args.topic_id} 를 찾을 수 없습니다.")
        return 1

    axis = t.get("axis")
    print(f"\n{t['id']}  {t['title']}")
    print(f"{axis}축 {topics.AXES.get(axis, '')}"
          + (f" · {t['judgment']} 판단" if t.get("judgment") else ""))
    print(f"\n  질문\n    {' '.join(str(t['question']).split())}")

    if t.get("why_now"):
        print("\n  지금인 이유")
        for w in t["why_now"]:
            if isinstance(w, dict):
                print(f"    - {w['text']}\n        출처 {w['source']}")
            else:
                print(f"    - {w}")

    if t.get("splits"):
        print("\n  갈리는 지점")
        for s in t["splits"]:
            print(f"    - {s}")

    data = t.get("data") or {}
    if data:
        print(f"\n  데이터  {READY_MARK.get(data.get('ready'), data.get('ready'))}")
        for s in data.get("series") or []:
            bits = " ".join(f"{k}={v}" for k, v in s.items() if k != "note")
            print(f"    - {bits}")
            if s.get("note"):
                print(f"        {s['note']}")
        for k in ("missing", "caveat", "note"):
            if data.get(k):
                print(f"    {k}: {data[k]}")

    print(f"\n  참가자 산출물\n    {t.get('output', '-')}")
    print(f"\n  준비 {t.get('prep', '?')}시간 · 유입 {t.get('origin', '-')} "
          f"· 상태 {t.get('status')}")
    if t.get("links"):
        print(f"  연결: {', '.join(t['links'])}")
    print()
    return 0


def cmd_topics_validate(_args) -> int:
    backlog = topics.load()
    inbox = topics.load_inbox()
    issues = topics.validate(backlog) + topics.validate_inbox(inbox)
    if not issues:
        print(f"후보 {len(backlog.topics)}건 + 독서 {len(backlog.books)}건 "
              f"+ 포착 {len(inbox.items)}건, 규칙 위반 없음.")
        return 0
    print(f"{len(issues)}건의 문제:\n")
    for i in issues:
        print(f"  {i.topic:<8} {i.message}")
    return 1


CHECK_LABEL = {
    "matches": "일치 — 확인하는 자리가 된다. 한 단계 더 파야 산다",
    "contradicts": "어긋남 — 서사와 데이터가 다르다. 가장 좋은 주제",
    "cause_open": "관측 일치·인과 미확정 — 사실은 정해졌고 원인이 갈린다. 찬반이 진짜로 갈리는 자리",
    "unverifiable": "확인 불가 — 탈락",
}


def cmd_topics_capture(args) -> int:
    inbox = topics.load_inbox()
    item = {
        "id": inbox.next_id(),
        "headline": args.headline,
        "claim": args.claim,
        "source": args.source,
        "seen": args.seen or _dt.date.today().isoformat(),
        "origin": args.origin,
        "verify": args.verify,
        "check": None,
        "status": "raw",
    }
    if args.by:
        item["by"] = args.by

    inbox.items.append(item)
    path = topics.save_inbox(inbox)

    print(f"{item['id']} 포착됨 → {path.relative_to(config.ROOT)}")
    print(f"  주장   {item['claim']}")
    print(f"  확인   {item['verify']}")
    if not args.verify:
        print("\n  verify 가 비었습니다. 데이터로 확인할 방법이 없으면 주제가 아닙니다.")
    print("\n  다음: 지표로 대조한 뒤  cli.py topics check "
          f"{item['id']} --result contradicts")
    return 0


def cmd_topics_check(args) -> int:
    inbox = topics.load_inbox()
    item = inbox.get(args.item_id)
    if not item:
        print(f"{args.item_id} 를 찾을 수 없습니다.")
        return 1

    item["check"] = args.result
    item["check_note"] = args.note
    if args.result == "unverifiable":
        item["status"] = "dropped"
    topics.save_inbox(inbox)

    print(f"{item['id']}  {item['headline']}")
    print(f"  주장   {item['claim']}")
    print(f"  판정   {CHECK_LABEL[args.result]}")
    if args.note:
        print(f"  근거   {args.note}")
    # 승격을 고민하는 시점이 중복을 알아야 할 시점이다. verify 는 넣지 않는다.
    # 데이터셋 코드 같은 배관 문자열이 분모만 키워 포함률을 떨어뜨린다.
    matches = sessions.similar(
        sessions.load(), f"{item['headline']} {item['claim']}")
    if matches:
        print("\n  비슷한 과거 회차")
        for m in matches:
            when = m.session.date.isoformat() if m.session.date else "?"
            print(f"    {m.session.id} ({when})  {m.session.raw.get('title', '')}")
            print(f"      겹친 말  {', '.join(m.shared[:6])}")
        print("    반복인지 후속인지는 사람이 판단한다. sessions show 로 확인.")

    if args.result in ("contradicts", "cause_open"):
        print("\n  승격하려면 topics/backlog.yaml 에 축·질문·splits·데이터·산출물을 채운다.")
    return 0


def cmd_topics_feed(args) -> int:
    items, errors = feeds.collect(layer=args.layer, days=args.days,
                                  only=args.only)
    if errors:
        print("가져오지 못한 피드:")
        for e in errors:
            print(f"  {e}")
        print()

    added = 0 if args.no_record else feeds.record(items)

    if not items:
        print(f"최근 {args.days}일 내 항목이 없습니다.")
        return 0

    if args.windows:
        return _show_windows(added)

    repeated = feeds.themes(items, min_feeds=args.min_feeds)
    if repeated:
        print(f"여러 채널에 걸쳐 반복된 말 — 최근 {args.days}일")
        for word, n_feeds, n in repeated:
            print(f"  {word:<14} {n_feeds}개 채널 · {n}회")
        print("\n  한 채널이 반복하는 것은 그 채널의 관심사고, 여러 채널이 같은 주에")
        print("  같은 말을 하면 그것이 이번 주 통념이다. 검증할 주장은 여기서 찾는다.\n")

    by_layer: dict[str, dict[str, list]] = {}
    for i in items:
        by_layer.setdefault(i.layer, {}).setdefault(i.name, []).append(i)

    for layer, sources_ in by_layer.items():
        print(f"── {layer} " + "─" * 52)
        for name, rows in sources_.items():
            print(f"\n  {name}")
            for i in rows[: args.per_feed]:
                age = f"{i.age_days:.0f}일 전" if i.published else "날짜없음"
                print(f"    {age:>7}  {i.title[:78]}")
            if len(rows) > args.per_feed:
                print(f"             … {len(rows) - args.per_feed}건 더")
        print()

    tail = f"  (누적 저장소에 {added}건 추가)" if added else ""
    print(f"총 {len(items)}건.{tail} 검증 가능한 주장을 골라 topics capture 로 넣는다.")
    return 0


HORIZON = 30


def _show_windows(added: int) -> int:
    """며칠에 걸쳐 살아남는 말인지 본다. 헤드라인과 구조적 변화의 구분."""
    stored = feeds.history(HORIZON)
    if not stored:
        print("누적된 것이 없습니다. topics feed 를 며칠 돌린 뒤 다시 보세요.")
        return 0

    reach = max((i.age_days for i in stored), default=0)
    spread = len({i.published.strftime("%Y-%m-%d")
                  for i in stored if i.published})
    print(f"누적 {len(stored)}건 · {spread}개 날짜 · 가장 오래된 것 {reach:.1f}일 전"
          f"{f' · 이번에 {added}건 추가' if added else ''}\n")

    if spread < 3:
        print(f"  수집 날짜가 {spread}일뿐이라 지속성은 아직 판정할 수 없습니다.")
        print("  RSS 는 최근 것만 돌려줍니다. 매일 한 번씩 며칠 쌓아야 갈립니다.\n")

    rows = feeds.persistence(stored)
    if not rows:
        print("여러 채널에 걸친 반복이 아직 없습니다.")
        return 0

    print(f"  {'말':<12}{'채널':>4}{'등장일':>6}{'최초':>7}   판정")
    for p in rows:
        verdict = ("구조적 — 오래 살아남는다" if p.days >= 10
                   else "지속 — 이번 주 이야기" if p.days >= 3
                   else "일회성 — 헤드라인일 수 있다")
        print(f"  {p.word:<12}{p.feeds:>4}{p.days:>6}{p.oldest:>6.0f}일   {verdict}")
    print("\n  채널 = 서로 다른 시각인지. 등장일 = 살아남는 이야기인지.")
    print("  둘 다 기사 건수가 아니다. 한 사건을 서른 곳이 베껴도 사건은 하나다.")
    return 0


def cmd_sessions_list(_args) -> int:
    history = sessions.load()
    if not history:
        print("회차 기록이 없습니다.  cli.py sessions new --help")
        return 0
    for s in history.ordered:
        kind = "독서" if s.is_book else f"{s.axis}축"
        date = s.date.isoformat() if s.date else "날짜없음"
        print(f"  {s.id:<7} {date}  [{kind:<3}] {s.raw.get('title', '')}")
        if s.open_questions:
            print(f"          미해결 {len(s.open_questions)}건")
    print(f"\n{len(history)}회")
    return 0


def cmd_sessions_show(args) -> int:
    s = sessions.load().get(args.session_id)
    if not s:
        print(f"{args.session_id} 를 찾을 수 없습니다.")
        return 1

    print(f"\n{s.id}  {s.raw.get('title', '')}")
    print(f"{s.date}  {s.axis}축 {sessions.AXES.get(s.axis, '')}"
          f"  {s.raw.get('judgment', '')}")
    print(f"\n  질문  {s.raw.get('question', '')}")

    if settled := s.raw.get("settled"):
        print("\n합의된 사실")
        for item in settled:
            print(f"  · {item.get('text', '')}")
            print(f"    출처 {item.get('source', '')}")

    if split := s.raw.get("split"):
        print("\n갈린 지점 — 이 회차의 알맹이")
        for item in split:
            print(f"  · {item.get('point', '')}")
            for p in item.get("positions") or []:
                print(f"      – {p}")

    if checked := s.raw.get("claims_checked"):
        print("\n검증한 주장")
        for c in checked:
            print(f"  [{c.get('result', '?')}] {c.get('claim', '')}")

    if s.open_questions:
        print("\n미해결 — 다음 후보")
        for o in s.open_questions:
            print(f"  · {o.get('question', '')}")
            print(f"    왜 못 풀었나  {o.get('why', '')}")
    print()
    return 0


def cmd_sessions_validate(_args) -> int:
    history = sessions.load()
    issues = sessions.validate(history)
    if not issues:
        print(f"회차 {len(history)}건, 규칙 위반 없음.")
        return 0
    print(f"{len(issues)}건의 문제:\n")
    for i in issues:
        print(f"  {i.session:<8} {i.message}")
    return 1


def cmd_sessions_open(_args) -> int:
    history = sessions.load()
    rows = [(s, o) for s in history.ordered for o in s.open_questions]
    if not rows:
        print("미해결 질문이 없습니다.")
        return 0
    print("미해결 질문 — 후보 유입 경로\n")
    for s, o in rows:
        date = s.date.isoformat() if s.date else "?"
        print(f"  {s.id} ({date}, {s.axis}축)")
        print(f"    {o.get('question', '')}")
        print(f"    왜 못 풀었나  {o.get('why', '')}")
    print(f"\n{len(rows)}건. 준비가 되면 topics capture 로 옮긴다.")
    return 0


def cmd_sessions_balance(_args) -> int:
    history = sessions.load()
    if not history:
        print("회차 기록이 없어 배치를 판단할 수 없습니다.")
        return 0

    counts = sessions.coverage(history)
    print("지난 분기 축 분포")
    for axis, n in counts.items():
        bar = "■" * n if n else "·"
        print(f"  {axis}축 {sessions.AXES[axis]:<12} {bar:<8} {n}회")

    print("\n배치 규칙")
    for rule in sessions.placement(history):
        print(f"  {'OK ' if rule.ok else '어긋남'}  {rule.name:<20} {rule.detail}")

    wanted, repeated = sessions.suggest_axes(history)
    print("\n다음 회차")
    if repeated:
        names = ", ".join(f"{a}축({sessions.AXES[a]})" for a in repeated)
        print(f"  직전과 같음  {names}")
    names = ", ".join(f"{a}축({sessions.AXES[a]})" for a in wanted)
    print(f"  권장        {names}")
    print("\n  권고이지 제약이 아니다. 그날 시장의 이슈가 다른 축을 가리키면")
    print("  뉴스를 따라가라. 균형은 아래처럼 분기마다 돌아본다.")

    s = sessions.skew(history)
    print(f"\n분기 편중  (회차 {s.total}건)")
    if s.starved:
        names = ", ".join(f"{a}축({sessions.AXES[a]})" for a in s.starved)
        print(f"  굶은 축  {names}")
        print("  다음 분기에 여기부터 의도적으로 채운다.")
    if s.lopsided and s.dominant:
        share = s.counts[s.dominant] / s.total * 100
        print(f"  쏠림    {s.dominant}축({sessions.AXES[s.dominant]})이 "
              f"{share:.0f}% — 시장이 한 이야기만 한 시기였는지 확인하라")
    if not s.starved and not s.lopsided:
        print("  고르다. 뉴스를 따라갔는데도 균형이 맞았다는 뜻이다.")
    print(f"\n  cli.py topics list --axis {wanted[0]}")
    return 0


def cmd_sessions_similar(args) -> int:
    history = sessions.load()
    text = args.text
    if args.topic:
        topic = topics.load().get(args.topic)
        if not topic:
            print(f"{args.topic} 를 찾을 수 없습니다.")
            return 1
        text = " ".join(str(topic.get(f, "")) for f in ("title", "question"))
        text += " " + " ".join(topic.get("splits") or [])

    matches = sessions.similar(history, text)
    if not matches:
        print("비슷한 과거 회차가 없습니다.")
        return 0
    print("비슷한 과거 회차\n")
    for m in matches:
        s = m.session
        date = s.date.isoformat() if s.date else "?"
        print(f"  {s.id} ({date}, {s.axis}축)  겹침 {m.score:.0%}")
        print(f"    {s.raw.get('title', '')}")
        print(f"    {s.raw.get('question', '')[:76]}")
        print(f"    겹친 말  {', '.join(m.shared[:8])}")
    print("\n  숫자보다 겹친 말을 보라. 흔한 말 둘이 겹친 것과 고유한 말 둘이")
    print("  겹친 것은 점수가 같아도 전혀 다르다.")
    print("\n  겹친다고 자동으로 거르지 않는다. 반복인지 후속인지는 사람만 안다.")
    print("  같은 주제를 반년 뒤에 다시 보는 것은 좋은 일이고, 모르고 하는 것만 문제다.")
    return 0


def cmd_topics_mail(args) -> int:
    directory = Path(args.dir).expanduser()
    if not directory.is_dir():
        print(f"없는 폴더: {directory}")
        return 1

    use_llm = args.llm and llm.available()
    if args.llm and not use_llm:
        print("LLM_API_KEY 가 없어 정규식 경로로 돌립니다.")
        print("  .env 에 LLM_API_KEY 를 넣으면 블록 분류와 주장 추출이 켜집니다.\n")

    letters = newsletters.read_dir(directory, enhance=use_llm,
                                   refresh=args.refresh)
    if not letters:
        print(f"{directory} 에 .eml 이 없다.")
        print("네이버 메일에서 뉴스레터를 골라 내려받으면 된다.")
        return 1

    mode = f"모델 ({llm.model_for('classify')} / {llm.model_for('extract')})" \
        if use_llm else "정규식"
    print(f"\n{len(letters)}통  ({directory})  추출: {mode}")
    for letter in letters:
        when = letter.date.strftime("%m/%d") if letter.date else "  ?  "
        flag = "  [LLM 생성]" if letter.generated else ""
        blocks_note = f"  블록 {len(letter.blocks):>2}" if letter.blocks else ""
        print(f"  {when} {letter.name:<11}{blocks_note}  주장 {len(letter.claims):>2}"
              f"  일정 {len(letter.schedule):>2}{flag}  {letter.subject[:34]}")
        for note in letter.notes:
            print(f"        ! {note[:88]}")

    days = sorted({newsletters._day(letter) for letter in letters},
                  reverse=True)
    target = args.day or days[0]

    if args.blocks:
        print(f"\n■ {target} — 블록 분류")
        if not use_llm:
            print("  모델 없이는 성격을 가릴 수 없어 전부 unknown 이다.")
        for letter in letters:
            if newsletters._day(letter) != target:
                continue
            print(f"\n  {letter.name}")
            for b in letter.blocks:
                mark = ("발견" if b.discovery_allowed else "   ·") + \
                       ("·근거" if b.evidence_allowed else "")
                print(f"    [{b.index:>2}] {b.content_type:<11} {mark:<7}"
                      f" {(b.heading or b.body)[:50]}")
                if b.reason:
                    print(f"          {b.reason[:74]}")

    print(f"\n■ {target} — 여러 곳이 함께 다룬 말")
    rows = newsletters.signals(letters, day=target)
    if not rows:
        print("  겹치는 게 없다. 그날은 각자 다른 이야기를 했다는 뜻이다.")
    for word, names in rows:
        print(f"  {word:<14} {len(names)}곳  {', '.join(names)}")
    print("\n  이건 후보가 아니라 '오늘의 통념' 목록이다. 여러 곳이 같은 말을")
    print("  했다는 사실은 그 말이 맞다는 뜻이 아니라, 대조할 값이 있다는 뜻이다.")

    schedule = [(letter, item) for letter in letters
                if newsletters._day(letter) == target
                for item in letter.schedule]
    if schedule:
        print(f"\n■ {target} — 예정된 일")
        for letter, item in schedule:
            print(f"  [{letter.name}] {item.text[:84]}")
        print("\n  이 목록이 뉴스레터에서 가장 값진 부분이다. 지난 수치는 어댑터로")
        print("  가져오지만 앞으로 있을 일은 어디서도 못 가져온다.")

    if args.claims:
        print(f"\n■ {target} — 추출된 주장")
        for letter in letters:
            if newsletters._day(letter) != target:
                continue
            mark = "  ※ LLM 생성물이다. 사실 확인이 더 필요하다." \
                if letter.generated else ""
            print(f"\n  {letter.name}{mark}")
            for item in letter.claims[:args.claims]:
                where = f"[{item.section[:10]}] " if item.section else ""
                ctype = f" ({item.claim_type})" if item.claim_type else ""
                print(f"    - {where}{item.text[:88]}{ctype}")
                if item.source_span:
                    # 대조 결과를 같이 보여준다. exact 가 아니면 그대로
                    # 인용하면 안 된다.
                    seal = "" if item.quotable else f"  ⚠ {item.provenance}"
                    print(f"        원문  {item.source_span[:70]}{seal}")
                if item.interpretation:
                    print(f"        해석  {item.interpretation[:76]}")
                if item.verification_mode:
                    line = f"        검증  {item.verification_mode}"
                    if item.verify and item.verify != "없음":
                        line += f" → {item.verify}"
                    if not item.evidence_allowed:
                        line += "  (근거불가)"
                    print(line)

    if args.events:
        from core import events as ev
        evts = ev.cluster(letters, day=target, limit=ev.EVENT_CAP)
        cands = ev.study_candidates(evts)
        print(f"\n■ {target} — 사건 {len(evts)}건 / 스터디 후보 {len(cands)}건")
        for e in evts[:8]:
            when = f"{e.day}~{e.last_day}" if e.spans_days else e.day
            solo = "  단독" if e.source_count == 1 else ""
            print(f"\n  {e.event_id}  신뢰 {e.confidence:.2f}{solo}  {when}"
                  f"  [{', '.join(e.sources)}]")
            print(f"    {e.label}")
            for c in e.claims[:2]:
                print(f"    · [{c['source']}] {c['claim'][:70]}")
        if cands:
            print(f"\n  --- 스터디 후보 (V0 휴리스틱) ---")
            for c in cands:
                print(f"\n  {c['event_id']}  {c['label']}  신뢰 {c['confidence']:.2f}")
                print(f"    질문  {c['core_question']}")
                print(f"    검증  {', '.join(c['verification_modes'])}")
                if c["single_source"]:
                    print(f"    한 곳만 다룬 사건이다. 교차 확인이 먼저다.")
        print("\n  사건은 주제가 아니다. 좋은 주제는 여러 사건을 하나의 질문으로")
        print("  묶는 경우가 많다. 이 목록은 그 재료다.")

    if getattr(args, "cost", False):
        t = llm.TELEMETRY
        print(f"\n■ 호출 기록")
        if not t.calls:
            print("  LLM 을 쓰지 않았다.")
        else:
            print(f"  {'단계':<18}{'모델':<16}{'호출':>5}{'캐시':>5}"
                  f"{'입력':>9}{'출력':>8}{'비용':>10}")
            for stage, r in t.by_stage.items():
                print(f"  {stage:<18}{r['model']:<16}{r['calls']:>5}"
                      f"{r['cached']:>5}{r['tokens_in']:>9,}"
                      f"{r['tokens_out']:>8,}{'$' + format(r['cost'], '.4f'):>10}")
            print(f"\n  실호출 {t.cold}건 / 캐시 {t.cached}건"
                  f"  재시도 {t.retries}회  합계 ${t.cost:.4f}")
            if t.cached:
                print("  캐시된 만큼은 오늘 안 낸 돈이다. 매일 오는 새 메일은")
                print("  전부 실호출이므로 운영 비용은 실호출 기준으로 봐라.")
            if t.unknown_price:
                print(f"  가격 미상: {', '.join(sorted(t.unknown_price))} — 합계 과소평가")

    print("\n뉴스레터는 1차 소스가 아니다. 여기 있는 무엇도 사실로 옮기지 마라.")
    print("쓸 것을 고르면  study topics capture  로 포착하고 반드시 대조를 거쳐라.")
    print()
    return 0


# --- 정답지 --------------------------------------------------------------

WANT = {"blocks": 80, "claims": 100, "recall": 20,
        "cluster": 30, "pairs": 80, "topics": 15}

# 100개를 다 붙이고 나서 기준이 흔들리면 100개를 다시 해야 한다. 앞의
# 스무 개로 경계를 먼저 확인하고, 기준을 고친 뒤 나머지를 붙인다.
CALIBRATION = 20


def _claim_id(letter, item) -> str:
    """다시 뽑아도 같은 주장이면 같은 id 여야 붙인 라벨이 안 날아간다.

    파이썬 hash() 는 실행마다 값이 달라서 못 쓴다.
    """
    import hashlib
    seed = f"{letter.name}:{newsletters._day(letter)}:{item.block}:{item.text}"
    return (f"{letter.name}:b{item.block}:"
            f"{hashlib.sha256(seed.encode()).hexdigest()[:8]}")


def _load_mail(args):
    letters = newsletters.read_dir(
        Path(args.dir).expanduser(),
        enhance=getattr(args, "llm", False),
        refresh=getattr(args, "refresh", False))
    if not letters:
        print(f"{args.dir} 에서 .eml 을 못 찾았다.")
    return letters


def cmd_golden_seed(args) -> int:
    if args.split == "holdout":
        print("holdout 은 손대지 마라. 한 번 보면 그 순간 개발용이 된다.")
        print("마지막에 딱 한 번 돌려서 성적을 확인할 몫이다.")
        return 1

    letters = _load_mail(args)
    if not letters:
        return 1
    layers = list(golden.LAYERS) if args.layer == "all" else [args.layer]
    golden.note_corpus(args.split,
                       claims=sum(len(L.claims) for L in letters),
                       blocks=sum(len(L.blocks) for L in letters))

    for layer in layers:
        want = args.n or WANT[layer]
        rows = _seed_rows(layer, letters, want)
        keep = golden.annotated(golden.load(layer, args.split))
        if keep:
            done = {r.get("id") for r in keep}
            rows = keep + [r for r in rows if r.get("id") not in done]
            print(f"  기존 라벨 {len(keep)}건은 그대로 두었다.")
        p = golden.save(layer, rows, args.split, seeding=True)
        print(f"{layer:<8} {len(rows):>4}건 → {p}")

    print("\n표본은 코드가 골랐지만 정답은 사람만 쓴다. 저장할 때 답안 칸을")
    print("한 번 더 비우므로 모델이 만든 값이 정답으로 새어 들어갈 수 없다.")
    print("휴리스틱 추측(heuristic)이 같이 들어 있지만 그건 답이 아니라")
    print("우리가 맞히려는 대상이다. 보고 따라 쓰면 채점이 무의미해진다.")
    print(f"\n주장 {CALIBRATION}건부터다. 100건을 다 붙인 뒤 기준이 흔들리면")
    print("100건을 다시 해야 한다. 앞의 스무 개로 경계를 먼저 확인하고,")
    print("기준을 고친 뒤 나머지를 붙인다.")
    return 0


def _seed_rows(layer: str, letters: list, want: int) -> list[dict]:
    if layer == "blocks":
        rows = []
        for L in letters:
            for b in L.blocks:
                rows.append({
                    "id": f"{L.name}:{newsletters._day(L)}:b{b.index}",
                    "source": L.name, "block": b.index,
                    "heading": b.heading[:60],
                    "text": b.text[:300],
                    "heuristic": b.content_type,
                    "label": None, "note": "",
                })
        return _spread(rows, want, key=lambda r: r["heuristic"])

    if layer == "claims":
        rows = []
        for L in letters:
            for i in L.claims:
                rows.append({
                    "id": _claim_id(L, i),
                    "source": L.name, "block": i.block,
                    "claim": i.text, "span": i.source_span,
                    "heuristic": i.claim_type,
                    "heuristic_mode": i.verification_mode,
                    "provenance": i.provenance,
                    "utility": None,      # 남길까 버릴까 — 이게 첫 질문이다
                    "label": None,        # 맞는 claim_type
                    "label_mode": None,   # 맞는 verification_mode
                    "atomic": None,       # 한 사실만 담았는가
                    "duplicate_of": None,
                    "note": "",
                })
        rows = _spread(rows, want, key=lambda r: r["heuristic"])
        for n, r in enumerate(rows):
            r["calibration"] = n < CALIBRATION
        return rows

    if layer == "recall":
        # 주장마다 라벨을 붙이는 것으로는 '빠진 것'을 못 센다. 아예 안 뽑힌
        # 것에는 붙일 라벨이 없다. 그래서 블록을 통째로 읽고 무엇이 빠졌는지
        # 적는 층을 따로 둔다.
        rows = []
        for L in letters:
            got = {}
            for i in L.claims:
                got.setdefault(i.block, []).append(i.text)
            for b in L.blocks:
                if b.content_type == "ui_residue" or not b.discovery_allowed:
                    continue
                rows.append({
                    "id": f"{L.name}:{newsletters._day(L)}:b{b.index}",
                    "source": L.name, "block": b.index,
                    "text": b.text[:1200],
                    "extracted": got.get(b.index, []),
                    "reviewed": False,
                    "kept_count": None,   # 뽑힌 것 중 실제로 필요했던 수
                    "missed": [],         # 뽑혔어야 하는데 빠진 주장
                    "note": "",
                })
        return _spread(rows, want, key=lambda r: r["source"])

    if layer == "cluster":
        # 좁은 구간 하나를 빠짐없이 훑는다. 기계가 올린 쌍만 정답지에 넣으면
        # 찾기 재현율은 정의상 100% 가 된다 — 놓친 쌍은 정답지에 없으니까.
        # 하루치를 통째로 쓰면 4천 쌍이라 사람이 못 한다. 대신 그 하루에서
        # 무작위로 추려 그 안을 전부 본다. 치우치지 않은 표본이므로 거기서
        # 나온 재현율은 그대로 믿을 수 있다.
        import random
        day = min({newsletters._day(L) for L in letters})
        pool = [(L, i) for L in letters if newsletters._day(L) == day
                for i in L.claims if i.verification_mode != "not_researchable"]
        rng = random.Random(11)
        rng.shuffle(pool)
        pool = pool[:want]

        machine = {}
        for e in events.cluster(letters):
            for c in e.claims:
                machine[(c["source"], c["claim"])] = e.event_id
        return [{
            "id": _claim_id(L, i),
            "day": day, "source": L.name, "claim": i.text,
            "machine_event": machine.get((L.name, i.text)),
            "event_group": None,   # 같은 사건이면 같은 이름을 적는다
            "theme_group": None,   # 사건은 다르지만 같은 줄기면 같은 이름
            "note": "",
        } for L, i in pool]

    if layer == "pairs":
        # 사건끼리가 아니라 주장끼리 짝짓는다. 묶는 기계가 답하는 질문이
        # "이 두 주장이 한 사건인가" 이기 때문이다. 이유는 core/events.py.
        rows = events.claim_pairs(letters, limit=want)
        for r in rows:
            r["id"] = r.pop("pair_id")
        return rows

    if layer == "topics":
        evts = events.cluster(letters)
        rows = []
        for c in events.study_candidates(evts, limit=want):
            rows.append({
                "id": c["event_id"], "label_text": c["label"],
                "question": c["core_question"],
                "sources": c["sources"], "confidence": c["confidence"],
                "sample_claim": c["sample_claim"],
                "criteria": {k: None for k in golden.TOPIC_CRITERIA},
                "label": None,           # usable | weak | reject
                "note": "",
            })
        return rows
    return []


def _spread(rows: list[dict], want: int, key) -> list[dict]:
    """분류마다 골고루 뽑는다.

    앞에서 그냥 자르면 흔한 분류만 정답지에 들어간다. 드문 분류일수록
    틀리기 쉬운데 그게 빠지면 성적이 실제보다 좋게 나온다.
    """
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in rows:
        buckets[key(r)].append(r)
    out, n = [], 0
    biggest = max((len(b) for b in buckets.values()), default=0)
    while len(out) < want and n < biggest:
        for k in sorted(buckets):
            if n < len(buckets[k]) and len(out) < want:
                out.append(buckets[k][n])
        n += 1
    return out


def cmd_golden_status(args) -> int:
    print(f"\n■ 정답지 진행 ({args.split})")
    for layer, (done, total) in golden.progress(args.split).items():
        bar = "" if not total else f"  {done / total * 100:.0f}%"
        print(f"  {layer:<8} {done:>4} / {total:<4}{bar}")
    if args.split == "dev":
        print("\n  지금 14통은 프롬프트와 스키마를 고치는 내내 봐 온 자료다.")
        print("  여기 성적이 좋은 건 당연하다. 새 메일 5~10통을 holdout 으로")
        print("  따로 두고 끝까지 열지 마라. 그게 유일한 진짜 성적표다.")
    print()
    return 0


def cmd_golden_audit(args) -> int:
    letters = _load_mail(args)
    if not letters:
        return 1
    total = sum(len(L.claims) for L in letters)
    print(f"\n■ 자동 점검 — 주장 {total}건 (사람 손 필요 없음)")

    prov = golden.audit_provenance(letters)
    print(f"\n  출처 대조")
    for k, v in prov["counts"].items():
        if v:
            print(f"    {k:<10}{v:>5}  {v / prov['total'] * 100:>5.1f}%")
    print(f"    그대로 인용해도 되는 비율 {prov['quotable'] * 100:.1f}%")
    for row in prov["bad"][:6]:
        print(f"      [{row['source']}] blk{row['block']} {row['status']}"
              f"  {row['span'][:56]}")

    dup = golden.audit_duplication(letters)
    print(f"\n  주장 밀도")
    print(f"    블록당 평균 {dup['claims_per_block']:.2f}"
          f"  최대 {dup['max_per_block']}")
    print(f"    분포 {dup['distribution']}")
    print(f"    거의 그대로 겹치는 쌍 {len(dup['duplicates'])}건"
          f"  ({dup['screen_hit_rate'] * 100:.1f}%)")
    for d in dup["duplicates"][:4]:
        print(f"      [{d['source']}] {d['a']}")
        print(f"                {d['b']}")
    if not dup["duplicates"]:
        print("    0 이라고 중복이 없는 게 아니다. 이건 글자가 겹치는 것만")
        print("    잡는다. 표현을 바꿔 두 번 뽑은 건 정답지로만 셀 수 있다.")

    from collections import Counter
    types = Counter(i.claim_type for L in letters for i in L.claims)
    modes = Counter(i.verification_mode for L in letters for i in L.claims)
    print(f"\n  주장 유형 ({len(types)}종, 합계 {sum(types.values())})")
    for k, v in types.most_common():
        print(f"    {k:<22}{v:>5}")
    if modes:
        print(f"\n  검증 경로 ({len(modes)}종, 합계 {sum(modes.values())})")
        for k, v in modes.most_common():
            print(f"    {k:<22}{v:>5}  {v / sum(modes.values()) * 100:>5.1f}%")
        if len(modes) < 3:
            print("    경로가 두 종류뿐이면 이 필드도 가르는 힘이 없다는 뜻이다.")
    print()
    return 0


def cmd_golden_score(args) -> int:
    print(f"\n■ 채점 ({args.split})")
    scored = False

    # 1. 뽑은 것 중 몇 개가 필요했나 — 이 정답지의 첫 번째 질문
    claims = golden.load("claims", args.split)
    u = golden.score_utility(claims)
    if u.get("n"):
        scored = True
        print(f"\n  주장 쓸모  n={u['n']}")
        for k, v in u["counts"].items():
            print(f"    {k:<18}{v:>4}  {v / u['n'] * 100:>5.1f}%")
        print(f"    남길 비율 {u['keep_rate'] * 100:.1f}%"
              f"  (핵심만 {u['core_rate'] * 100:.1f}%)")
        whole = golden.corpus_size(args.split)
        if whole:
            print(f"    → 전체 {whole}건에 이 비율을 적용하면 약 "
                  f"{round(whole * u['keep_rate'])}건이 필요했던 셈이다.")
    else:
        print("\n  주장 쓸모  utility 가 비어 있다. 여기부터 채워라.")

    # 2. 빠진 것은 없나
    r = golden.score_recall(golden.load("recall", args.split))
    if r.get("n"):
        scored = True
        print(f"\n  주장 재현율  블록 {r['n']}개")
        print(f"    뽑힘 {r['found']}  빠짐 {r['missed']}"
              f"  재현율 {r['recall'] * 100:.1f}%")
        print(f"    빠뜨린 블록 {r['blocks_with_gaps']}개")

    # 3. 분류가 맞나
    for layer in ("blocks", "claims", "pairs"):
        rows = golden.annotated(golden.load(layer, args.split))
        if not rows:
            continue
        scored = True
        s = golden.score_labels(rows, "heuristic")
        print(f"\n  {layer} 분류  정확도 {s.accuracy * 100:.1f}%"
              f"  ({s.correct}/{s.n})")
        for label, m in s.per_label().items():
            print(f"    {label:<18} 정밀 {m['precision'] * 100:>5.1f}%"
                  f"  재현 {m['recall'] * 100:>5.1f}%"
                  f"  F1 {m['f1'] * 100:>5.1f}%  n={m['n']}")
        if layer == "pairs":
            big = max((m["n"] for m in s.per_label().values()), default=0)
            if big and big / s.n > 0.7:
                print(f"    한 분류가 {big / s.n * 100:.0f}% 다. 전체 정확도는")
                print(f"    믿지 말고 분류별 F1 을 봐라.")

    # 4. 좁은 구간 전수 — 기계가 올린 쌍만 보면 재현율은 늘 100% 가 된다
    ex = golden.score_retrieval_exhaustive(golden.load("cluster", args.split))
    if ex.get("n"):
        scored = True
        print(f"\n  묶기 (주장 {ex['claims']}개 · 쌍 {ex['n']}개 전수)")
        print(f"    사람이 본 같은 사건 {ex['same_event']}쌍")
        print(f"    그중 기계도 묶은 것 {ex['found']}쌍"
              f"  재현율 {ex['recall'] * 100:.1f}%")
        print(f"    엉뚱하게 묶은 것 {ex['over_merged']}쌍"
              f"  정밀도 {ex['precision'] * 100:.1f}%")

    # 5. 찾기와 판정을 갈라서 — 앞 단계가 안 올린 쌍은 판정 몫이 아니다
    ret = golden.score_retrieval(golden.load("pairs", args.split))
    if ret.get("n"):
        scored = True
        print(f"\n  후보 찾기  실제 양성 {ret['n']}쌍 중"
              f" {ret['retrieved']}쌍 올림  재현율 {ret['recall'] * 100:.1f}%")
        for lost in ret["lost"][:5]:
            print(f"    놓침  {lost['label']:<14} 겹침 {lost['overlap']}"
                  f"  {lost['a']} ↔ {lost['b']}")
        if ret["lost"]:
            print("    이건 판정자가 못 고친다. 애초에 안 올라간 쌍이다.")

    t = golden.score_topics(golden.load("topics", args.split))
    if t.get("n"):
        scored = True
        print(f"\n  주제 품질  n={t['n']}")
        for c in golden.TOPIC_CRITERIA:
            print(f"    {c:<18}{t[c] * 100:>5.1f}%")
        print(f"    {'다섯 개 모두':<18}{t['all_pass'] * 100:>5.1f}%")

    if scored:
        print("\n  이 숫자는 휴리스틱의 성적이다. Luna·Terra 를 붙일 때")
        print("  같은 정답지로 재서 비교해야 비용을 올릴 근거가 생긴다.")
    print()
    return 0


def cmd_golden_import_canvas(args) -> int:
    """캔버스에서 붙인 라벨을 claims.jsonl 로 옮긴다."""
    from pathlib import Path
    import json

    root = Path.home() / ".cursor/projects/Users-byeong-uk-yu-Desktop-study-os/canvases"
    sidecar = Path(args.canvas) if args.canvas else root / "golden-calibration.canvas.data.json"
    if not sidecar.exists():
        print(f"캔버스 저장 파일이 없다: {sidecar}")
        print("먼저 golden-calibration.canvas.tsx 를 열고 라벨을 붙여라.")
        return 1

    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"읽기 실패: {e}")
        return 1

    labels = data.get("golden-labels") or {}
    if not labels:
        print("golden-labels 가 비어 있다. 캔버스에서 utility 를 선택했는지 확인하라.")
        return 1

    rows = golden.load("claims", args.split)
    by_id = {r["id"]: r for r in rows}
    updated = 0
    for cid, lab in labels.items():
        if cid not in by_id:
            continue
        u = (lab or {}).get("utility") or ""
        if u not in golden.CLAIM_UTILITY:
            continue
        by_id[cid]["utility"] = u
        note = (lab or {}).get("note") or ""
        if note:
            by_id[cid]["note"] = note
        updated += 1

    golden.save("claims", list(by_id.values()), args.split)
    print(f"\n■ 캔버스 → claims.jsonl  {updated}건 반영 ({args.split})")
    u = golden.score_utility(list(by_id.values()))
    if u.get("n"):
        print(f"  남길 비율 {u['keep_rate'] * 100:.1f}%"
              f"  (핵심 {u['core_rate'] * 100:.1f}%)  n={u['n']}")
    print("  python3 cli.py golden score 로 전체 채점을 본다.")
    print()
    return 0


def cmd_topics_sources(_args) -> int:
    data = feeds.load_sources()
    for layer, meta in (data.get("layers") or {}).items():
        rows = [f for f in data.get("feeds") or [] if f.get("layer") == layer]
        print(f"\n{layer} — {meta.get('role', '')}")
        print(f"  → {meta.get('maps_to', '')}")
        if meta.get("note"):
            print(f"  ※ {' '.join(str(meta['note']).split())}")
        for f in rows:
            kind = f.get("kind")
            where = f"@{f['handle']}" if kind == "youtube" else f.get("url", "")
            print(f"    {f['id']:<14} {f.get('name', ''):<12} {where[:52]}")
            if f.get("note"):
                print(f"                   {f['note']}")

    if mail := data.get("mail"):
        print("\n받은 뉴스레터 — study topics mail")
        print(f"  ※ {' '.join(str(mail.get('note', '')).split())}")
        for letter in mail.get("letters") or []:
            flag = "  [LLM 생성]" if letter.get("generated") else ""
            print(f"    {letter['id']:<14} {letter.get('name', ''):<22}"
                  f"{letter.get('from', '')}{flag}")
            if letter.get("note"):
                print(f"                   {' '.join(str(letter['note']).split())}")
        print(f"\n  얻는 것  {' '.join(str(mail.get('gives', '')).split())}")
        print(f"  치르는 값  {' '.join(str(mail.get('costs', '')).split())}")

    if data.get("manual"):
        print("\n자동화하지 않는 것 — 빠뜨린 게 아니라 뺀 것이다")
        for m in data["manual"]:
            print(f"\n    {m['name']}  [{m.get('layer', '')}]")
            print(f"      {' '.join(str(m.get('why', '')).split())}")
    print()
    return 0


def _promote_guidance(item: dict) -> int:
    """축을 고르기 전에 같은 사건이 축마다 다른 회차가 된다는 것을 보게 한다."""
    print(f"\n{item['id']}  {item['headline']}")
    print(f"  주장  {item['claim']}")
    if item.get("check_note"):
        print(f"  대조  {item['check_note']}")

    print("\n같은 주장도 축에 따라 다른 회차가 된다")
    for axis, prompt in topics.FRAMINGS.items():
        print(f"  {axis}축 {topics.AXES[axis]:<12} {prompt}")

    history = sessions.load()
    if len(history):
        wanted, repeated = sessions.suggest_axes(history)
        print()
        if repeated:
            names = ", ".join(f"{a}축" for a in repeated)
            print(f"  직전과 같은 축  {names}")
        print(f"  배치상 권장     {', '.join(f'{a}축' for a in wanted)}")
        print("  다만 뉴스가 다른 축을 가리키면 뉴스를 따른다. 균형은")
        print("  cli.py sessions balance 로 분기마다 돌아본다.")

    print(f"\n  cli.py topics promote {item['id']} --axis N --judgment 리스크 \\")
    print("    --question '...' --split '...' --split '...' \\")
    print("    --output '...' --ready partial")
    print("\n  질문·찬반·산출물은 대신 써주지 않는다. 정렬 기준 1·2번에 직접")
    print("  걸리는 것이고, 채우지 못하면 회차로 낼 준비가 안 된 것이다.")
    return 0


def cmd_topics_promote(args) -> int:
    inbox = topics.load_inbox()
    item = inbox.get(args.item_id)
    if not item:
        print(f"{args.item_id} 를 찾을 수 없습니다.")
        return 1

    if not item.get("check"):
        print(f"{item['id']} 은 아직 대조하지 않았습니다.")
        print(f"  cli.py topics check {item['id']} --result ...")
        return 1
    if item["check"] not in topics.PROMOTABLE:
        print(f"{item['id']} 은 '{item['check']}' 판정이라 승격 대상이 아닙니다.")
        return 1
    if item.get("topic"):
        print(f"{item['id']} 은 이미 {item['topic']} 로 승격됐습니다.")
        return 1

    if args.axis is None:
        return _promote_guidance(item)

    if len(args.split or []) < 2:
        print("--split 은 2개 이상이어야 합니다. 찬반이 안 갈리면 토론이 아닙니다.")
        return 1

    if item["check"] == "matches":
        print("  주의: 데이터와 일치한 주장입니다. 확인하는 자리가 되기 쉽습니다.")
        print("  '왜 그런가' 로 한 단계 판 질문인지 확인하세요.\n")

    try:
        series = [topics.parse_series(s) for s in (args.series or [])]
    except ValueError as exc:
        print(exc)
        return 1

    backlog = topics.load()
    topic = topics.promote(
        item, topic_id=topics.next_topic_id(backlog), axis=args.axis,
        judgment=args.judgment, question=args.question, splits=args.split,
        output=args.output, ready=args.ready, series=series,
        title=args.title, prep=args.prep,
    )

    issues = topics._check_topic(topic)
    if issues:
        print(f"{len(issues)}건이 규칙에 걸려 저장하지 않았습니다:\n")
        for i in issues:
            print(f"  {i.message}")
        return 1

    backlog.topics.append(topic)
    topics.save(backlog)

    item["status"] = "promoted"
    item["topic"] = topic["id"]
    topics.save_inbox(inbox)

    print(f"{topic['id']} 생성  ({topic['axis']}축 {topics.AXES[topic['axis']]}"
          f" · {topic['judgment']})")
    print(f"  {topic['title']}")
    print(f"  질문  {topic['question']}")
    print("\n  넘어온 근거")
    for w in topic["why_now"]:
        print(f"    · {w['text']}")
        print(f"      {w['source']}")
    if topic["data"]["series"]:
        print("\n  붙은 시리즈")
        for s in topic["data"]["series"]:
            detail = s.get("field") or ", ".join(s.get("items") or [])
            print(f"    {s['source']}/{s['dataset']}" + (f"  {detail}" if detail else ""))
    elif topic["data"]["note"]:
        print(f"\n  data.series 가 비어 있다. 확인 계획: {topic['data']['note']}")
        print("  --series 로 채우면 준비 시간이 줄어든다.")
    return 0


def cmd_topics_inbox(args) -> int:
    inbox = topics.load_inbox()
    items = inbox.pending() if args.pending else inbox.items
    if not items:
        print("포착함이 비어 있습니다.  cli.py topics capture --help")
        return 0
    for i in items:
        mark = {"matches": "일치", "contradicts": "어긋남",
                "cause_open": "인과열림", "unverifiable": "확인불가"
                }.get(i.get("check"), "미대조")
        who = f" ({i['by']})" if i.get("by") else ""
        print(f"  {i['id']:<6} [{mark:<4}] {i['headline']}{who}")
        print(f"          주장  {i['claim']}")
    print(f"\n{len(items)}건 · 미대조 {len(inbox.pending())}건")
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

    tp = sub.add_parser("topics", help="주제 후보 풀 — 규칙은 docs/TOPICS.md")
    tsub = tp.add_subparsers(dest="topics_command", required=True)

    sp = tsub.add_parser("list", help="후보 목록")
    sp.add_argument("--axis", type=int, choices=[1, 2, 3, 4, 5, 6])
    sp.add_argument("--ready", choices=["full", "partial", "external"])
    sp.add_argument("--status", choices=["candidate", "selected", "done", "dropped"])
    sp.add_argument("--books", action="store_true", help="독서 트랙만")
    sp.add_argument("--no-books", action="store_true", help="독서 제외")
    sp.set_defaults(func=cmd_topics_list)

    sp = tsub.add_parser("show", help="후보 상세")
    sp.add_argument("topic_id")
    sp.set_defaults(func=cmd_topics_show)

    sp = tsub.add_parser("validate", help="형식 계약 검사")
    sp.set_defaults(func=cmd_topics_validate)

    sp = tsub.add_parser("capture", help="뉴스·제안을 포착함에 던져 넣는다")
    sp.add_argument("--headline", required=True, help="본 것의 제목")
    sp.add_argument("--claim", required=True,
                    help="그것이 하는 검증 가능한 주장 하나. 분위기 말고 주장")
    sp.add_argument("--source", required=True, help="어디서 봤나")
    sp.add_argument("--verify", help="무엇으로 확인할 수 있나. 없으면 주제가 아니다")
    sp.add_argument("--origin", default="뉴스", help="뉴스 / 참가자 제안 / 워크시트")
    sp.add_argument("--by", help="참가자 제안이면 누가")
    sp.add_argument("--seen", help="본 날짜 (기본: 오늘)")
    sp.set_defaults(func=cmd_topics_capture)

    sp = tsub.add_parser("check", help="포착한 주장을 지표와 대조한 결과 기록")
    sp.add_argument("item_id")
    sp.add_argument("--result", required=True,
                    choices=["matches", "contradicts", "cause_open", "unverifiable"])
    sp.add_argument("--note", help="대조 근거 — 어떤 데이터가 무엇을 말했나")
    sp.set_defaults(func=cmd_topics_check)

    sp = tsub.add_parser("promote", help="포착 항목을 정식 후보로 올린다")
    sp.add_argument("item_id")
    sp.add_argument("--axis", type=int, choices=[1, 2, 3, 4, 5, 6],
                    help="빼면 축별 관점과 배치 권장을 보여준다")
    sp.add_argument("--judgment", choices=sorted(topics.JUDGMENTS))
    sp.add_argument("--question", help="하나의 질문. 예·아니오로 끝나지 않을 것")
    sp.add_argument("--split", action="append",
                    help="찬반이 갈리는 지점. 2번 이상 지정")
    sp.add_argument("--output", help="참가자가 워크시트에 남기게 될 것")
    sp.add_argument("--ready", choices=["full", "partial", "external"])
    sp.add_argument("--series", action="append", metavar="source:dataset[:항목]",
                    help="대조에 쓴 시리즈. 여러 번 지정 가능")
    sp.add_argument("--title")
    sp.add_argument("--prep", type=int, help="예상 준비 시간")
    sp.set_defaults(func=cmd_topics_promote)

    sp = tsub.add_parser("feed", help="소스 피드를 훑는다 — 후보가 아니라 재료")
    sp.add_argument("--layer", choices=["통념", "견해", "1차"])
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--only", action="append", help="특정 피드만. 여러 번 지정 가능")
    sp.add_argument("--per-feed", type=int, default=8)
    sp.add_argument("--min-feeds", type=int, default=2,
                    help="반복 판정에 필요한 최소 채널 수")
    sp.add_argument("--windows", action="store_true",
                    help="누적 저장소에서 2·7·30일 창을 비교한다")
    sp.add_argument("--no-record", action="store_true",
                    help="누적 저장소에 쓰지 않는다")
    sp.set_defaults(func=cmd_topics_feed)

    sp = tsub.add_parser("mail", help="받은 뉴스레터에서 대조할 주장을 뽑는다")
    sp.add_argument("--dir", default="~/Downloads",
                    help="내려받은 .eml 이 있는 폴더")
    sp.add_argument("--day", help="YYYY-MM-DD. 없으면 가장 최근 날")
    sp.add_argument("--claims", type=int, nargs="?", const=8, default=0,
                    metavar="N", help="매체별 주장 N 개까지 본문 출력")
    sp.add_argument("--llm", action="store_true",
                    help="블록을 모델로 분류하고 주장을 다시 뽑는다")
    sp.add_argument("--blocks", action="store_true", help="블록 분류 결과를 본다")
    sp.add_argument("--events", action="store_true",
                    help="사건 묶음과 스터디 후보")
    sp.add_argument("--cost", action="store_true",
                    help="단계별 호출 수·토큰·비용")
    sp.add_argument("--refresh", action="store_true", help="모델 응답 캐시 무시")
    sp.set_defaults(func=cmd_topics_mail)

    gp = sub.add_parser("golden", help="정답지 — 얼마나 잘하고 있나 재는 자")
    gsub = gp.add_subparsers(dest="golden_cmd", required=True)

    sp = gsub.add_parser("seed", help="라벨 붙일 표본을 뽑아 둔다")
    sp.add_argument("layer", choices=list(golden.LAYERS) + ["all"])
    sp.add_argument("--dir", default="~/Downloads")
    sp.add_argument("--split", default="dev", choices=list(golden.SPLITS))
    sp.add_argument("--n", type=int, default=0, help="0 이면 계층별 권장 수량")
    sp.add_argument("--llm", action="store_true")
    sp.set_defaults(func=cmd_golden_seed)

    sp = gsub.add_parser("status", help="어디까지 라벨을 붙였나")
    sp.add_argument("--split", default="dev", choices=list(golden.SPLITS))
    sp.set_defaults(func=cmd_golden_status)

    sp = gsub.add_parser("audit", help="정답지 없이 되는 점검")
    sp.add_argument("--dir", default="~/Downloads")
    sp.add_argument("--llm", action="store_true")
    sp.set_defaults(func=cmd_golden_audit)

    sp = gsub.add_parser("score", help="라벨과 예측을 맞춰 본다")
    sp.add_argument("--split", default="dev", choices=list(golden.SPLITS))
    sp.set_defaults(func=cmd_golden_score)

    sp = gsub.add_parser("import-canvas", help="캔버스 라벨을 claims.jsonl 에 반영")
    sp.add_argument("--split", default="dev", choices=list(golden.SPLITS))
    sp.add_argument("--canvas", default="", help="캔버스 .canvas.data.json 경로")
    sp.set_defaults(func=cmd_golden_import_canvas)

    sp = tsub.add_parser("sources", help="소스 목록과 각 층의 역할")
    sp.set_defaults(func=cmd_topics_sources)

    sp = tsub.add_parser("inbox", help="포착함 목록")
    sp.add_argument("--pending", action="store_true", help="아직 대조 안 한 것만")
    sp.set_defaults(func=cmd_topics_inbox)

    ss = sub.add_parser("sessions", help="회차 이력 — 무엇을 했나")
    ssub = ss.add_subparsers(dest="sessions_command", required=True)

    sp = ssub.add_parser("list", help="회차 목록")
    sp.set_defaults(func=cmd_sessions_list)

    sp = ssub.add_parser("show", help="회차 상세")
    sp.add_argument("session_id")
    sp.set_defaults(func=cmd_sessions_show)

    sp = ssub.add_parser("validate", help="기록 형식 검사")
    sp.set_defaults(func=cmd_sessions_validate)

    sp = ssub.add_parser("open", help="미해결 질문 모음 — 후보 유입 경로")
    sp.set_defaults(func=cmd_sessions_open)

    sp = ssub.add_parser("balance", help="축 배분 진단과 다음 회차 권장 축")
    sp.set_defaults(func=cmd_sessions_balance)

    sp = ssub.add_parser("similar", help="비슷한 과거 회차를 찾는다")
    sp.add_argument("text", nargs="?", default="", help="검사할 문장")
    sp.add_argument("--topic", help="후보 id 로 검사 (예: T-002)")
    sp.set_defaults(func=cmd_sessions_similar)

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
