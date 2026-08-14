"""뉴스레터 파싱 — 무엇을 뽑고 무엇을 버리는가."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core import newsletters as nl

KST = timezone(timedelta(hours=9))


def letter(name, day, texts, *, generated=False, sections=None):
    items = [nl.Item(text=t, kind="claim", section="") for t in texts]
    for item in items:
        if sections:
            item.section = sections
    return nl.Newsletter(
        sender=f"{name} <x@y.com>", name=name, layer="통념",
        subject="", date=datetime(2026, 8, day, 6, tzinfo=KST),
        generated=generated, items=items,
    )


def test_keeps_only_falsifiable_lines():
    lines = [
        "코스닥지수가 이달 들어 18.72% 올라 상승률 1위를 기록했습니다",
        "시장의 투자 심리가 전반적으로 개선되는 모습을 보이고 있습니다",
    ]
    got = [i.text for i in nl.extract(lines)]
    assert len(got) == 1, "숫자 없는 서술은 대조할 수 없으므로 버린다"
    assert "18.72%" in got[0]

def test_drops_boilerplate_even_with_numbers():
    """광고와 수신거부에도 숫자가 붙어 온다."""
    lines = [
        "🎁 8월 11일 퀴즈 당첨자 상품은 5영업일 안에 발송됩니다",
        "1만 명이 보는 실시간 뉴스 오픈채팅방 (입장코드 bytenews)",
        "기관은 이달 들어 코스닥시장에서 1조 2,240억 원을 순매수했는데요",
    ]
    got = [i.text for i in nl.extract(lines)]
    assert len(got) == 1
    assert "순매수" in got[0]

def test_separates_schedule_from_claim():
    lines = [
        "📆 일정",
        "미국의 7월 생산자물가지수( PPI )가 발표돼요.",
        "📊 증시",
        "12일 코스피는 3.68% 상승해 장을 마쳤어요.",
    ]
    items = nl.extract(lines)
    kinds = {i.kind: i.text for i in items}
    assert "PPI" in kinds["schedule"]
    assert "3.68%" in kinds["claim"]

def test_reads_bare_text_headings():
    """이모지로 시작하는 제목만 보다가 DAILY_BYTE 소제목을 놓쳤다."""
    assert nl._section_of("코스닥, 어떻게 이렇게 올랐지") is not None
    assert nl._section_of("🔎 핵심만 콕콕") == "핵심만 콕콕"
    assert nl._section_of("기관은 코스닥을 순매수했어요") is None, \
        "평서문은 제목이 아니다"

def test_deduplicates_repeated_lines():
    lines = ["12일 코스피는 외국인 매수에 힘입어 3.68% 상승해 장을 마쳤어요"] * 3
    assert len(nl.extract(lines)) == 1


def test_flags_machine_written_sources():
    """DeepSearch 는 LLM 이 쓰고 스스로 정확성을 보장하지 않는다고 밝힌다."""
    assert nl._profile("noreply@deepsearch.com")["generated"] is True
    assert nl._profile("moneyletter@uppity.co.kr").get("generated") is None

def test_falls_back_to_sender_name():
    got = nl._profile("어디선가 <a@unknown.io>")
    assert got["layer"] == "통념", "모르는 곳도 1차로 올리지 않는다"

def test_no_source_is_ever_primary():
    assert all(p["layer"] != "1차" for p in nl.PROFILES.values()), \
        "뉴스레터는 어떤 경우에도 1차 소스가 아니다"


def test_needs_multiple_sources():
    letters = [
        letter("A", 12, ["인텔이 12조 원을 투자한다고 밝혔어요"]),
        letter("B", 12, ["삼성이 5조 원 규모 계약을 맺었어요"]),
    ]
    got = dict(nl.signals(letters, day="2026-08-12"))
    assert "인텔" not in got, "한 곳만 말하면 그 매체의 관심사다"

def test_surfaces_words_two_sources_share():
    letters = [
        letter("A", 12, ["인텔이 12조 원을 투자한다고 밝혔어요"]),
        letter("B", 12, ["인텔의 파운드리 수주가 3배 늘었어요"]),
    ]
    got = dict(nl.signals(letters, day="2026-08-12"))
    assert "인텔" in got

def test_strips_particles_before_counting():
    """가격·가격이·가격을이 서로 다른 단어로 세어지면 상위권을 독식한다."""
    letters = [
        letter("A", 12, ["금 가격이 10% 올랐어요"]),
        letter("B", 12, ["금 가격을 3개월간 지켜봤어요"]),
    ]
    got = dict(nl.signals(letters, day="2026-08-12"))
    assert "가격" in got
    assert "가격이" not in got and "가격을" not in got

def test_drops_words_that_appear_every_day():
    """매일 나오는 말은 그날의 이야기가 아니다."""
    everyday = "정부가 관련 대책을 5조 원 규모로 내놨어요"
    letters = []
    for day in (10, 11, 12):
        letters += [letter("A", day, [everyday]),
                    letter("B", day, [everyday])]
    letters += [letter("A", 12, ["인텔이 12조 원을 투자해요"]),
                letter("B", 12, ["인텔의 수주가 3배 늘었어요"])]

    got = dict(nl.signals(letters, day="2026-08-12"))
    assert "인텔" in got
    assert "정부" not in got, "사흘 내내 나온 말은 배경으로 빠져야 한다"

def test_keeps_everything_when_history_is_thin():
    """이틀치로 배경을 판정하면 그날 유일한 주제가 지워진다."""
    letters = [letter("A", 12, ["인텔이 12조 원을 투자해요"]),
               letter("B", 12, ["인텔의 수주가 3배 늘었어요"])]
    assert "인텔" in dict(nl.signals(letters, day="2026-08-12"))

def test_ranks_rare_words_above_common_ones():
    """환율은 절반의 날에만 나와 배경까지는 아니고, 인텔은 오늘만 나왔다."""
    letters = [letter("A", 10, ["환율이 3% 올라 1400원을 넘었어요"]),
               letter("B", 10, ["환율이 5% 움직여 1400원대가 됐어요"]),
               letter("A", 11, ["금값이 3% 올라 사상 최고를 기록했어요"]),
               letter("B", 11, ["금값이 5% 뛰어 최고가를 새로 썼어요"]),
               letter("A", 13, ["유가가 3% 내려 60달러를 밑돌았어요"]),
               letter("B", 13, ["유가가 5% 빠져 60달러 아래로 갔어요"]),
               letter("A", 12, ["환율이 3% 오르고 인텔이 12조 원을 투자해요"]),
               letter("B", 12, ["환율이 5% 뛰고 인텔 수주가 3배 늘었어요"])]

    order = [word for word, _ in nl.signals(letters, day="2026-08-12")]
    assert "인텔" in order and "환율" in order
    assert order.index("인텔") < order.index("환율"), \
        "같은 매체 수라면 평소 안 나오던 말이 먼저다"
