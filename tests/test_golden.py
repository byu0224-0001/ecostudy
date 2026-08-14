import pytest

from core import golden, newsletters as nl


def _item(text, block=0, span="", provenance="exact", mode="direct_data"):
    return nl.Item(text=text, kind="claim", section="", block=block,
                   source_span=span, provenance=provenance,
                   verification_mode=mode)


def _letter(items, name="X"):
    return nl.Newsletter(sender="a@b.c", name=name, layer="통념", subject="",
                         date=None, items=items)


# --- 채점 ------------------------------------------------------------------

def test_blank_labels_are_left_out():
    """확신 없는 줄을 억지로 채우지 않게 하려면 빈 줄이 빠져야 한다."""
    rows = [{"label": "a"}, {"label": None}, {"label": ""}]
    assert len(golden.annotated(rows)) == 1


def test_accuracy_counts_only_labelled_rows():
    rows = [
        {"label": "same_event", "heuristic": "same_event"},
        {"label": "unrelated", "heuristic": "related_theme"},
        {"label": None, "heuristic": "same_event"},
    ]
    s = golden.score_labels(rows)
    assert s.n == 2
    assert s.accuracy == 0.5


def test_per_label_exposes_what_accuracy_hides():
    """전부 흔한 라벨로 찍어도 정확도는 높게 나온다.

    사건 쌍은 unrelated 가 대부분이라 이 함정이 실제로 있다. 라벨별로
    재현율을 봐야 한 종류만 맞히고 있다는 걸 알 수 있다.
    """
    rows = [{"label": "unrelated", "heuristic": "unrelated"}] * 9
    rows.append({"label": "same_event", "heuristic": "unrelated"})
    s = golden.score_labels(rows)

    assert s.accuracy == 0.9
    assert s.per_label()["same_event"]["recall"] == 0.0


def test_progress_asks_each_layer_the_right_question():
    """계층마다 '채웠다'의 뜻이 다르다.

    전부 label 로 세면 주장 쓸모와 빠짐 계층은 다 채워도 0% 로 남는다.
    """
    assert golden.filled("claims", [{"utility": "keep_core", "label": None}])
    assert not golden.filled("claims", [{"utility": None, "label": "x"}])
    assert golden.filled("recall", [{"reviewed": True}])
    assert not golden.filled("recall", [{"reviewed": False}])
    assert golden.filled("pairs", [{"label": "same_event"}])


def test_utility_answers_how_many_were_needed():
    """유형이 맞느냐와 남길 만하냐는 다른 축이다.

    "코스닥이 7% 올랐다"는 유형도 맞고 원문에도 있지만, 글의 요지가
    수급과 정책 기대라면 이 하나로는 리서치에 쓸 게 없다.
    """
    rows = [{"utility": u} for u in
            ["keep_core"] * 2 + ["keep_supporting"] * 3
            + ["drop_background"] * 4 + ["drop_redundant"] + ["drop_invalid"]]
    got = golden.score_utility(rows)
    assert got["n"] == 11
    assert got["keep_rate"] == pytest.approx(5 / 11)
    assert got["core_rate"] == pytest.approx(2 / 11)


def test_unlabelled_utility_is_not_counted_as_drop():
    assert golden.score_utility([{"utility": None}]) == {"n": 0}


def test_recall_layer_catches_what_per_claim_labels_cannot():
    """안 뽑힌 주장에는 붙일 라벨이 없다.

    주장 100개에 전부 라벨을 붙여도 '빠진 것'은 한 건도 안 세어진다.
    880 이 과추출인지 recall 개선인지 가르려면 이 층이 있어야 한다.
    """
    rows = [
        {"reviewed": True, "kept_count": 3, "missed": ["기관 순매수가 핵심인데 빠짐"]},
        {"reviewed": True, "kept_count": 5, "missed": []},
        {"reviewed": False, "kept_count": None, "missed": []},
    ]
    got = golden.score_recall(rows)
    assert got["n"] == 2
    assert got["found"] == 8
    assert got["missed"] == 1
    assert got["recall"] == pytest.approx(8 / 9)
    assert got["blocks_with_gaps"] == 1


def test_retrieval_recall_is_separate_from_adjudication():
    """찾기가 안 올린 쌍은 판정자가 못 고친다.

    이 둘을 한 숫자로 보면 앞 단계 문제를 뒷 단계 탓으로 돌리게 된다.
    """
    rows = [
        {"id": "P-1", "label": "same_event", "retrieved": True},
        {"id": "P-2", "label": "same_event", "retrieved": False,
         "token_overlap": 0.04, "a_label": "가", "b_label": "나"},
        {"id": "P-3", "label": "related_theme", "retrieved": True},
        {"id": "P-4", "label": "unrelated", "retrieved": False},
    ]
    got = golden.score_retrieval(rows)
    assert got["n"] == 3            # unrelated 는 양성이 아니다
    assert got["retrieved"] == 2
    assert got["recall"] == pytest.approx(2 / 3)
    assert got["lost"][0]["pair"] == "P-2"


def test_f1_reported_per_label():
    rows = [
        {"label": "same_event", "heuristic": "same_event"},
        {"label": "same_event", "heuristic": "unrelated"},
        {"label": "unrelated", "heuristic": "unrelated"},
    ]
    m = golden.score_labels(rows).per_label()["same_event"]
    assert m["precision"] == 1.0
    assert m["recall"] == 0.5
    assert m["f1"] == pytest.approx(2 / 3)


# --- 정답은 사람만 쓴다 ----------------------------------------------------

