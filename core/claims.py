"""주장의 유형, 검증 방식, 출처 대조.

## 왜 boolean 하나로는 안 되는가

처음에는 주장마다 `researchable` 을 참/거짓으로 받았다. 실측해 보니
489건 중 481건이 참이었다. `interpretation` 18건 전부, `opinion` 2건까지
참으로 표시됐다. 거의 항상 참인 필드는 아무것도 가르지 못한다.

문제는 질문이 잘못된 것이었다. "이걸 연구할 가치가 있나"는 주장 하나가
아니라 사건이나 주제 단위에서 답할 일이다. 주장 단위에서 물어야 할 것은
**"이걸 어떻게 확인하나"** 이고, 그건 참/거짓이 아니라 방식의 문제다.

## 출처 대조는 모델에게 묻지 않는다

모델이 `source_span` 을 냈다고 그 구절이 원문에 있는 것은 아니다. 실제로
489건 중 8건은 원문에 그대로 없었다. 대부분 떨어진 두 문장을 "..." 으로
이어 붙인 것이었다. 지어낸 것은 아니지만 인용도 아니다.

이 대조는 문자열 검색이라 모델도 돈도 필요 없다. 그런데 이걸 하지 않으면
"provenance 100%"라는 말이 "필드가 채워져 있다"는 뜻밖에 되지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 주장의 성격
CLAIM_TYPES = (
    "numeric_fact",         # 숫자·지표
    "event_fact",           # 일어난 일
    "market_observation",   # 시장 관측
    "policy_fact",          # 정책·제도
    "corporate_fact",       # 기업 실적·공시
    "causal_claim",         # 인과
    "interpretation",       # 해석
    "forecast",             # 전망
    "opinion",              # 의견·권고
)

# 어떻게 확인하는가. 참/거짓이 아니라 방식이다.
#
# 이 값이 곧 나중에 만들 Verification Router 의 입력이 된다. 그래서
# "확인 가능한가"가 아니라 "어느 경로로 가야 하나"를 묻는다.
VERIFICATION_MODES = (
    "direct_data",           # 어댑터로 바로 대조 (ECOS·KRX·DART·FRED)
    "primary_source",        # 원문을 찾아야 한다 (공시·보도자료·법령)
    "multi_source_research", # 여러 근거를 모아야 판단이 선다
    "interpretive",          # 검증 대상이 아니라 논의 대상
    "future_tracking",       # 아직 안 일어났다. 추적 대상
    "not_researchable",      # 광고 문구 등 다룰 것이 없다
)

# 유형이 통상 어느 방식으로 가는가. 모델이 모드를 안 주거나 이상한 값을
# 줬을 때의 기본값이지, 강제 규칙이 아니다.
DEFAULT_MODE = {
    "numeric_fact": "direct_data",
    "market_observation": "direct_data",
    "corporate_fact": "primary_source",
    "policy_fact": "primary_source",
    "event_fact": "primary_source",
    "causal_claim": "multi_source_research",
    "interpretation": "interpretive",
    "forecast": "future_tracking",
    "opinion": "not_researchable",
}

KNOWN_ADAPTERS = ("ecos", "krx", "dart", "fred", "bls", "fed", "none")

# 출처 대조 결과
PROVENANCE = ("exact", "stitched", "fuzzy", "missing", "none")

# 모델이 떨어진 구절을 이어 붙일 때 쓰는 표시
STITCH = re.compile(r"\s*(?:\.\.\.|…|\s/\s)\s*")


@dataclass
class VerificationTarget:
    adapter: str
    purpose: str = ""


@dataclass
class Verification:
    mode: str = "multi_source_research"
    targets: list[VerificationTarget] = field(default_factory=list)

    @property
    def adapter_verifiable(self) -> bool:
        """어댑터 하나로 바로 맞춰볼 수 있는가.

        모드가 direct_data 이고 실제로 갈 곳이 지정돼야 참이다. 둘 중
        하나만으로는 안 된다. 모드만 있고 대상이 없으면 어디를 볼지
        모르는 것이고, 대상만 있고 모드가 다르면 그 어댑터는 방증일 뿐이다.
        """
        return self.mode == "direct_data" and bool(
            [t for t in self.targets if t.adapter != "none"])


def normalize_mode(raw: str | None, *, claim_type: str) -> str:
    value = (raw or "").strip().lower()
    if value in VERIFICATION_MODES:
        return value
    return DEFAULT_MODE.get(claim_type, "multi_source_research")


def normalize_type(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    return value if value in CLAIM_TYPES else "event_fact"


def normalize_targets(raw: list[dict] | None) -> list[VerificationTarget]:
    out: list[VerificationTarget] = []
    seen: set[str] = set()
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        adapter = (row.get("adapter") or "none").strip().lower()
        if adapter not in KNOWN_ADAPTERS or adapter == "none":
            continue
        if adapter in seen:
            continue
        seen.add(adapter)
        out.append(VerificationTarget(
            adapter=adapter, purpose=(row.get("purpose") or "").strip()))
    return out


def verification_from_dict(raw: dict | None, *, claim_type: str) -> Verification:
    raw = raw or {}
    return Verification(
        mode=normalize_mode(raw.get("mode"), claim_type=claim_type),
        targets=normalize_targets(raw.get("targets")),
    )


def adapters_of(verification: Verification) -> str:
    """capture CLI 가 받는 문자열. 없으면 '없음'."""
    names = [t.adapter for t in verification.targets]
    return ", ".join(names) if names else "없음"


# --- 출처 대조 -------------------------------------------------------------

def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def check_provenance(span: str, source: str) -> str:
    """모델이 준 구절이 원문에 실제로 있는가.

    stitched 를 따로 두는 이유는 fuzzy 와 성격이 다르기 때문이다. fuzzy 는
    모델이 문장을 조금 바꿔 쓴 것이고, stitched 는 떨어진 두 곳을 이어 붙인
    것이다. 후자는 각 조각이 원문에 정확히 있으므로 추적은 되지만, 그대로
    인용하면 원문에 없는 문장이 만들어진다.
    """
    if not span:
        return "none"
    if not source:
        return "missing"

    hay, needle = _squash(source), _squash(span)
    if not needle:
        return "none"
    if needle in hay:
        return "exact"

    parts = [_squash(p) for p in STITCH.split(span) if _squash(p)]
    if len(parts) > 1 and all(p in hay for p in parts):
        return "stitched"

    # 원문과 겹치는 조각들이 구절의 대부분을 덮으면 표현만 손댄 것으로 본다.
    # 가장 긴 조각 하나만 보면 "30% 이상"을 "30% 넘게"로 바꾼 것처럼 가운데가
    # 끊긴 경우를 놓친다. 다만 두세 글자짜리 조각은 아무 데나 걸리므로
    # 뺀다.
    from difflib import SequenceMatcher
    covered = sum(b.size for b in SequenceMatcher(None, needle, hay)
                  .get_matching_blocks() if b.size >= 4)
    return "fuzzy" if covered >= len(needle) * 0.75 else "missing"


def quotable(status: str) -> bool:
    """자료에 그대로 인용해도 되는가. exact 만 허용한다."""
    return status == "exact"
