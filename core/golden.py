"""정답지와 채점.

## 왜 지금인가

지금까지 나온 숫자는 전부 "얼마나 나왔나"였다. 사건 10개, 후보 4개. 그런데
그 10개가 맞는 묶음인지, 그 4개가 실제로 열고 싶은 모임인지는 아무도 모른다.
모델을 Luna 로 올렸을 때 "좋아 보인다" 말고 할 말이 없다면 비용을 올릴
근거가 없다.

## 개발용과 시험용을 나눈다

지금 가진 14통은 프롬프트를 고치고 스키마를 두 번 갈아엎는 동안 계속 봐 왔다.
이 메일들에 맞춰 시스템을 조금씩 손댔으므로 **여기서 잘 나오는 것은 당연하다.**
그래서 14통은 개발용(dev)으로만 쓰고, 앞으로 올 메일 5~10통은 한 번도
보지 않은 채(holdout) 남겨 둔다. 마지막에 그걸로 돌려야 진짜 성적이 나온다.

holdout 을 미리 열어보면 그 순간 dev 가 된다. 되돌릴 수 없다.

## 네 계층을 따로 채점한다

한 층만 맞혀도 전체가 좋아지지 않는다. 블록 분류가 완벽해도 주장 경계가
엉키면 사건이 엉키고, 사건이 맞아도 질문이 시시하면 모임이 안 열린다.

    블록 분류 → 주장 추출 → 사건 쌍 → 주제 품질
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import claims as claims_mod

ROOT = Path(__file__).resolve().parent.parent / "data" / "golden"
SPLITS = ("dev", "holdout")

LAYERS = ("blocks", "claims", "recall", "pairs", "topics")

# 사건 쌍의 정답. 둘 중 하나로 강요하지 않는 이유는 core/events.py 참고.
PAIR_LABELS = ("same_event", "related_theme", "unrelated")

# 주장을 남길지 버릴지. 유형이 맞느냐와는 다른 축이다.
#
# "코스닥이 7% 올랐다"는 유형도 맞고 원문에도 있다. 그런데 그 글의 요지가
# "기관 수급과 정책 기대가 겹쳐 급등했다"라면, 이 주장 하나만으로는
# 리서치에 쓸 게 없다. 맞는 주장과 필요한 주장은 다르다.
#
# 880건 중 몇 개가 실제로 필요했는지는 이 축으로만 답할 수 있다.
CLAIM_UTILITY = (
    "keep_core",        # 글의 요지를 이루는 주장
    "keep_supporting",  # 요지는 아니지만 근거로 필요하다
    "drop_background",  # 맞는 말이지만 각주다
    "drop_redundant",   # 다른 주장과 사실상 같다. duplicate_of 를 채운다
    "drop_invalid",     # 잘못 뽑았거나 원문이 받쳐 주지 않는다
)
KEEP = ("keep_core", "keep_supporting")

# 주제 품질 기준. 다섯 개 모두 사람이 0/1 로 매긴다.
TOPIC_CRITERIA = (
    "why_now",          # 지금 다룰 이유가 있는가
    "investment_link",  # 투자 판단에 닿는가
    "debatable",        # 찬반이 갈리는가
    "standalone",       # 두 시간짜리로 독립적인가
    "evidence_ready",   # 확인할 자료에 닿을 수 있는가
)


def path(layer: str, split: str = "dev") -> Path:
    if layer not in LAYERS:
        raise ValueError(f"모르는 계층: {layer}")
    if split not in SPLITS:
        raise ValueError(f"모르는 분할: {split}")
    return ROOT / split / f"{layer}.jsonl"


def load(layer: str, split: str = "dev") -> list[dict]:
    p = path(layer, split)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            rows.append(json.loads(line))
    return rows


# 표본을 고르는 데는 코드도 모델도 써도 된다. 정답을 정하는 것은 사람만
# 할 수 있다. 모델이 붙인 라벨을 정답으로 저장하고 그 모델을 채점하면
# 자기 답안지로 자기를 채점하는 셈이 된다.
ANSWER_FIELDS = ("label", "utility", "label_mode", "atomic", "criteria")


def blank_answers(row: dict) -> dict:
    """표본에서 답안 칸을 비운다."""
    out = dict(row)
    for f in ANSWER_FIELDS:
        if f in out:
            out[f] = ({k: None for k in out[f]}
                      if isinstance(out[f], dict) else None)
    return out


def save(layer: str, rows: list[dict], split: str = "dev",
         *, seeding: bool = False) -> Path:
    """seeding 이면 답안 칸이 비어 있는지 확인하고 저장한다."""
    if seeding:
        rows = [blank_answers(r) for r in rows]
    p = path(layer, split)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    return p


def note_corpus(split: str, claims: int, blocks: int) -> None:
    """표본을 뽑을 때 전체가 몇 건이었는지 적어 둔다.

    표본 100건에서 나온 비율을 전체로 되돌리려면 전체 크기를 알아야 한다.
    코드에 숫자를 박으면 다음에 코퍼스가 바뀌었을 때 조용히 틀린다.
    """
    p = ROOT / split / "meta.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"claims": claims, "blocks": blocks},
                            ensure_ascii=False, indent=2), encoding="utf-8")


def corpus_size(split: str = "dev") -> int:
    p = ROOT / split / "meta.json"
    if not p.exists():
        return 0
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("claims", 0)
    except (OSError, json.JSONDecodeError):
        return 0


def annotated(rows: list[dict]) -> list[dict]:
    """사람이 라벨을 채운 것만."""
    return [r for r in rows if r.get("label") not in (None, "")]


def filled(layer: str, rows: list[dict]) -> list[dict]:
    """계층마다 '채웠다'의 뜻이 다르다.

    주장은 쓸모부터 묻고, 빠짐 계층은 라벨이 아니라 블록을 다 읽었느냐를
    묻는다. 전부 `label` 로 세면 이 두 층은 영영 0% 로 남는다.
    """
    if layer == "claims":
        return [r for r in rows if r.get("utility")]
    if layer == "recall":
        return [r for r in rows if r.get("reviewed")]
    return annotated(rows)


def progress(split: str = "dev") -> dict[str, tuple[int, int]]:
    out = {}
    for layer in LAYERS:
        rows = load(layer, split)
        out[layer] = (len(filled(layer, rows)), len(rows))
    return out


# --- 채점 ------------------------------------------------------------------

@dataclass
class Score:
    """한 계층의 성적."""
    layer: str
    n: int = 0
    correct: int = 0
    confusion: dict = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0

    def add(self, truth: str, guess: str) -> None:
        self.n += 1
        if truth == guess:
            self.correct += 1
        self.confusion.setdefault(truth, {}).setdefault(guess, 0)
        self.confusion[truth][guess] += 1

    def per_label(self) -> dict[str, dict]:
        """라벨마다 precision·recall.

        전체 정확도만 보면 흔한 라벨에 가려진다. 사건 쌍에서 unrelated 가
        대부분이면 전부 unrelated 라고 찍어도 정확도가 높게 나온다.
        """
        labels = set(self.confusion) | {
            g for row in self.confusion.values() for g in row}
        out = {}
        for label in sorted(labels):
            tp = self.confusion.get(label, {}).get(label, 0)
            predicted = sum(row.get(label, 0) for row in self.confusion.values())
            actual = sum(self.confusion.get(label, {}).values())
            p = tp / predicted if predicted else 0.0
            r = tp / actual if actual else 0.0
            out[label] = {
                "precision": p,
                "recall": r,
                "f1": 2 * p * r / (p + r) if p + r else 0.0,
                "n": actual,
            }
        return out


def score_labels(rows: list[dict], guess_key: str = "heuristic") -> Score:
    """정답이 있는 행만 골라 예측과 맞춰 본다."""
    s = Score(layer="labels")
    for r in annotated(rows):
        s.add(r["label"], r.get(guess_key, ""))
    return s


def score_utility(rows: list[dict]) -> dict:
    """880건 중 몇 개가 필요했나.

    이게 이 정답지의 첫 번째 질문이다. 유형을 맞혔느냐보다 먼저다.
    맞는 주장을 잔뜩 뽑아도 필요 없는 것이면 뒤 단계가 묽어질 뿐이다.
    """
    marked = [r for r in rows if r.get("utility")]
    if not marked:
        return {"n": 0}
    counts = {u: sum(1 for r in marked if r["utility"] == u)
              for u in CLAIM_UTILITY}
    kept = sum(counts[u] for u in KEEP)
    return {
        "n": len(marked),
        "counts": counts,
        "keep_rate": kept / len(marked),
        "core_rate": counts["keep_core"] / len(marked),
    }


def score_recall(rows: list[dict]) -> dict:
    """놓친 주장은 몇 개인가.

    주장마다 라벨을 붙이는 것으로는 이 질문에 답할 수 없다. 아예 안 뽑힌
    것에는 붙일 라벨이 없기 때문이다. 그래서 블록을 통째로 읽고 빠진 것을
    적는 층을 따로 둔다.

    이게 없으면 880 이 과추출인지 recall 개선인지 영영 못 가른다. 위에서
    재는 것은 뽑은 것의 질(정밀도)뿐이다.
    """
    marked = [r for r in rows if r.get("reviewed")]
    if not marked:
        return {"n": 0}
    found = sum(r.get("kept_count", 0) for r in marked)
    missed = sum(len(r.get("missed") or []) for r in marked)
    total = found + missed
    return {
        "n": len(marked),
        "found": found,
        "missed": missed,
        "recall": found / total if total else 0.0,
        "blocks_with_gaps": sum(1 for r in marked if r.get("missed")),
    }


def score_retrieval(rows: list[dict]) -> dict:
    """찾기 단계가 진짜 양성을 몇 개나 올렸나.

    판정 정확도와 섞으면 안 된다. 찾기가 안 올린 쌍은 판정자에게 도착하지
    않으므로, 판정을 아무리 잘해도 그 쌍은 영영 놓친다. 나중에 "Luna 가
    왜 이걸 못 잡았지" 할 때 앞 단계 문제인지 가려내려면 따로 재야 한다.
    """
    marked = annotated(rows)
    positives = [r for r in marked if r["label"] in ("same_event", "related_theme")]
    if not positives:
        return {"n": 0}
    surfaced = [r for r in positives if r.get("retrieved")]
    lost = [r for r in positives if not r.get("retrieved")]
    return {
        "n": len(positives),
        "retrieved": len(surfaced),
        "recall": len(surfaced) / len(positives),
        "lost": [{"pair": r.get("id") or r.get("pair_id"),
                  "label": r["label"],
                  "overlap": r.get("token_overlap"),
                  "a": r.get("a_label"), "b": r.get("b_label")}
                 for r in lost],
    }


def score_topics(rows: list[dict]) -> dict:
    """주제 품질. 기준마다 통과율과, 다섯 개 모두 통과한 비율.

    다섯 개를 다 채운 줄만 센다. 빈칸이 있는 줄을 세면 안 매긴 것이
    '통과 못 함'으로 둔갑해 전부 0% 로 나온다.
    """
    marked = [r for r in rows
              if all(r.get("criteria", {}).get(c) is not None
                     for c in TOPIC_CRITERIA)]
    if not marked:
        return {"n": 0}
    out = {"n": len(marked)}
    for c in TOPIC_CRITERIA:
        hit = sum(1 for r in marked if r["criteria"].get(c))
        out[c] = hit / len(marked)
    out["all_pass"] = sum(
        1 for r in marked
        if all(r["criteria"].get(c) for c in TOPIC_CRITERIA)) / len(marked)
    return out


# --- 자동 채점 (사람 없이 되는 것) -----------------------------------------

def audit_provenance(letters) -> dict:
    """출처 대조. 정답지가 필요 없다. 원문 검색이면 끝난다."""
    counts = dict.fromkeys(claims_mod.PROVENANCE, 0)
    bad = []
    for L in letters:
        for item in L.claims:
            counts[item.provenance] = counts.get(item.provenance, 0) + 1
            if item.provenance in ("missing", "fuzzy", "none"):
                bad.append({"source": L.name, "block": item.block,
                            "status": item.provenance,
                            "span": item.source_span[:80],
                            "claim": item.text[:60]})
    total = sum(counts.values())
    return {"counts": counts, "total": total, "bad": bad,
            "quotable": counts.get("exact", 0) / total if total else 0.0}


def audit_duplication(letters, threshold: float = 0.6) -> dict:
    """같은 블록에서 같은 말을 두 번 뽑았는가.

    이건 **선별 장치이지 측정 장치가 아니다.** 단어가 거의 그대로 겹치는
    경우만 잡는다. 어미가 다르거나 다른 동사로 바꿔 쓴 것은 못 잡는다.
    한국어에서 "상승했다"와 "올랐다"는 글자가 하나도 안 겹친다.

    그래서 여기 나온 수를 중복률이라고 부르면 안 된다. 실제 중복은
    정답지의 `duplicate_of` 로만 셀 수 있다. 여기서 0 이 나와도 중복이
    없다는 뜻이 아니라 **눈에 띄게 똑같은 것은 없다**는 뜻이다.

    숫자가 다르면 중복으로 세지 않는다. "코스피 6,579"와 "코스닥 858"은
    문장 틀이 같아 자카드가 높지만 서로 다른 사실이다.
    """
    import re
    from collections import defaultdict

    from .sessions import _stem

    def words(text: str) -> set[str]:
        # 어미를 떼지 않으면 "상승했다"와 "상승 마감했다"가 남남이 된다.
        return {_stem(w) for w in re.findall(r"[가-힣]{2,}", text)}

    # 날짜를 키에 넣지 않으면 같은 매체의 8/11자 3번 블록과 8/13자 3번
    # 블록이 한 덩어리가 된다. 밀도가 부풀고, 서로 다른 날 기사를 중복으로
    # 비교하게 된다.
    from . import newsletters as nl

    groups = defaultdict(list)
    for L in letters:
        for item in L.claims:
            groups[(L.name, nl._day(L), item.block)].append(item)

    pairs, dupes = 0, []
    for key, group in groups.items():
        for a in range(len(group)):
            for b in range(a + 1, len(group)):
                x, y = group[a], group[b]
                tx, ty = words(x.text), words(y.text)
                if not tx or not ty:
                    continue
                pairs += 1
                if len(tx & ty) / len(tx | ty) < threshold:
                    continue
                nx = set(re.findall(r"[\d.,]+", x.text))
                ny = set(re.findall(r"[\d.,]+", y.text))
                if nx and ny and nx != ny:
                    continue        # 숫자가 다르면 다른 사실이다
                dupes.append({"source": key[0], "block": key[1],
                              "a": x.text[:60], "b": y.text[:60]})

    per_block = [len(g) for g in groups.values()]
    return {
        "blocks": len(groups),
        "claims_per_block": sum(per_block) / len(per_block) if per_block else 0,
        "max_per_block": max(per_block, default=0),
        "distribution": {n: per_block.count(n) for n in sorted(set(per_block))},
        "pairs_checked": pairs,
        "duplicates": dupes,
        # 이름에 screen 을 남겨 둔다. 진짜 중복률로 착각하지 않도록.
        "screen_hit_rate": len(dupes) / pairs if pairs else 0.0,
    }
