# 07. 데이터 스키마

모델이 자유롭게 요약하지 않는다. 출처 하나는 아래 객체로만 남는다. 필드가 비면 빈 배열로 두고, 지어 채우지 않는다.

## 문장 층

한 출처의 각 주장 묶음:

| 필드 | 의미 | 예 |
|---|---|---|
| `fact` | 출처가 데이터로 말한 것 | 10년물 입찰 커버리지가 낮았다 |
| `interpretation` | 그 사실에서 원인을 읽은 것 | 공급 부담이 커브를 밀었다 |
| `opinion` | 그래서 어떻다는 판단 | 장기금리는 상승 압력이 있다 |
| `forecast` | 시점과 방향이 있는 예측 | 6~12개월, 10년물 상승 |
| `implication` | **그 출처가 말한** 포지션 함의 | 장기채 가격에 불리 |
| `evidence` | 숫자, 이벤트, 1차 자료 이름 | 커버리지, CPI, FOMC |
| `assumptions` | 말하지 않으면 무너지는 전제 | 침체가 깊어지지 않는다 |
| `quote` | 원문 짧은 인용 | 타임스탬프 또는 문장 |
| `quote_status` | `exact` / `fuzzy` / `missing` | `missing`이면 폐기 |

`implication`을 시스템 조언으로 렌더링하지 않는다. 라벨은 “출처의 해석”이다.

## 영상

```json
{
  "id": "yt_sample_fiscal",
  "video_id": null,
  "url": null,
  "title": "원제목 그대로",
  "channel": "채널명",
  "published_at": "2026-09-28",
  "duration_sec": 1104,
  "thumbnail_url": "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
  "language": "ko",
  "subscriber_bucket": "mid",
  "keywords": ["재정 적자", "국채 공급", "텀 프리미엄"],
  "stance": "up",
  "issues": ["fiscal", "term_premium"],
  "summary": "이 영상이 하는 말 두세 문장",
  "delta": "다른 영상과 다른 점 한 문단",
  "claims": [],
  "caption_status": "ok",
  "selected_because": "하락 프레임 검색, 중형 채널, 재정 논점의 반대쪽"
}
```

`stance`는 `up` | `down` | `range` | `structural` | `unknown` 이다. 카드 배지용이다. 본편은 `issues`와 `claims`다.

썸네일은 구현 시 `video_id`로 위 URL을 만든다. 별도 이미지 API가 필요 없다.

`delta`는 Reduce가 만든다. Map 단계에서는 비운다. 다른 카드와 비교하기 전에 “다르다고” 쓰지 않는다.

## 기사

영상과 같은 `claims`를 쓴다. `thumbnail_url` 대신 `publisher`, `canonical_url`, `section`(`news` | `column` | `primary`). 칼럼과 뉴스를 한 더미로 섞지 않는다.

## 쟁점

```json
{
  "id": "fiscal",
  "label": "재정과 국채 공급",
  "proposition": "국채 공급 증가가 장기금리의 주된 상승 압력인가",
  "sides": [
    {
      "label": "공급이 민다",
      "source_ids": ["yt_sample_fiscal"],
      "quote_ok": true
    },
    {
      "label": "수요 둔화가 흡수한다",
      "source_ids": ["yt_sample_growth"],
      "quote_ok": true
    }
  ],
  "status": "conflict"
}
```

`status`는 `conflict` | `incomparable` | `consensus` 만 허용한다.

- `conflict`: 같은 `proposition`에 양쪽 `quote_status != missing`
- `consensus`: 인용 있는 출처가 한쪽으로만 모임
- `incomparable`: 말은 많지만 같은 명제가 아님. 쟁점 행으로 승격하지 않는다.

## 리포트

```json
{
  "keyword": "미국 국채 금리",
  "window_days": 30,
  "generated_at": "2026-10-01T00:00:00Z",
  "sample": false,
  "queries": [],
  "pool": { "youtube_seen": 36, "youtube_kept": 8, "articles_kept": 6 },
  "one_line": "관측은 비슷하고, 원인 명제가 갈린다",
  "issues": [],
  "videos": [],
  "articles": [],
  "blind_spot": "사용자가 등록한 채널 목록이 없으면 null"
}
```

시안 HTML은 `sample: true`에 해당한다. 숫자와 채널명은 예시이며 수집 결과가 아니다.

## Map-Reduce 경계

| 단계 | 입력 | 출력 | 모델 |
|---|---|---|---|
| 선별 | 메타데이터, 제목, 채널 | 분석할 id 8+6 | 규칙. 모델 아님 |
| Map | 자막 또는 본문 1개 | claims[] | 싼 모델 |
| 검증 | quote vs 원문 | quote_status | 코드 |
| Reduce | 검증을 통과한 claims만 | issues, delta, one_line | 좋은 모델 |

Reduce 입력에 자막 전문을 넣지 않는다.