def test_seeding_blanks_every_answer_field():
    """모델이 만든 값이 정답으로 새어 들어가면 자기 답안지로 자기를 채점한다."""
    rows = [{"id": "x", "heuristic": "same_event", "label": "same_event",
             "utility": "keep_core", "atomic": True,
             "criteria": dict.fromkeys(golden.TOPIC_CRITERIA, True)}]
    cleaned = golden.blank_answers(rows[0])

    assert cleaned["label"] is None
    assert cleaned["utility"] is None
    assert cleaned["atomic"] is None
    assert all(v is None for v in cleaned["criteria"].values())
    assert cleaned["heuristic"] == "same_event"   # 추측은 남긴다


def test_unfilled_topic_is_not_scored_as_failing():
    """빈칸을 세면 안 매긴 것이 '통과 못 함'으로 둔갑해 전부 0% 가 된다."""
    rows = [{"criteria": dict.fromkeys(golden.TOPIC_CRITERIA, None)}]
    assert golden.score_topics(rows) == {"n": 0}


def test_partially_filled_topic_is_skipped():
    rows = [{"criteria": {**dict.fromkeys(golden.TOPIC_CRITERIA, True),
                          "standalone": None}}]
    assert golden.score_topics(rows)["n"] == 0


def test_topic_needs_every_criterion():
    rows = [
        {"criteria": dict.fromkeys(golden.TOPIC_CRITERIA, True)},
        {"criteria": {**dict.fromkeys(golden.TOPIC_CRITERIA, True),
                      "debatable": False}},
    ]
    got = golden.score_topics(rows)
    assert got["all_pass"] == 0.5
    assert got["debatable"] == 0.5


# --- 자동 점검 -------------------------------------------------------------

def test_provenance_audit_counts_by_status():
    letters = [_letter([
        _item("a", span="a", provenance="exact"),
        _item("b", span="b", provenance="stitched"),
        _item("c", span="c", provenance="missing"),
    ])]
    got = golden.audit_provenance(letters)
    assert got["total"] == 3
    assert got["quotable"] == pytest.approx(1 / 3)
    assert len(got["bad"]) == 1        # stitched 는 bad 가 아니라 별도 집계


def test_same_sentence_shape_different_numbers_is_not_duplicate():
    """코스피 6,579 와 코스닥 858 은 문장 틀이 같아도 다른 사실이다.

    자카드만 보면 중복으로 잡히는데, 그렇게 세면 중복률이 부풀어 프롬프트를
    엉뚱하게 고치게 된다.
    """
    letters = [_letter([
        _item("코스피 지수는 6,579.04로 전일 대비 233.51포인트 상승했다"),
        _item("코스닥 지수는 858.91로 전일 대비 1.07포인트 상승했다"),
    ])]
    assert golden.audit_duplication(letters)["duplicates"] == []


def test_near_verbatim_repeat_is_caught():
    letters = [_letter([
        _item("기관이 코스닥을 1조 2,240억 원 순매수했다"),
        _item("기관은 코스닥을 1조 2,240억 원 순매수했다"),
    ])]
    assert len(golden.audit_duplication(letters)["duplicates"]) == 1


def test_paraphrase_slips_through_and_that_is_expected():
    """한국어에서 "상승했다"와 "올랐다"는 글자가 하나도 안 겹친다.

    이 선별 장치로는 못 잡는다. 그래서 0 이 나와도 중복이 없다는 뜻이
    아니고, 진짜 중복은 정답지의 duplicate_of 로만 셀 수 있다.
    """
    letters = [_letter([
        _item("코스닥 지수가 6.97% 상승했다"),
        _item("코스닥 지수는 6.97% 올랐다"),
    ])]
    assert golden.audit_duplication(letters)["duplicates"] == []


def test_claims_in_different_blocks_are_never_duplicates():
    letters = [_letter([
        _item("코스닥이 6.97% 올랐다", block=1),
        _item("코스닥이 6.97% 올랐다", block=2),
    ])]
    assert golden.audit_duplication(letters)["duplicates"] == []


def test_same_block_index_on_different_days_stays_separate():
    """같은 매체의 8/11자 3번 블록과 8/13자 3번 블록은 다른 블록이다.

    날짜를 키에서 빼면 둘이 한 덩어리가 되어 밀도가 부풀고, 서로 다른
    날 기사를 중복으로 비교하게 된다.
    """
    from datetime import datetime
    letters = [
        nl.Newsletter(sender="a@b.c", name="X", layer="통념", subject="",
                      date=datetime(2026, 8, 11),
                      items=[_item("코스닥이 6.97% 올랐다", block=3)]),
        nl.Newsletter(sender="a@b.c", name="X", layer="통념", subject="",
                      date=datetime(2026, 8, 13),
                      items=[_item("코스닥이 6.97% 올랐다", block=3)]),
    ]
    got = golden.audit_duplication(letters)
    assert got["blocks"] == 2
    assert got["claims_per_block"] == 1.0
    assert got["duplicates"] == []


def test_density_distribution_surfaces_prompt_anchoring():
    """한 숫자에 몰리면 프롬프트가 개수를 암시한 것이다."""
    letters = [_letter([_item(f"주장 {n}", block=b)
                        for b in range(4) for n in range(3)])]
    got = golden.audit_duplication(letters)
    assert got["claims_per_block"] == 3.0
    assert got["distribution"] == {3: 4}


# --- 분할 -----------------------------------------------------------------

def test_holdout_is_a_separate_file():
    assert golden.path("claims", "dev") != golden.path("claims", "holdout")


def test_unknown_split_refused():
    with pytest.raises(ValueError):
        golden.path("claims", "test")
