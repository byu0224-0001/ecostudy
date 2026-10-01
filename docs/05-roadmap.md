# 05. 구현 순서

코드를 많이 쓰기 전에, **폰에서 한 번 돌아가는 얇은 세로 슬라이스**를 먼저 만든다.

## 기본값 (아직 반대가 없으면 이걸로)

| 항목 | 값 |
|---|---|
| 1차 UI | 텔레그램 봇 |
| 2차 UI | 모바일 PWA |
| 언어 | 한국어 브리핑, 수집은 한+영 |
| 기간 기본 | 14일 |
| 기사 | 네이버 + Google News RSS |
| 유튜브 | Data API 검색 + 비공식 자막 |
| 모델 | 개별 요약은 싼 모델, 지도는 좋은 모델 |
| 저장 | SQLite |
| 배포 | 작은 VPS, 자막 막히면 집 워커 |

## Step 0 — 저장소 리셋

- [x] 이전 스터디 엔진 코드·데이터·문서 삭제
- [x] 이 설계 문서만 남김

## Step 1 — 엔진 뼈대 (UI 없이)

목표: CLI 한 방으로 JSON 브리핑이 나온다.

```bash
python -m radar brief "미국 국채 금리" --days 14
```

포함할 것:

- 쿼리 확장 (규칙 기반. LLM 확장은 나중)
- Google News RSS
- 네이버 뉴스 (키 없으면 이 소스만 스킵)
- 유튜브 검색 (키 없으면 yt-dlp 검색)
- 자막 수집 (실패해도 메타만)
- 더미가 아닌 실제 LLM 요약 (키 없으면 추출 문장만)
- 채널당 1개, 최근성 정렬
- SQLite 캐시

성공: 터미널에서 합의/갈림/출처 링크가 보인다.  
이 단계가 끝나기 전에 웹을 만들지 않는다.

## Step 2 — 텔레그램 봇

목표: 폰에서 Step 1을 호출한다.

- allowlist (내 user id)
- 키워드 메시지 → 작업 중 표시 → 브리핑
- 영상마다 `유튜브` 버튼
- 재검색 캐시 (같은 키워드 30분)

성공: 핸드폰에서 키워드를 보내고 유튜브 앱이 열린다.

여기까지가 **“써볼 수 있는 것”**이다.

## Step 3 — 다양성 엔진

Step 1~2는 아직 상위 채널에 치우칠 수 있다.

- 한/영·원인 키워드로 검색 분할
- 구독자 버킷 2:3:3
- 매체 중복 제거
- 스탠스 라벨 (상승/하락/중립/조건부)
- 반대 의견 0개면 그 사실을 명시
- 숏츠 기본 제외
- 자막 실패 영상은 신뢰도 낮게

성공: 같은 키워드를 두 번 쳐도 채널 구성이 한쪽으로만 기울지 않는다.  
평소 안 보던 채널이 목록에 실제로 있다.

## Step 4 — 모바일 PWA

- `/` 검색
- `/b/{id}` 브리핑
- 스탠스 필터
- 본 영상 숨김
- 홈 화면 설치 매니페스트
- 봇 메시지에 `웹에서 보기`

성공: 출퇴근 중 한 손으로 갈림을 훑고 영상 2개만 연다.

## Step 5 — 공유 시트와 다이제스트

- 유튜브 URL을 봇에게 보내면 그 영상 요약 + 반대 2개
- 저장 키워드 아침 1통
- 타임스탬프 딥링크

## Step 6 — 선택

필요할 때만:

- Cursor MCP (`brief_topic`)
- 지정 언론 RSS 세트
- 뉴스레터 `.eml` 드롭
- 텔레그램 미니앱
- 집 자막 워커
- Whisper 수동 버튼
- Expo 앱

## 기술 스케치 (Step 1~2)

아직 코드는 없다. 구현 착수 시 이 모양을 기본으로 한다.

```text
radar/
  __init__.py
  cli.py
  config.py
  expand.py
  news/
    naver.py
    google_rss.py
    extract.py
  youtube/
    search.py          # official then yt-dlp
    captions.py        # transcript-api then yt-dlp
    diversity.py
  llm/
    claims.py
    map.py
  store.py
  render/
    telegram.py
    json.py
apps/
  telegram_bot.py
  web/                 # Step 4
```

의존성 후보:

- `httpx`, `feedparser`, `trafilatura`
- `youtube-transcript-api`, 시스템 `yt-dlp`
- `google-api-python-client` (키 있을 때)
- `python-telegram-bot`
- `sqlite3` (표준)
- LLM은 공식 SDK 하나 + 환경변수로 교체

## 키 — 없어도 부분 동작

| 키 | 없으면 |
|---|---|
| `NAVER_CLIENT_ID/SECRET` | 한국어 뉴스는 RSS만 |
| `YOUTUBE_API_KEY` | yt-dlp 검색 |
| `GEMINI_API_KEY` 또는 `ANTHROPIC_API_KEY` | 요약 없이 인용 추출만 |
| `TELEGRAM_BOT_TOKEN` | CLI만 |
| `BRAVE_API_KEY` / `TAVILY_API_KEY` | 없어도 1차는 됨 |

키를 강요하지 않는다. `.env`에 넣는 족족 소스가 켜진다.

## 위험

| 위험 | 완화 |
|---|---|
| 유튜브 자막 403 | 캐시, yt-dlp 폴백, 집 IP 워커, Whisper는 최후 |
| 공식 검색 쿼터 | 캐시, 쿼리 수 제한, yt-dlp |
| 모델 환각 숫자 | 인용 재매칭, 없으면 삭제 |
| 큰 채널만 수집 | 버킷·채널 상한·쿼리 분할 |
| 페이월 기사 | 스니펫, 억지 우회 안 함 |
| VPS 슬립/다운 | 작은 상시 VPS, 봇이 실패를 말함 |
| ToS | 개인, 저속, 재배포 없음. 상용 전환 시 공식만 |

## 의도적으로 미루는 것

- 회원가입, 멀티유저
- 결제
- 자동 매매/시그널
- 앱스토어
- 카카오 챗봇
- 이전 스터디 OS 기능 (ECOS, DART, 회차 관리)

그 코드는 이 저장소에 다시 넣지 않는다. 필요하면 다른 저장소다.

## 다음 작업

설계를 읽었고 기본값에 반대가 없으면 **Step 1 CLI**부터 구현한다.  
반대가 있으면 그 한 줄만 바꾸면 된다. 형태(봇이냐 PWA냐)와 모델 정도다.
