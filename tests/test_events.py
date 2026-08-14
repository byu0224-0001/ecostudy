from core import events as ev, newsletters as nl


def _item(text, mode="direct_data", headline=""):
    return nl.Item(text=text, kind="claim", section="", block=0,
                   headline=headline, claim_type="numeric_fact",
                   verification_mode=mode, source_span=text,
                   provenance="exact")


def _letter(name, day, items):
    from datetime import datetime
    return nl.Newsletter(sender=f"a@{name}.com", name=name, layer="통념",
                         subject="", date=datetime.fromisoformat(day),
                         items=items)


def test_single_source_event_survives():
    """한 곳만 다뤄도 사건이다.

    한국은행 발표 하나로도 스터디는 열린다. 매체 수를 관문으로 쓰면
    그런 게 통째로 사라진다.
    """
    letters = [_letter("ECOS레터", "2026-08-13", [
        _item("한국은행이 기준금리를 2.50%로 동결했다"),
        _item("한국은행은 성장률 전망을 1.4%로 낮췄다"),
    ])]
    evts = ev.cluster(letters)
    assert evts
    assert any(e.source_count == 1 for e in evts)


def test_source_count_only_moves_confidence():
    solo = ev.Event(event_id="E-1", label="x", day="d",
                    claims=[{}, {}], sources=["A"], evidence=2.0)
    both = ev.Event(event_id="E-2", label="x", day="d",
                    claims=[{}, {}], sources=["A", "B"], evidence=2.0)
    assert solo.confidence < both.confidence


def test_event_can_span_days():
    """월요일 발언과 수요일 급등은 한 줄기다."""
    letters = [
        _letter("A", "2026-08-11", [_item("일본은행 총재가 엔화 약세를 언급했다")]),
        _letter("B", "2026-08-13", [_item("일본은행 발언 이후 엔화가 급등했다")]),
    ]
    evts = ev.cluster(letters)
    assert evts
    assert any(e.spans_days for e in evts)


def test_far_apart_days_stay_apart():
    letters = [
        _letter("A", "2026-07-01", [_item("일본은행 총재가 엔화 약세를 언급했다")]),
        _letter("B", "2026-08-13", [_item("일본은행 발언 이후 엔화가 급등했다")]),
    ]
    assert ev.cluster(letters) == []


def test_entity_ending_in_da_is_not_a_predicate():
    """혼다·마쓰다·소다는 `다` 로 끝나지만 개체명이다.

    처음에는 `다` 로 끝나면 전부 서술어로 봤다. 그러면 이런 이름이 통째로
    사라진다. 활용 어미 음절까지 봐야 갈린다.
    """
    for word in ("혼다", "마쓰다", "소다", "현다"):
        assert not ev.is_predicate(word)
    for word in ("상승했다", "기록했다", "예정이다", "높아진다", "밝혔다"):
        assert ev.is_predicate(word)


def test_predicates_never_seed_or_label():
    """서술어는 사건의 이름이 될 수 없다.

    빈도로는 못 거른다. 실측에서 `상승했다` 4.0%, `반도체` 3.5% 였다.
    문턱을 낮추면 정작 쓸모 있는 쪽이 먼저 죽는다.
    """
    letters = [_letter("A", "2026-08-13", [
        _item("코스닥이 사상 최고치를 기록했다"),
        _item("삼성전자가 분기 최대 매출을 기록했다"),
        _item("현대차가 판매 대수 신기록을 기록했다"),
        _item("환율이 연저점을 기록했다"),
    ])]
    for e in ev.cluster(letters):
        for token in e.label.split(" · "):
            assert not ev.is_predicate(token)


def test_entity_survives_even_when_common():
    """`코스닥` 은 자주 나와도 사건의 이름이 될 수 있다."""
    letters = [_letter("A", "2026-08-13", [
        _item("코스닥 승강제가 도입된다"),
        _item("코스닥 승강제 시행일이 확정됐다"),
    ])]
    evts = ev.cluster(letters)
    assert evts
    assert any("코스닥" in e.label or "승강제" in e.label for e in evts)


def test_not_researchable_claims_excluded():
    letters = [_letter("A", "2026-08-13", [
        _item("지금 구독하면 할인됩니다", mode="not_researchable"),
        _item("당장 신청하면 할인됩니다", mode="not_researchable"),
    ])]
    assert ev.cluster(letters) == []


def test_evidence_weights_by_verification_path():
    strong = ev.Event(event_id="E-1", label="x", day="d",
                      claims=[{}], sources=["A"],
                      evidence=ev.MODE_WEIGHT["direct_data"])
    soft = ev.Event(event_id="E-2", label="x", day="d",
                    claims=[{}], sources=["A"],
                    evidence=ev.MODE_WEIGHT["interpretive"])
    assert strong.confidence > soft.confidence


def _two_topics():
    return [
        _letter("A", "2026-08-13", [
            _item("코스닥 지수가 6.97% 상승 마감했다"),
            _item("코스닥 기관 순매수가 1조원을 넘었다"),
        ]),
        _letter("B", "2026-08-13", [
            _item("엔비디아가 데이터센터 투자를 확대한다"),
            _item("엔비디아 데이터센터 매출이 급증했다"),
        ]),
    ]


def test_pair_sample_offers_three_way_labels():
    pairs = ev.pair_sample(ev.cluster(_two_topics()))
    assert pairs
    for p in pairs:
        assert p["label"] is None            # 사람이 채운다
        assert p["heuristic"] in ("same_event", "related_theme", "unrelated")


def test_pair_sample_marks_what_retrieval_would_surface():
    """찾기가 안 올린 쌍도 표본에 넣어야 놓친 것을 셀 수 있다.

    올린 쌍만 라벨링하면 찾기 재현율은 정의상 100% 가 된다.
    """
    pairs = ev.pair_sample(ev.cluster(_two_topics()))
    for p in pairs:
        assert p["retrieved"] == (p["token_overlap"] >= ev.RELATED)


def test_retrieval_threshold_is_not_the_adjudicator():
    """찾기 문턱과 판정은 다른 일이다.

    지금은 판정 자리를 겹침으로 임시로 채워 뒀지만, 둘을 한 함수에 두면
    나중에 Luna 를 끼울 자리가 없어진다.
    """
    borderline = {"token_overlap": ev.RELATED}
    assert ev.retrieved(borderline)
    assert ev.guess(borderline) == "related_theme"

    below = {"token_overlap": ev.RELATED - 0.01}
    assert not ev.retrieved(below)
    assert ev.guess(below) == "unrelated"


def test_study_candidate_flags_single_source():
    letters = [_letter("A", "2026-08-13", [
        _item("한국은행이 기준금리를 2.50%로 동결했다"),
        _item("한국은행은 성장률 전망을 1.4%로 낮췄다"),
    ])]
    cands = ev.study_candidates(ev.cluster(letters))
    assert cands
    assert cands[0]["single_source"]
