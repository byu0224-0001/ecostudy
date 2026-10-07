import argparse
import json
import os
import sys
from pathlib import Path

from radar.collect import collect_articles, collect_videos, fetch_caption_segments
from radar.config import load_settings, missing_key_names, on_hosted
from radar.extract import gemini_video_claims, gemini_video_deltas, make_extractor
from radar.html import render_report
from radar.pipeline import build_brief, expand_queries
from radar.store import connect, default_path, save_brief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radar", description="키워드로 기사와 영상 인용을 모읍니다.")
    sub = parser.add_subparsers(dest="command", required=True)

    brief = sub.add_parser("brief", help="리포트 JSON을 만듭니다.")
    brief.add_argument("keyword")
    brief.add_argument("--days", type=int, default=14)
    brief.add_argument("--max-videos", type=int, default=6)
    brief.add_argument("--max-articles", type=int, default=6)
    brief.add_argument("--json", dest="json_path")
    brief.add_argument("--html", dest="html_path")
    brief.add_argument("--skip-youtube", action="store_true")
    brief.add_argument("--root", default=".")

    serve = sub.add_parser("serve", help="검색 화면을 로컬에서 엽니다.")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--root", default=".")

    doctor = sub.add_parser("doctor", help="없는 키 이름만 보여 줍니다.")
    doctor.add_argument("--root", default=".")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        settings = load_settings(Path(args.root))
        missing = missing_key_names(settings)
        if missing:
            print("없는 키: " + ", ".join(missing))
        else:
            print("필요한 키가 모두 있습니다.")
        return 0
    if args.command == "serve":
        from radar.serve import serve as run_server
        run_server(Path(args.root), args.port, args.host)
        return 0
    settings = load_settings(Path(args.root))
    report = run_brief(
        args.keyword,
        days=args.days,
        max_videos=args.max_videos,
        max_articles=args.max_articles,
        settings=settings,
        skip_youtube=args.skip_youtube,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_path:
        Path(args.json_path).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    if args.html_path:
        html_path = Path(args.html_path)
        web = settings.root / "web"
        prefix = _relative_prefix(html_path.parent, web)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(render_report(report, prefix), encoding="utf-8")
        print(f"html: {html_path}", file=sys.stderr)
    database = connect(default_path(settings.root))
    save_brief(database, report)
    print(f"id: {report['id']}", file=sys.stderr)
    return 0


def run_brief(keyword: str, *, days: int, max_videos: int, max_articles: int, settings, skip_youtube: bool = False) -> dict:
    queries = expand_queries(keyword)
    articles, skipped = collect_articles(keyword, days, settings, queries)
    articles = _prefer_body(articles)
    videos = []
    if skip_youtube:
        skipped.append("youtube: 건너뜀")
    else:
        found, video_skips = collect_videos(queries, days, settings)
        videos.extend(found)
        skipped.extend(video_skips)

    def captions(video_id: str):
        if on_hosted():
            return None
        try:
            return fetch_caption_segments(video_id)
        except Exception as exc:
            if "block" in type(exc).__name__.lower() or "blocking requests" in str(exc).lower():
                captions.reason = "blocked"
            return None

    def watch(video: dict):
        if watch.stopped or watch.count >= max_videos:
            return None
        try:
            claims, note = gemini_video_claims(video, settings, keyword)
        except Exception:
            watch.note = "youtube_video: 영상을 열지 못함"
            return None
        if claims:
            watch.count += 1
            return claims
        if note:
            watch.note = note
            if "한도" in note:
                watch.stopped = True
                watch.note = "gemini: 요청 한도로 이후 영상은 보지 않음"
        return None

    watch.count = 0
    watch.note = ""
    watch.stopped = False

    def compare(videos: list[dict]):
        try:
            return gemini_video_deltas(videos, settings)
        except Exception:
            return {}

    return build_brief(
        keyword,
        days=days,
        max_videos=max_videos,
        max_articles=max_articles,
        articles=articles,
        videos=videos,
        caption_fn=captions,
        video_fn=watch if settings.gemini_key else None,
        compare_fn=compare if settings.gemini_key else None,
        extractor=make_extractor(settings),
        skipped=skipped,
    )


def _relative_prefix(from_dir: Path, web_dir: Path) -> str:
    relative = Path(os_relpath(web_dir, from_dir))
    text = relative.as_posix()
    if text == ".":
        return ""
    return text.rstrip("/") + "/"


def _prefer_body(articles: list[dict]) -> list[dict]:
    long = [item for item in articles if len(item.get("text") or "") >= 180]
    return long or articles


def os_relpath(path: Path, start: Path) -> str:
    import os
    return os.path.relpath(path, start)
