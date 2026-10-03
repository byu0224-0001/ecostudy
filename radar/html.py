import html
from radar.textutil import STANCE_LABEL


def render_report(report: dict, asset_prefix: str = "") -> str:
    keyword = _esc(report.get("keyword") or "")
    videos = report.get("videos") or []
    articles = report.get("articles") or []
    issues = report.get("issues") or []
    body = [
        _shell_open(f"{keyword} — Opinion Radar", asset_prefix, current=""),
        '<section class="hero-band"><div class="wrap">',
        f'<p class="eyebrow">최근 {int(report.get("window_days") or 0)}일 · 영상 {len(videos)} · 기사 {len(articles)}</p>',
        f"<h1>{keyword}</h1>",
        f'<p class="one-line">{_esc(report.get("one_line") or "")}</p>',
    ]
    if report.get("sample"):
        body.append('<p class="sample-pill">예시 데이터. 채널과 숫자는 수집 결과가 아니다.</p>')
    skipped = report.get("skipped") or []
    if skipped:
        body.append('<ul class="skip-list">')
        for item in skipped:
            body.append(f"<li>{_esc(item)}</li>")
        body.append("</ul>")
    body.append("</div></section>")
    queries = report.get("queries") or []
    if queries:
        joined = ", ".join(_esc(query) for query in queries)
        body.append(
            '<section class="blind-spot"><div class="wrap">'
            "<h2>검색어를 나눠 모았다</h2>"
            f"<p>{joined}</p>"
            "</div></section>"
        )
    body.append('<section class="section section-issues"><div class="wrap"><h2>어디가 갈리는가</h2>')
    if issues:
        body.append('<div class="issue-list">')
        for issue in issues:
            body.append(
                f'<button class="issue-card" type="button" data-issue-filter="{_esc(issue.get("id") or "")}" aria-pressed="false">'
                f'<h3>{_esc(issue.get("label") or "")}</h3>'
                f'<p class="proposition">{_esc(issue.get("proposition") or "")}</p>'
                '<dl class="sides">'
            )
            for side in issue.get("sides") or []:
                body.append(
                    '<div class="side">'
                    f'<dt>{_esc(side.get("label") or "")}</dt>'
                    f'<dd>출처 {len(side.get("source_ids") or [])}개</dd>'
                    "</div>"
                )
            body.append("</dl></button>")
        body.append("</div>")
    else:
        body.append('<div class="notice-card"><h2>같은 명제로 올린 쟁점이 없다</h2><p>양쪽 인용이 같은 주제에 없을 때는 쟁점 행을 만들지 않습니다.</p></div>')
    body.append("</div></section>")
    body.append('<section class="section section-videos" id="videos"><div class="wrap"><h2>영상</h2>')
    body.append(
        '<div class="filters" role="group" aria-label="방향 필터">'
        '<button class="chip" type="button" data-stance-filter="all" aria-pressed="true">전체</button>'
        '<button class="chip" type="button" data-stance-filter="up" aria-pressed="false">상승 압력</button>'
        '<button class="chip" type="button" data-stance-filter="down" aria-pressed="false">하락 압력</button>'
        '<button class="chip" type="button" data-stance-filter="range" aria-pressed="false">범위·구조</button>'
        "</div>"
        f'<p class="count-line">보이는 영상 <span id="video-count">{len(videos)}</span>개. 쟁점을 누르면 그 명제만 남는다.</p>'
        '<p class="empty" id="video-empty" hidden>이 조합에 해당하는 영상이 없다. 전체로 되돌리면 다시 보인다.</p>'
    )
    if videos:
        body.append('<div class="video-list">')
        for video in videos:
            body.append(_video_card(video))
        body.append("</div>")
    else:
        body.append('<div class="notice-card"><h2>영상 카드가 없다</h2><p>자막에서 다시 찾은 문장이 있는 영상만 보여 줍니다.</p></div>')
    body.append("</div></section>")
    body.append('<section class="section section-articles"><div class="wrap"><h2>기사와 칼럼</h2>')
    if articles:
        body.append('<div class="article-list">')
        for article in articles:
            body.append(_article_card(article))
        body.append("</div>")
    else:
        body.append('<div class="notice-card"><h2>기사 카드가 없다</h2><p>본문이나 요약에서 확인된 문장이 없습니다.</p></div>')
    body.append("</div></section>")
    body.append(_footer())
    body.append(f'<script src="{_esc(asset_prefix)}app.js"></script></body></html>')
    return "".join(body)


