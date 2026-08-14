from core import claims as cl


def test_adapters_of_lists_targets():
    ver = cl.Verification(mode="direct_data", targets=[
        cl.VerificationTarget("krx", "수급"),
        cl.VerificationTarget("ecos", "지수"),
    ])
    assert cl.adapters_of(ver) == "krx, ecos"


def test_no_targets_reads_as_none():
    assert cl.adapters_of(cl.Verification()) == "없음"


def test_causal_not_auto_verifiable_without_targets():
    ver = cl.verification_from_dict({"targets": []}, claim_type="causal_claim")
    assert not ver.adapter_verifiable
    assert ver.mode == "multi_source_research"


def test_direct_data_needs_a_target():
    """모드만 있고 갈 곳이 없으면 대조할 수 없다."""
    ver = cl.verification_from_dict(
        {"mode": "direct_data", "targets": []}, claim_type="numeric_fact")
    assert ver.mode == "direct_data"
    assert not ver.adapter_verifiable


def test_mode_falls_back_to_type_default():
    ver = cl.verification_from_dict({"mode": "무엇"}, claim_type="forecast")
    assert ver.mode == "future_tracking"


def test_unknown_adapter_dropped():
    ver = cl.verification_from_dict(
        {"mode": "direct_data", "targets": [{"adapter": "bloomberg"}]},
        claim_type="numeric_fact")
    assert ver.targets == []
    assert not ver.adapter_verifiable


def test_unknown_claim_type_normalized():
    assert cl.normalize_type("아무거나") == "event_fact"
    assert cl.normalize_type("Causal_Claim") == "causal_claim"


# --- 출처 대조 -------------------------------------------------------------

SOURCE = ("코스닥 지수가 최근 10일 동안 30% 이상 상승하며 전 세계 상승률 1위를 "
          "기록했습니다. 기관은 1조 2,240억 원을 순매수했습니다.")


def test_exact_ignores_whitespace():
    assert cl.check_provenance("코스닥  지수가 최근 10일 동안", SOURCE) == "exact"


def test_stitched_is_not_exact():
    """떨어진 두 곳을 이어 붙인 것은 인용이 아니다.

    실측에서 나온 missing 8건 중 대부분이 이 형태였다. 각 조각은 원문에
    있으므로 추적은 되지만 그대로 옮기면 원문에 없는 문장이 된다.
    """
    span = "코스닥 지수가 최근 10일 동안 ... 기관은 1조 2,240억 원을"
    assert cl.check_provenance(span, SOURCE) == "stitched"
    assert not cl.quotable("stitched")


def test_slash_also_counts_as_stitched():
    span = "전 세계 상승률 1위를 기록했습니다 / 기관은 1조 2,240억 원을"
    assert cl.check_provenance(span, SOURCE) == "stitched"


def test_reworded_span_is_fuzzy():
    span = "코스닥 지수가 최근 10일 동안 30% 넘게 상승하며"
    assert cl.check_provenance(span, SOURCE) == "fuzzy"


def test_invented_span_is_missing():
    assert cl.check_provenance("삼성전자가 HBM 공급을 늘렸습니다", SOURCE) == "missing"


def test_empty_span_is_none():
    assert cl.check_provenance("", SOURCE) == "none"


def test_only_exact_is_quotable():
    assert cl.quotable("exact")
    for other in ("fuzzy", "missing", "none", "stitched"):
        assert not cl.quotable(other)
