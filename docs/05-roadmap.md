# 05. 구현 순서

코드를 많이 쓰기 전에, **폰에서 한 번 돌아가는 얇은 세로 슬라이스**를 먼저 만든다.

결정의 이유는 [06-synthesis.md](06-synthesis.md). 텔레그램을 본편 화면으로 두었던 줄은 거기서 바뀌었다. 텔레그램은 알림과 링크, 읽는 화면은 리포트다.

## 기본값

| 항목 | 값 |
|---|---|
| 읽는 화면 | 리포트 HTML. 검색은 `web/index.html`, 예시는 `web/report.html` |
| 폰 진입 | 텔레그램이 리포트 링크를 보냄 |
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

## Step 1 — 엔진 뼈대

CLI가 JSON과 같은 모양의 HTML을 만든다. 키가 없는 소스는 건너뛰고, 원문에 없는 인용은 버린다.

```bash
python -m radar brief "미국 국채 금리" --days 14
python -m radar serve
```

화면 전체는 `web/index.html`에서 시작한다. 예시 리포트는 `web/report.html`이다.

남은 것: 자막이 막히는 환경에서의 집 IP 워커, 텔레그램 링크 발송.

들어 있는 것:

- 검색어를 원문, 상승, 하락으로 나눔
- Google News RSS. 네이버와 유튜브 공식 검색은 키가 있을 때만
- 키가 없으면 yt-dlp 검색. 자막이 없으면 그 영상은 카드에서 제외
- Gemini 키가 있으면 인용 추출, 없으면 원문 문장만
- 원문에 없는 인용은 삭제. 채널당 1개
- SQLite에 기록

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