def render_history(reports: list[dict], asset_prefix: str = "/") -> str:
    parts = [
        _shell_open("기록 — Opinion Radar", asset_prefix, current="history.html"),
        '<section class="hero-band"><div class="wrap"><p class="eyebrow">지난 검색</p><h1>기록</h1>',
    ]
    if not reports:
        parts.append("<p class=\"lede\">아직 저장된 리포트가 없습니다.</p></div></section>")
        parts.append(
            '<section class="section section-plain"><div class="wrap"><div class="notice-card">'
            "<h2>검색부터 시작합니다</h2><p>키워드를 실행하면 이 목록에 남습니다.</p></div>"
            f'<p class="screen-links"><a class="button-primary" href="{_esc(asset_prefix)}index.html">검색</a></p>'
            "</div></section>"
        )
    else:
        parts.append("<p class=\"lede\">저장한 리포트를 다시 엽니다.</p></div></section>")
        parts.append('<section class="section section-plain"><div class="wrap"><div class="history-list">')
        for report in reports:
            pool = report.get("pool") or {}
            parts.append(
                f'<a class="history-card" href="{_esc(asset_prefix)}r/{_esc(report.get("id") or "")}">'
                f'<p class="card-kicker"><time>{_esc(_day(report.get("generated_at")))}</time>'
                f'<span>{int(report.get("window_days") or 0)}일 · 영상 {int(pool.get("youtube_kept") or 0)} · 기사 {int(pool.get("articles_kept") or 0)}</span></p>'
                f'<h2>{_esc(report.get("keyword") or "")}</h2>'
                f'<p>{_esc(report.get("one_line") or "")}</p></a>'
            )
        parts.append("</div></div></section>")
    parts.append(_footer())
    parts.append("</body></html>")
    return "".join(parts)


def _video_card(video: dict) -> str:
    issues = " ".join(video.get("issues") or [])
    stance = video.get("stance") or "unknown"
    thumb = video.get("thumbnail_url") or ""
    url = video.get("url") or ""
    start = next((claim.get("start_sec") for claim in video.get("claims") or [] if claim.get("start_sec") is not None), None)
    stamp_url = url
    if start is not None and url:
        stamp_url = f"{url}{'&' if '?' in url else '?'}t={int(start)}"
    bits = [
        f'<article class="video-card" data-stance="{_esc(stance)}" data-issues="{_esc(issues)}">',
        f'<a class="thumb" href="{_esc(url)}" target="_blank" rel="noopener noreferrer">',
    ]
    if thumb:
        bits.append(f'<img src="{_esc(thumb)}" alt="">')
    bits.append('<span class="play" aria-hidden="true"></span>')
    if video.get("duration_sec"):
        bits.append(f'<span class="duration">{_esc(_clock(video.get("duration_sec")))}</span>')
    bits.append("</a><div>")
    bits.append('<div class="card-kicker">')
    bits.append(f'<span class="badge">{_esc(STANCE_LABEL.get(stance, "미분류"))}</span>')
    bits.append(f'<span>{_esc(video.get("channel") or "")}</span>')
    if video.get("published_at"):
        bits.append(f'<time datetime="{_esc(video.get("published_at") or "")}">{_esc(_day(video.get("published_at")))}</time>')
    bits.append("</div>")
    bits.append(f'<h3>{_esc(video.get("title") or "")}</h3>')
    bits.append(_keywords(video.get("keywords") or []))
    if video.get("summary"):
        bits.append(f'<p class="summary">{_esc(video.get("summary") or "")}</p>')
    if video.get("delta"):
        bits.append(
            f'<div class="delta"><span class="delta-label">{_esc(video.get("delta_label") or "다른 영상과 다른 점")}</span>'
            f'<p>{_esc(video.get("delta") or "")}</p></div>'
        )
    quote = next((claim for claim in video.get("claims") or [] if claim.get("quote")), None)
    if quote:
        stamp = _clock(quote.get("start_sec")) if quote.get("start_sec") is not None else "인용"
        bits.append(
            '<blockquote class="quote">'
            f'<a class="stamp" href="{_esc(stamp_url)}" target="_blank" rel="noopener noreferrer">{_esc(stamp)}</a>'
            f'<p>“{_esc(quote.get("quote") or "")}”</p></blockquote>'
        )
    if url:
        bits.append(f'<a class="button-ghost" href="{_esc(url)}" target="_blank" rel="noopener noreferrer">유튜브에서 보기</a>')
    bits.append("</div></article>")
    return "".join(bits)


