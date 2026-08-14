"""모델 호출.

## 어디까지 맡기고 어디부터 안 맡기는가

모델이 하는 일은 둘뿐이다. 블록이 어떤 성격인지 고르는 것, 블록에서
대조할 주장을 뽑는 것. 둘 다 "글을 읽고 분류한다"는 한 종류의 일이다.

맡기지 않는 것은 그 뒤 전부다. 주장이 맞는지는 ECOS·KRX·DART 가 판정하고,
회차로 낼지는 사람이 정한다. 모델에게 주제를 고르게 하면 그건 운영자의
결론을 모델에게 외주 준 것이고, 처음부터 하지 않기로 한 일이다.

블록의 권한(discover/evidence)도 모델이 정하지 않는다. `blocks.POLICY` 가
정한다. 모델이 분류를 틀리면 블록 하나가 잘못 분류될 뿐이지만, 권한까지
맡기면 원칙 자체가 매번 흔들린다.

## 키가 없어도 돌아간다

키를 안 넣으면 `available()` 이 False 를 돌려주고 호출부는 정규식 경로로
간다. 결과는 나빠지지만 멈추지는 않는다.

## 응답을 캐시하는 이유

같은 메일을 두 번 읽으면 두 번 낸다. 그것도 이유지만 더 중요한 게 있다.
이 시스템은 "왜 이 주제가 올라왔나"를 나중에 되짚을 수 있어야 하는데,
모델 응답이 남지 않으면 그 사슬이 끊긴다. 캐시는 비용 절감이 아니라
감사 기록이다.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from . import config

PROMPTS = config.ROOT / "prompts"
CACHE = config.CACHE_DIR / "llm"

# 사용자가 고른 조합. 분류는 싼 모델로 충분하고 추출은 문맥을 봐야 한다.
# 하루 입력이 2만 토큰대라 어느 쪽을 쓰든 연 $100 을 넘지 않는다. 모델을
# 잘게 나눠 라우팅하는 것보다 프롬프트를 고치는 편이 결과에 훨씬 크게 작용한다.
DEFAULTS = {
    "classify": "gpt-5.4-nano",
    "extract": "gpt-5.6-luna",
}

# 프롬프트·스키마·모델이 바뀌면 캐시가 자동 miss 되도록 버전을 올린다.
# 프롬프트는 파일 내용이 키에 들어가므로 저절로 갈리고, 스키마는 구조가
# 바뀌어도 문자열이 그대로일 수 있어 손으로 올린다.
SCHEMA_VERSION = "claims-v3"

# 100만 토큰당 (입력, 출력) 달러. 알 수 없는 모델은 비용을 0 으로 두되
# telemetry 에 unknown 으로 표시해 합계를 믿지 않게 한다.
PRICES = {
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.6-luna": (1.00, 6.00),
    "gpt-5.6-terra": (2.50, 15.00),
}
TIMEOUT = 120.0
MAX_REPAIR = 1


class Unavailable(RuntimeError):
    pass


# --- 스키마 ---------------------------------------------------------------
#
# 모델의 자유 텍스트를 그대로 파이프라인에 넣지 않는다. 통과하지 못하면
# 한 번 고쳐 부르고, 그래도 안 되면 그 블록만 버리고 나머지를 계속한다.

class BlockLabel(BaseModel):
    index: int
    content_type: str
    reason: str = ""


class BlockLabels(BaseModel):
    labels: list[BlockLabel] = Field(default_factory=list)


class VerificationTarget(BaseModel):
    adapter: str
    purpose: str = ""


class Verification(BaseModel):
    # 참/거짓이 아니라 경로를 묻는다. 이유는 core/claims.py 주석 참고.
    mode: str = "multi_source_research"
    targets: list[VerificationTarget] = Field(default_factory=list)


class Claim(BaseModel):
    headline: str
    claim: str
    claim_type: str = "event_fact"
    source_span: str = ""
    interpretation: str = ""
    verification: Verification = Field(default_factory=Verification)


class Claims(BaseModel):
    claims: list[Claim] = Field(default_factory=list)


# --- 클라이언트 -----------------------------------------------------------

def model_for(task: str) -> str:
    return os.getenv(f"MODEL_{task.upper()}") or DEFAULTS.get(task, "")


def available() -> bool:
    return bool(os.getenv("LLM_API_KEY"))


def _client():
    if not available():
        raise Unavailable(
            "LLM_API_KEY 가 없습니다. .env 에 넣으면 블록 분류와 주장 추출이 "
            "켜집니다. 없어도 정규식 경로로 동작합니다."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise Unavailable("openai 패키지가 없습니다: pip install openai") from exc

    return OpenAI(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL") or None,
        timeout=TIMEOUT,
    )


def prompt(name: str) -> str:
    path = PROMPTS / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"프롬프트가 없습니다: {path}")
    return path.read_text(encoding="utf-8")


def _key(model: str, system: str, user: str, schema: type[BaseModel]) -> str:
    digest = hashlib.sha256()
    schema_tag = f"{SCHEMA_VERSION}:{schema.__name__}"
    for part in (model, schema_tag, system, user):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:24]


def _cached(key: str) -> dict | None:
    path = CACHE / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _store(key: str, payload: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / f"{key}.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(CACHE / f"{key}.json")


@dataclass
class Result:
    data: dict
    model: str
    cached: bool
    elapsed: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    retries: int = 0

    @property
    def cost(self) -> float:
        return estimate_cost(self.model, self.tokens_in, self.tokens_out)


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    price = PRICES.get(model)
    if not price:
        return 0.0
    return tokens_in / 1e6 * price[0] + tokens_out / 1e6 * price[1]


@dataclass
class Telemetry:
    """한 번 돌린 동안의 호출 기록.

    캐시가 있으면 2 초에 끝나지만 매일 오는 새 메일은 전부 cold 다.
    개발 중 체감 속도를 운영 성능으로 착각하지 않으려면 cold 만 따로
    세어야 한다.
    """
    calls: int = 0
    cached: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    retries: int = 0
    elapsed: float = 0.0
    by_stage: dict = field(default_factory=dict)
    unknown_price: set = field(default_factory=set)

    def record(self, task: str, result: Result) -> None:
        self.calls += 1
        row = self.by_stage.setdefault(task, {
            "model": result.model, "calls": 0, "cached": 0,
            "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "elapsed": 0.0,
        })
        row["calls"] += 1
        if result.cached:
            self.cached += 1
            row["cached"] += 1
            return
        self.tokens_in += result.tokens_in
        self.tokens_out += result.tokens_out
        self.retries += result.retries
        self.elapsed += result.elapsed
        row["tokens_in"] += result.tokens_in
        row["tokens_out"] += result.tokens_out
        row["cost"] += result.cost
        row["elapsed"] += result.elapsed
        if result.model not in PRICES:
            self.unknown_price.add(result.model)

    @property
    def cold(self) -> int:
        return self.calls - self.cached

    @property
    def cost(self) -> float:
        return sum(r["cost"] for r in self.by_stage.values())


TELEMETRY = Telemetry()


def reset_telemetry() -> Telemetry:
    global TELEMETRY
    TELEMETRY = Telemetry()
    return TELEMETRY


def ask(task: str, user: str, schema: type[BaseModel], *,
        refresh: bool = False) -> Result:
    """프롬프트 파일 + 입력 → 검증된 구조체.

    실패하면 예외를 던진다. 호출부가 블록 단위로 잡아서 그 블록만 건너뛴다.
    한 통이 이상하다고 하루치를 버리지 않는다.
    """
    system = prompt(task if task in ("classify_blocks", "extract_claims")
                    else task)
    model = model_for("classify" if "classify" in task else "extract")
    key = _key(model, system, user, schema)

    if not refresh and (hit := _cached(key)):
        got = Result(data=hit["data"], model=hit.get("model", model),
                     cached=True)
        TELEMETRY.record(task, got)
        return got

    client = _client()
    started = time.monotonic()
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    last: Exception | None = None
    used_in = used_out = 0
    for attempt in range(MAX_REPAIR + 1):
        try:
            raw, tin, tout = _call(client, model, messages, schema)
            used_in += tin
            used_out += tout
            data = schema.model_validate_json(raw).model_dump()
            _store(key, {"data": data, "model": model, "task": task,
                         "at": time.time(),
                         "tokens": {"in": used_in, "out": used_out}})
            got = Result(data=data, model=model, cached=False,
                         elapsed=time.monotonic() - started,
                         tokens_in=used_in, tokens_out=used_out,
                         retries=attempt)
            TELEMETRY.record(task, got)
            return got
        except ValidationError as exc:
            last = exc
            if attempt >= MAX_REPAIR:
                break
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": ("스키마에 맞지 않습니다. 아래 오류를 고쳐 JSON 만 "
                            f"다시 내세요.\n{exc}"),
            })

    raise ValueError(f"{task}: 스키마 검증 실패 — {last}")


def _call(client, model: str, messages: list[dict],
          schema: type[BaseModel]) -> tuple[str, int, int]:
    """구조화 출력을 지원하면 쓰고, 아니면 JSON 모드로 내려간다.

    모델을 .env 로 바꿀 수 있게 해 둔 이상 어떤 엔드포인트가 올지 모른다.
    지원하지 않는 기능 때문에 통째로 못 쓰는 일은 없어야 한다.
    """
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": False,
                },
            },
        )
    except Exception:
        resp = client.chat.completions.create(
            model=model,
            messages=messages + [{
                "role": "system",
                "content": f"JSON 만 출력하세요. 스키마: "
                           f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}",
            }],
            response_format={"type": "json_object"},
        )
    usage = getattr(resp, "usage", None)
    return (
        resp.choices[0].message.content or "",
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
    )


def cache_size() -> tuple[int, int]:
    if not CACHE.exists():
        return 0, 0
    files = list(CACHE.glob("*.json"))
    return len(files), sum(f.stat().st_size for f in files)
