# Opinion Radar

키워드 하나를 넣으면, 최근 기사·칼럼과 유튜브 영상에서 **서로 다른 의견**을 모아 한 번에 정리해 주는 개인용 리서치 도구.

이 저장소의 이전 프로젝트(투자 스터디 주제 선정·리서치 엔진)는 전부 제거했다. 지금부터는 이 도구만 다룬다.

검색부터 기록까지 같은 화면 기준으로 붙어 있고, CLI가 그 리포트와 같은 JSON을 만든다. `web/report.html`의 채널과 숫자는 예시이고, 실행 결과는 수집한 인용만 담는다.

## 한 줄로

`미국 국채 금리` 같은 키워드 → 최근 기사/칼럼 종합 + 최근 유튜브 영상 리스트 + 영상마다 핵심 의견 차이까지.

## 왜 직접 만드는가

이미 있는 서비스는 이 문제를 거의 안 푼다.

| 기존 도구 | 하는 일 | 빠지는 것 |
|---|---|---|
| 유튜브 검색·추천 | 내가 보던 채널을 더 보여 줌 | 다른 전문가, 반대 의견 |
| Eightify / Glasp / 요약 확장 | **한 영상**을 요약 | 여러 영상의 의견 지도 |
| Perplexity / 일반 검색 AI | 웹 문서를 종합 | 최근 유튜브 발언의 다양성 |
| 네이버·구글 뉴스 | 기사 목록 | 영상 의견, 스탠스 비교 |

핵심은 검색이 아니라 **필터 버블을 깨는 것**이다. 조회수순 상위 10개를 그대로 보여 주면 결국 또 큰 채널만 나온다.

## 문서

| 문서 | 내용 |
|---|---|
| [docs/01-architecture.md](docs/01-architecture.md) | 전체 파이프라인, 산출물 형식, 다양성 규칙 |
| [docs/02-approaches.md](docs/02-approaches.md) | 만들 수 있는 형태 비교 (봇, PWA, MCP, 자동화 등) |
| [docs/03-sources.md](docs/03-sources.md) | 유튜브·기사 수집의 공식/비공식 경로 |
| [docs/04-mobile.md](docs/04-mobile.md) | 폰에서 쓰는 방법 |
| [docs/05-roadmap.md](docs/05-roadmap.md) | 단계별 구현 순서 |
| [docs/06-synthesis.md](docs/06-synthesis.md) | 세 의견 비교와 최종 결정 |
| [docs/07-schema.md](docs/07-schema.md) | 주장·영상·쟁점 JSON |
| [docs/08-report-ui.md](docs/08-report-ui.md) | 썸네일 카드와 리포트 배치 |
| [design.md](design.md) | 화면 색, 글자, 모서리 |
| [docs/09-screens.md](docs/09-screens.md) | 검색, 수집, 리포트, 기록, 빈 결과 |
| [web/index.html](web/index.html) | 검색 화면 |

## 결정

자세한 비교는 [docs/06-synthesis.md](docs/06-synthesis.md).

1. **엔진은 파이썬.** n8n과 MCP는 본체가 아니다.
2. **텔레그램은 리모컨, 리포트 페이지가 본편.** 썸네일과 쟁점 표는 메시지에 들어가지 않는다.
3. **영상 카드**는 썸네일, 원제목, 키워드, 의견, 다른 점, 타임스탬프를 같이 보여 준다.
4. **쟁점은 같은 명제에 양쪽 인용이 있을 때만** 만든다.
5. 기사는 네이버와 Google News RSS. SerpApi·유료 자막 API는 기본 경로가 아니다.
6. 자막이 막히면 Gemini가 공개 영상의 본론을 보고 카드로 남긴다. 무료 등급의 검색 그라운딩은 쓰지 않고, 기사 주소는 네이버로 찾는다.

화면은 [design.md](design.md)의 어두운 면, 헤어라인, Pretendard를 따른다. 검색은 `web/index.html`, 예시 리포트는 `web/report.html`이다.

```bash
pip install -r requirements.txt
python -m radar doctor
python -m radar brief "미국 국채 금리" --days 14 --html data/report.html
python -m radar serve
```

`serve`를 켠 뒤 검색 화면에서 키워드를 실행하면 리포트로 넘어간다. 네이버, 유튜브 공식 검색, Gemini 키가 없으면 그 경로만 건너뛰고 Google News와 yt-dlp는 그대로 시도한다.

밖에서 폰으로 열려면 Vercel에 올린 주소를 쓴다. 로그인 없이 올린 함수는 60초라 영상 1개, 기사 1개로 끊고, 영상은 응답이 빠른 `gemini-3.6-flash`부터 본다. 만든 리포트 HTML은 그 브라우저에 남는다. 서버 디스크의 기록은 다음 요청까지 남지 않는다. `RADAR_PASSWORD`가 있으면 그 암호가 맞을 때만 검색이 시작된다.

## 전제

- 사용자 1명, 본인만 쓴다.
- 상업 배포, 대량 수집, 자막 재배포는 하지 않는다.
- 비공식 경로는 **개인 리서치용 보조 수단**으로만 쓴다. 유튜브·매체 ToS와 충돌할 수 있다.
- 투자 조언이 아니다. 의견 지도일 뿐이다.

## 다음 결정

구현으로 넘어가기 전에 이것만 고르면 된다.

1. 1차 사용면: **텔레그램 봇** / **PWA** / **둘 다**
2. 요약 모델: **Gemini** / **Claude** / **둘 다 가능하게**
3. 기사 범위: **한국만** / **한국+영문**
4. 호스팅: **로컬+터널** / **싼 VPS 하나**

기본값은 `둘 다` / `둘 다 가능하게` / `한국+영문` / `싼 VPS` 다.
