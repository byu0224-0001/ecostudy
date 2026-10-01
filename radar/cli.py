import argparse
import json
import sys
from pathlib import Path

from radar.collect import collect_articles, collect_videos, fetch_caption_segments
from radar.config import load_settings, missing_key_names
from radar.extract import make_extractor
from radar.html import render_report
from radar.pipeline import build_brief, expand_queries
from radar.store import connect, save_brief


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
        run_server(Path(args.root), args.port)
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
    database = connect(settings.root / "data" / "radar.sqlite")
    save_brief(database, report)
    print(f"id: {report['id']}", file=sys.stderr)
    return 0


def run_brief(keyword: str, *, days: int, max_videos: int, max_articles: int, settings, skip_youtube: bool = False) -> dict:
    queries = expand_queries(keyword)
    articles, skipped = collect_articles(keyword, days, settings, queries)
    videos = []
    if skip_youtube:
        skipped.append("youtube: 건너뜀")
    else:
        found, video_skips = collect_videos(queries, days, settings)
        videos.extend(found)
        skipped.extend(video_skips)

    def captions(video_id: str):
        try:
            return fetch_caption_segments(video_id)
        except Exception:
            return None

    return build_brief(
        keyword,
        days=days,
        max_videos=max_videos,
        max_articles=max_articles,
        articles=articles,
        videos=videos,
        caption_fn=captions,
        extractor=make_extractor(settings),
        skipped=skipped,
    )


def _relative_prefix(from_dir: Path, web_dir: Path) -> str:
    relative = Path(os_relpath(web_dir, from_dir))
    text = relative.as_posix()
    if text == ".":
        return ""
    return text.rstrip("/") + "/"


def os_relpath(path: Path, start: Path) -> str:
    import os
    return os.path.relpath(path, start)
