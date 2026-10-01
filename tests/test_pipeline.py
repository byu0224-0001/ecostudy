import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar.html import render_report
from radar.pipeline import build_brief, expand_queries
from radar.textutil import keep_grounded, quote_status


class GroundTests(unittest.TestCase):
    def test_missing_quote_is_dropped(self):
        source = "국채 금리는 공급 부담으로 오른다."
        claims = [
            {"quote": "국채 금리는 공급 부담으로 오른다.", "opinion": "오른다", "stance": "up"},
            {"quote": "모델이 지어낸 문장이다.", "opinion": "없음", "stance": "down"},
        ]
        kept = keep_grounded(claims, source)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["quote_status"], "exact")
        self.assertEqual(quote_status("국채  금리는 공급 부담으로 오른다.", source), "fuzzy")
        self.assertEqual(quote_status("없는 말", source), "missing")


class PipelineTests(unittest.TestCase):
    def test_diverse_channel_cap_and_conflict(self):
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        articles = [
            {
                "title": "공급이 민다",
                "publisher": "한겨레",
                "canonical_url": "https://example.test/a",
                "published_at": "2026-09-28",
                "section": "news",
                "text": "국채 발행이 늘어 금리는 상승 압력이 있다.",
            },
            {
                "title": "같은 매체 두 번째",
                "publisher": "한겨레",
                "canonical_url": "https://example.test/a2",
                "published_at": "2026-09-29",
                "section": "news",
                "text": "발행 증가로 금리는 상승한다.",
            },
            {
                "title": "둔화가 흡수한다",
                "publisher": "매경",
                "canonical_url": "https://example.test/b",
                "published_at": "2026-09-27",
                "section": "column",
                "text": "고용 둔화가 깊어 금리는 하락 압력이 있다.",
            },
        ]
        videos = [
            {
                "video_id": "aaa111",
                "title": "재정 영상 <위험>",
                "channel": "커브",
                "published_at": "2026-09-28",
                "duration_sec": 90,
                "url": "https://youtu.be/aaa111",
            },
            {
                "video_id": "aaa222",
                "title": "같은 채널 최신",
                "channel": "커브",
                "published_at": "2026-09-20",
                "duration_sec": 80,
                "url": "https://youtu.be/aaa222",
            },
            {
                "video_id": "bbb333",
                "title": "둔화 영상",
                "channel": "데스크",
                "published_at": "2026-09-26",
                "duration_sec": 70,
                "url": "https://youtu.be/bbb333",
            },
        ]

        def captions(video_id):
            if video_id == "aaa111":
                return [{"text": "국채 발행이 늘어 금리는 상승 압력이 있다.", "start": 12}]
            if video_id == "bbb333":
                return [{"text": "고용 둔화가 깊어 금리는 하락 압력이 있다.", "start": 40}]
            return None

        report = build_brief(
            "미국 국채 금리",
            days=14,
            articles=articles,
            videos=videos,
            caption_fn=captions,
            skipped=["naver: 키 없음"],
            now=now,
        )
        self.assertEqual(report["pool"]["youtube_kept"], 2)
        self.assertTrue(any(issue["status"] == "conflict" for issue in report["issues"]))
        self.assertIn("naver: 키 없음", report["skipped"])
        titles = [video["title"] for video in report["videos"]]
        self.assertIn("재정 영상 <위험>", titles)
        self.assertNotIn("같은 채널 최신", titles)
        page = render_report(report, "")
        self.assertNotIn("<위험>", page)
        self.assertIn("&lt;위험&gt;", page)
        self.assertIn("https://i.ytimg.com/vi/aaa111/hqdefault.jpg", page)
        self.assertIn("다른 영상과 다른 점", page)
        self.assertNotIn("모델이 지어낸", page)

    def test_ungrounded_extractor_drops_the_source(self):
        articles = [{
            "title": "제목",
            "publisher": "통신",
            "canonical_url": "https://example.test/c",
            "published_at": "2026-09-30",
            "section": "news",
            "text": "실제 문장만 여기에 있다.",
        }]

        def liar(_text, _segments=None):
            return [{"quote": "원문에 없는 전망이다.", "opinion": "하락", "stance": "down", "fact": "", "interpretation": "", "forecast": "", "implication": "", "evidence": [], "assumptions": [], "start_sec": None}]

        report = build_brief("테스트", articles=articles, videos=[], caption_fn=lambda _id: None, extractor=liar)
        self.assertEqual(report["articles"], [])
        self.assertEqual(report["one_line"], "인용으로 확인할 문장이 없다.")

    def test_expand_splits_frames(self):
        queries = expand_queries("미국 국채 금리")
        self.assertIn("미국 국채 금리 상승", queries)
        self.assertIn("미국 국채 금리 하락", queries)
        self.assertIn("US treasury yield", queries)


class HtmlFileTests(unittest.TestCase):
    def test_sample_pages_share_shell(self):
        web = Path(__file__).resolve().parents[1] / "web"
        for name in ("index.html", "report.html", "history.html", "empty.html", "progress.html"):
            text = (web / name).read_text(encoding="utf-8")
            self.assertIn("Manrope", text)
            self.assertIn("styles.css", text)
            self.assertIn("Opinion Radar", text)
        report = (web / "report.html").read_text(encoding="utf-8")
        self.assertIn("video-card", report)
        self.assertIn("다른 영상과 다른 점", report)
        self.assertNotIn("json.loads", report)


if __name__ == "__main__":
    unittest.main()
