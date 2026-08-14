"""회차 이력.

검사하는 것은 코드 품질이 아니라 모임의 형식이다. 특히 두 가지가
중요하다. 회차 기록에도 결론을 적지 않는다는 것, 그리고 아무도 갈리지
않은 회차를 잘된 것으로 두지 않는다는 것.
"""

from __future__ import annotations

import datetime as dt

import yaml

from core import sessions


def _session(**over) -> dict:
    base = {
        "id": "S-001",
        "date": dt.date(2026, 8, 12),
        "axis": 1,
        "judgment": "리스크",
        "title": "엔캐리와 미국 장기금리",
        "question": "엔캐리 청산 위험이 실제로 얼마나 큰가",
        "settled": [{"text": "레버리지펀드 21만 계약", "source": "CFTC TFF"}],
        "claims_checked": [
            {"claim": "엔화 급변의 배경은 레버리지 펀드의 대규모 청산이다",
             "result": "contradicts"},
        ],
        "split": [
            {"point": "규모를 어떻게 볼 것인가",
             "positions": ["제한적이다", "과소평가다"]},
            {"point": "FIMA 잔고 0 을 어떻게 읽을 것인가",
             "positions": ["평시에 쓸 유인이 없는 백스톱이다",
                           "언급 자체가 미국 장기금리 방어 의도를 드러낸다"]},
        ],
        "open": [{"question": "선물 밖 익스포저는 어떻게 재나",
                  "why": "데이터 접근 방법을 찾지 못했다"}],
    }
    base.update(over)
    return base


def _history(*raws) -> sessions.History:
    return sessions.History(sessions=[sessions.Session(raw=r) for r in raws])


def _messages(hist) -> str:
    return " ".join(i.message for i in sessions.validate(hist))


def test_clean_record_passes():
    assert sessions.validate(_history(_session())) == []


def test_conclusion_field_rejected_in_history_too():
    """후보에서 결론을 막아놓고 회차 기록에서 허용하면 우회로가 된다."""
    assert "결론" in _messages(_history(_session(결론="청산 위험은 제한적")))
    assert "conclusion" in _messages(_history(_session(conclusion="limited")))


def test_session_without_split_is_flagged():
    """깨끗하게 합의된 회차는 잘된 게 아니라 의심스러운 것이다."""
    assert "split" in _messages(_history(_session(split=[])))


def test_book_session_may_have_no_split():
    raw = _session(book=True, split=[])
    assert "split" not in _messages(_history(raw))


def test_session_without_open_questions_is_flagged():
    assert "open" in _messages(_history(_session(open=[])))


def test_open_question_needs_a_reason():
    raw = _session(open=[{"question": "무엇이 원인인가"}])
    assert "why" in _messages(_history(raw))


def test_settled_fact_needs_a_source():
    raw = _session(settled=[{"text": "FIMA 잔고 0"}])
    assert "source" in _messages(_history(raw))


def test_same_axis_twice_in_a_row_is_flagged():
    a = _session(id="S-001", date=dt.date(2026, 7, 1), axis=2)
    b = _session(id="S-002", date=dt.date(2026, 7, 15), axis=2)
    assert "연속" in _messages(_history(a, b))

    b["axis"] = 3
    assert "연속" not in _messages(_history(a, b))


def test_book_session_does_not_break_the_axis_streak():
    a = _session(id="S-001", date=dt.date(2026, 7, 1), axis=2)
    book = _session(id="S-002", date=dt.date(2026, 7, 8), axis=2, book=True, split=[])
    c = _session(id="S-003", date=dt.date(2026, 7, 15), axis=4)
    assert "연속" not in _messages(_history(a, book, c))


def test_duplicate_ids_caught():
    assert "중복" in _messages(_history(_session(), _session()))


# --- 배치 -------------------------------------------------------------------

TODAY = dt.date(2026, 8, 20)


def test_next_axis_excludes_the_previous_one():
    hist = _history(_session(axis=1, date=dt.date(2026, 8, 12)))
    wanted, repeated = sessions.suggest_axes(hist, today=TODAY)
    assert repeated == [1]
    assert 1 not in wanted


def test_personal_axes_are_urgent_when_missing():
    """4·5·6축은 데이터가 적다는 이유로 계속 밀리기 쉬워 먼저 올린다."""
    hist = _history(_session(axis=1, date=dt.date(2026, 8, 12)))
    wanted, _ = sessions.suggest_axes(hist, today=TODAY)
    assert set(wanted) == {4, 5, 6}


def test_anchor_axis_becomes_urgent_once_personal_axes_are_covered():
    hist = _history(
        _session(id="S-001", axis=4, date=dt.date(2026, 6, 10)),
        _session(id="S-002", axis=5, date=dt.date(2026, 7, 10)),
        _session(id="S-003", axis=6, date=dt.date(2026, 8, 12)),
    )
    wanted, repeated = sessions.suggest_axes(hist, today=TODAY)
    assert repeated == [6]
    assert wanted == [1]


def test_placement_reports_missing_personal_axes():
    hist = _history(_session(axis=1, date=dt.date(2026, 8, 12)))
    rules = {r.name: r for r in sessions.placement(hist, today=TODAY)}
    assert rules["4·5·6축 분기 1회 이상"].ok is False
    assert rules["1축 분기 1회 이상"].ok is True


