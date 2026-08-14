"""모델 계층.

키 없이 돌아간다. 검사하는 것은 모델의 품질이 아니라 경계다 —
없으면 어떻게 되는가, 이상한 답을 주면 어떻게 되는가, 그리고
모델이 정하면 안 되는 것을 정하지 못하는가.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from core import blocks, llm, newsletters as nl


def test_no_key_means_unavailable(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert not llm.available()
    with pytest.raises(llm.Unavailable):
        llm._client()


def test_prompts_exist_for_every_task():
    for name in ("classify_blocks", "extract_claims"):
        assert llm.prompt(name).strip(), f"{name} 프롬프트가 비어 있다"


def test_prompt_forbids_subject_based_ad_filtering():
    """실제 데이터에서 (광고) 메일 6통에 주장 170건이 들어 있었다."""
    text = llm.prompt("classify_blocks")
    assert "(광고)" in text and "전부" in text


def test_prompt_refuses_unfalsifiable_claims():
    text = llm.prompt("extract_claims")
    assert "opinion" in text
    assert "신중" in text or "권고" in text


def test_prompt_allows_causal_claims_without_numbers():
    assert "causal_claim" in llm.prompt("extract_claims")


def test_cache_key_changes_with_prompt():
    from core.llm import Claim, Claims, _key
    a = _key("m", "프롬프트 A", "입력", Claims)
    b = _key("m", "프롬프트 B", "입력", Claims)
    assert a != b


def test_cache_key_covers_all_four_inputs(monkeypatch):
    """입력·프롬프트·스키마·모델 넷 중 하나만 바뀌어도 캐시가 갈려야 한다.

    특히 프롬프트다. 문구만 고치고 스키마 버전을 안 올렸을 때 옛 응답을
    그대로 쓰면, 고친 프롬프트가 반영 안 된 결과를 보고 판단하게 된다.
    사람이 매번 --refresh 를 기억해야 하는 구조면 언젠가 틀린다.
    """
    from core.llm import BlockLabels, Claims, _key
    base = _key("m", "프롬프트", "입력", Claims)

    assert _key("m2", "프롬프트", "입력", Claims) != base       # 모델
    assert _key("m", "프롬프트 v2", "입력", Claims) != base     # 프롬프트
    assert _key("m", "프롬프트", "다른 입력", Claims) != base   # 입력
    assert _key("m", "프롬프트", "입력", BlockLabels) != base   # 스키마
    assert _key("m", "프롬프트", "입력", Claims) == base        # 같으면 같다


def test_cache_key_carries_schema_version(monkeypatch):
    """스키마가 바뀌면 옛 응답을 그대로 쓰면 안 된다.

    구조를 바꿔도 프롬프트 문자열이 그대로일 수 있어 버전을 손으로 올린다.
    """
    from core.llm import Claims, _key
    a = _key("m", "s", "u", Claims)
    monkeypatch.setattr(llm, "SCHEMA_VERSION", "claims-v99")
    assert _key("m", "s", "u", Claims) != a


def test_model_comes_from_env(monkeypatch):
    monkeypatch.setenv("MODEL_CLASSIFY", "some-other-model")
    assert llm.model_for("classify") == "some-other-model"


def test_model_has_a_default(monkeypatch):
    monkeypatch.delenv("MODEL_CLASSIFY", raising=False)
    assert llm.model_for("classify")


def test_schema_rejects_free_text():
    with pytest.raises(ValidationError):
        llm.BlockLabels.model_validate_json('{"labels": "전부 광고입니다"}')


def test_schema_accepts_the_shape_we_ask_for():
    got = llm.BlockLabels.model_validate_json(json.dumps({
        "labels": [{"index": 0, "content_type": "sponsored", "reason": "구매 유도"}]
    }))
    assert got.labels[0].content_type == "sponsored"


def test_claim_schema_carries_mode_and_targets():
    got = llm.Claims.model_validate_json(json.dumps({
        "claims": [{
            "headline": "h",
            "claim": "c",
            "claim_type": "numeric_fact",
            "source_span": "c",
            "verification": {
                "mode": "direct_data",
                "targets": [{"adapter": "ecos", "purpose": "지수"}],
            },
        }]
    }))
    claim = got.claims[0]
    assert claim.verification.mode == "direct_data"
    assert claim.verification.targets[0].adapter == "ecos"


def test_telemetry_separates_cold_from_cached():
    """캐시 덕에 빨라진 것을 운영 성능으로 착각하지 않기 위해 따로 센다."""
    t = llm.Telemetry()
    t.record("extract", llm.Result(data={}, model="gpt-5.6-luna", cached=False,
                                   tokens_in=1_000_000, tokens_out=0))
    t.record("extract", llm.Result(data={}, model="gpt-5.6-luna", cached=True))
    assert t.calls == 2
    assert t.cold == 1
    assert t.cost == pytest.approx(1.00)


def test_unknown_model_flagged_not_silently_free():
    t = llm.Telemetry()
    t.record("x", llm.Result(data={}, model="새모델", cached=False,
                             tokens_in=999_999, tokens_out=999_999))
    assert t.cost == 0.0
    assert "새모델" in t.unknown_price


def _letter(html: str) -> nl.Newsletter:
    return nl.Newsletter(
        sender="x <a@b.com>", name="X", layer="통념", subject="", date=None,
        blocks=blocks.split(html),
    )


HTML = "<h2>제목</h2><p>코스닥이 이달 18.72% 올라 상승률 1위입니다.</p>"


def test_enrich_records_failure_and_keeps_going(monkeypatch):
    def boom(*_a, **_k):
        raise llm.Unavailable("키 없음")

    monkeypatch.setattr(llm, "ask", boom)
    letter = _letter(HTML)
    before = list(letter.items)
    nl.enrich(letter)

    assert letter.items == before, "실패했으면 기존 추출을 지우지 않는다"
    assert letter.notes and "분류 실패" in letter.notes[0]
    assert not letter.classified


def test_enrich_never_lets_the_model_set_permissions(monkeypatch):
    """모델이 권한 필드를 보내와도 무시된다."""
    def fake(task, _user, _schema, **_k):
        if task == "classify_blocks":
            return llm.Result(data={"labels": [
                {"index": 0, "content_type": "sponsored",
                 "reason": "구매 유도", "evidence_allowed": True}]},
                model="t", cached=True)
        return llm.Result(data={"claims": []}, model="t", cached=True)

    monkeypatch.setattr(llm, "ask", fake)
    letter = _letter(HTML)
    nl.enrich(letter)

    block = letter.blocks[0]
    assert block.content_type == "sponsored"
    assert not block.evidence_allowed, "POLICY 가 이긴다"


def test_enrich_skips_blocks_that_may_not_be_mined(monkeypatch):
    seen: list[str] = []

    def fake(task, user, _schema, **_k):
        if task == "classify_blocks":
            return llm.Result(data={"labels": [
                {"index": 0, "content_type": "footer", "reason": "바닥글"}]},
                model="t", cached=True)
        seen.append(user)
        return llm.Result(data={"claims": []}, model="t", cached=True)

    monkeypatch.setattr(llm, "ask", fake)
    nl.enrich(_letter(HTML))
    assert seen == [], "바닥글에서는 주장을 뽑지 않는다"


def test_enrich_carries_verify_path_through(monkeypatch):
    def fake(task, _user, _schema, **_k):
        if task == "classify_blocks":
            return llm.Result(data={"labels": [
                {"index": 0, "content_type": "lead_story", "reason": "기사"}]},
                model="t", cached=True)
        return llm.Result(data={"claims": [{
            "headline": "코스닥 상승률 1위",
            "claim": "코스닥이 이달 18.72% 올랐다",
            "claim_type": "numeric_fact",
            "source_span": "코스닥이 이달 18.72% 올라 상승률 1위입니다",
            "interpretation": "심리가 회복되고 있다",
            "verification": {
                "mode": "direct_data",
                "targets": [{"adapter": "ecos", "purpose": "코스닥"}],
            },
        }]}, model="t", cached=True)

    monkeypatch.setattr(llm, "ask", fake)
    letter = _letter(HTML)
    nl.enrich(letter)

    item = letter.claims[0]
    assert item.verify == "ecos"
    assert item.claim_type == "numeric_fact"
    assert item.verification_mode == "direct_data"
    assert item.adapter_verifiable
    assert item.provenance == "exact"
    assert item.quotable


def test_invented_span_is_caught_without_a_model_call(monkeypatch):
    """모델이 source_span 을 냈다고 원문에 있는 것은 아니다."""
    def fake(task, _user, _schema, **_k):
        if task == "classify_blocks":
            return llm.Result(data={"labels": [
                {"index": 0, "content_type": "lead_story", "reason": "기사"}]},
                model="t", cached=True)
        return llm.Result(data={"claims": [{
            "headline": "h",
            "claim": "삼성전자가 HBM 증설을 발표했다",
            "claim_type": "corporate_fact",
            "source_span": "삼성전자는 HBM4 증설 계획을 공개했습니다",
            "verification": {"mode": "primary_source", "targets": []},
        }]}, model="t", cached=True)

    monkeypatch.setattr(llm, "ask", fake)
    letter = _letter(HTML)
    nl.enrich(letter)

    item = letter.claims[0]
    assert item.provenance == "missing"
    assert not item.quotable
    assert any("missing" in n for n in letter.notes)


def test_calendar_blocks_become_schedule_items(monkeypatch):
    def fake(task, _user, _schema, **_k):
        if task == "classify_blocks":
            return llm.Result(data={"labels": [
                {"index": 0, "content_type": "calendar", "reason": "예정"}]},
                model="t", cached=True)
        return llm.Result(data={"claims": [
            {"headline": "PPI", "claim": "7월 PPI 가 발표된다",
             "claim_type": "policy_fact", "source_span": "7월 PPI",
             "verification": {"adapter_verifiable": False, "targets": []},
             "researchable": True}]},
            model="t", cached=True)

    monkeypatch.setattr(llm, "ask", fake)
    letter = _letter(HTML)
    nl.enrich(letter)
    assert letter.schedule and not letter.claims