def _article_card(article: dict) -> str:
    section = article.get("section") or "news"
    label = {"news": "뉴스", "column": "칼럼", "primary": "1차"}.get(section, "뉴스")
    badge = "badge badge-column" if section == "column" else "badge"
    bits = ['<article class="article-card"><div class="card-kicker">', f'<span class="{badge}">{label}</span>']
    bits.append(f'<span>{_esc(article.get("publisher") or "")}</span>')
    if article.get("published_at"):
        bits.append(f'<time datetime="{_esc(article.get("published_at") or "")}">{_esc(_day(article.get("published_at")))}</time>')
    bits.append("</div>")
    title = _esc(article.get("title") or "")
    url = article.get("canonical_url") or ""
    if url:
        bits.append(f'<h3><a href="{_esc(url)}" target="_blank" rel="noopener noreferrer">{title}</a></h3>')
    else:
        bits.append(f"<h3>{title}</h3>")
    bits.append(_keywords(article.get("keywords") or []))
    quote = next((claim for claim in article.get("claims") or [] if claim.get("quote")), None)
    if quote:
        bits.append(
            '<blockquote class="quote">'
            f'<a class="stamp" href="{_esc(url)}" target="_blank" rel="noopener noreferrer">인용</a>'
            f'<p>“{_esc(quote.get("quote") or "")}”</p></blockquote>'
        )
    elif article.get("summary"):
        bits.append(f'<p class="summary">{_esc(article.get("summary") or "")}</p>')
    if article.get("delta"):
        bits.append(
            f'<div class="delta"><span class="delta-label">{_esc(article.get("delta_label") or "다른 글과 다른 점")}</span>'
            f'<p>{_esc(article.get("delta") or "")}</p></div>'
        )
    bits.append("</article>")
    return "".join(bits)


def _keywords(words: list[str]) -> str:
    if not words:
        return ""
    items = "".join(f"<li>{_esc(word)}</li>" for word in words)
    return f'<ul class="keywords">{items}</ul>'


def _shell_open(title: str, asset_prefix: str, current: str) -> str:
    def link(href: str, label: str) -> str:
        current_attr = ' aria-current="page"' if href == current else ""
        return f'<a href="{_esc(asset_prefix)}{href}"{current_attr}>{label}</a>'

    return (
        "<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(title)}</title>"
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">'
        f'<link rel="stylesheet" href="{_esc(asset_prefix)}styles.css"></head><body>'
        '<header class="nav"><div class="wrap nav-inner">'
        f'<a class="brand" href="{_esc(asset_prefix)}index.html">Opinion Radar</a>'
        '<nav class="nav-links" aria-label="주요">'
        f'{link("index.html", "검색")}{link("history.html", "기록")}{link("report.html", "예시 리포트")}'
        "</nav></div></header>"
    )


def _footer() -> str:
    return (
        '<footer class="site-footer"><div class="wrap"><p>'
        "카드의 투자 함의는 그 출처의 말입니다. 이 리포트의 매수·매도 의견이 아닙니다. "
        "원문에서 다시 찾지 못한 문장은 들어가지 않습니다."
        "</p></div></footer>"
    )


def _clock(seconds) -> str:
    if seconds is None:
        return ""
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _day(value: str | None) -> str:
    import re
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not match:
        return value or ""
    return f"{int(match.group(2))}월 {int(match.group(3))}일"


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)