def test_coverage_only_counts_the_trailing_quarter():
    hist = _history(
        _session(id="S-001", axis=2, date=dt.date(2025, 1, 1)),
        _session(id="S-002", axis=3, date=dt.date(2026, 8, 12)),
    )
    counts = sessions.coverage(hist, today=TODAY)
    assert counts[2] == 0
    assert counts[3] == 1


# --- 중복 -------------------------------------------------------------------

def test_similarity_uses_containment_not_jaccard():
    """긴 회차 기록이 짧은 주장에 벌점을 주면 안 된다. 첫 구현이 이걸
    틀려서 엔캐리 회차와 엔화 개입 주장을 못 잡았다."""
    hist = _history(_session())
    hits = sessions.similar(
        hist, "엔화 반등은 미국의 개입 때문이다 엔캐리 청산 레버리지펀드 포지션")
    assert hits and hits[0].session.id == "S-001"
    assert hits[0].score >= 0.30


def test_match_reports_which_words_overlapped():
    """숫자만으로는 흔한 말이 겹친 건지 고유한 말이 겹친 건지 알 수 없다."""
    hist = _history(_session())
    hit = sessions.similar(hist, "엔캐리 청산 규모가 서사에 걸맞나")[0]
    assert "엔캐리" in hit.shared
    assert "청산" in hit.shared


def test_headline_noise_still_finds_the_session():
    """실제로 잡은 문제. 기사 제목의 '구출·작전·진짜' 가 분모만 키웠다."""
    hist = _history(_session())
    text = ("미국의 엔화 구출 작전 / 미국이 엔화 방어에 나선 진짜 이유 "
            "최근 엔화 반등은 미국의 개입 때문이다")
    hits = sessions.similar(hist, text)
    assert hits, "제목 잡음이 섞여도 엔캐리 회차는 떠야 한다"
    assert "엔화" in hits[0].shared


def test_particles_are_stripped_before_matching():
    """'엔캐리와' 와 '엔캐리' 가 다른 토큰이면 중복 검사가 통째로 헛돈다."""
    assert sessions._stem("엔캐리와") == "엔캐리"
    assert sessions._stem("미국의") == "미국"
    assert sessions._stem("대만으로") == "대만"
    assert sessions._stem("코스피만") == "코스피"


def test_stemming_never_eats_a_short_word():
    for word in ("물가", "제도", "종이", "대만", "국가", "경기"):
        assert sessions._stem(word) == word


def test_unrelated_claim_does_not_match():
    hist = _history(_session())
    assert sessions.similar(hist, "외국인 자금이 대만으로 이동해 코스피가 부진했다") == []


def test_very_short_query_never_matches():
    hist = _history(_session())
    assert sessions.similar(hist, "엔화") == []


def test_similarity_reads_split_and_open_not_just_the_title():
    hist = _history(_session())
    hits = sessions.similar(hist, "선물 익스포저 규모 과소평가 재나 어떻게")
    assert hits, "갈린 지점과 미해결 질문도 중복 판단 대상이다"


# --- 파일 -------------------------------------------------------------------

def test_round_trip_through_disk(tmp_path):
    sessions.save(sessions.Session(raw=_session()), directory=tmp_path)
    loaded = sessions.load(tmp_path)
    assert len(loaded) == 1
    assert loaded.get("s-001").axis == 1
    assert loaded.get("S-001").date == dt.date(2026, 8, 12)


def test_next_id_continues_the_sequence(tmp_path):
    hist = _history(_session(id="S-001"), _session(id="S-007"))
    assert hist.next_id() == "S-008"
    assert sessions.History().next_id() == "S-001"


def test_skew_reports_but_never_blocks():
    """편중 회고는 아무것도 막지 않는다. 막는 순간 뉴스를 이긴다."""
    hist = _history(
        _session(id="S-001", axis=1, date=dt.date(2026, 8, 3)),
        _session(id="S-002", axis=1, date=dt.date(2026, 8, 10)),
        _session(id="S-003", axis=1, date=dt.date(2026, 8, 17)),
    )
    s = sessions.skew(hist, today=TODAY)
    assert s.lopsided and s.dominant == 1
    assert set(s.starved) == {2, 3, 4, 5, 6}

    # 세 번 연속 1축이었어도 1축 후보를 올리는 길은 여전히 열려 있다.
    _, repeated = sessions.suggest_axes(hist, today=TODAY)
    assert repeated == [1], "직전 축은 알려주되 그뿐이다"


def test_even_coverage_is_not_lopsided():
    hist = _history(
        _session(id="S-001", axis=1, date=dt.date(2026, 8, 3)),
        _session(id="S-002", axis=2, date=dt.date(2026, 8, 10)),
        _session(id="S-003", axis=3, date=dt.date(2026, 8, 17)),
    )
    s = sessions.skew(hist, today=TODAY)
    assert not s.lopsided
    assert set(s.starved) == {4, 5, 6}


def test_skew_on_empty_history_is_quiet():
    s = sessions.skew(_history(), today=TODAY)
    assert not s.lopsided and s.total == 0


def test_shipped_records_are_valid():
    hist = sessions.load()
    assert sessions.validate(hist) == []
    for s in hist.sessions:
        assert s.open_questions, f"{s.id}: 미해결이 없으면 후보 유입이 끊긴다"


def test_shipped_records_carry_no_conclusion():
    for path in sessions.DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert not (set(raw) & sessions.BANNED_FIELDS), path.name
