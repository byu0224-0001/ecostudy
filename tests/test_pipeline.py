import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar.collect import articles_from_pages, html_to_text, mix_topic_links, page_title, parse_google_cse, select_naver_links
from radar.extract import grounding_links
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
        fuzzy_source = "10년물 국채금리와 금리 인상 가능성이 낮아졌다. 다른 문장도 있다."
        fuzzy = keep_grounded(
            [{"quote": "10년물 국채금리 와 금리 인상 가능성 이 낮아졌다", "opinion": "하락", "stance": "down"}],
            fuzzy_source,
        )
        self.assertEqual(fuzzy[0]["quote_status"], "fuzzy")
        self.assertEqual(fuzzy[0]["quote"], "10년물 국채금리와 금리 인상 가능성이 낮아졌다.")


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
        self.assertIn("국채 발행이 늘어 금리는 상승 압력이 있다.", page)
        self.assertIn(">인용</a>", page)

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
        self.assertIn("미국 국채 금리 칼럼", queries)
        self.assertIn("미국 국채 금리 재정", queries)
        self.assertIn("US treasury yield", queries)
        self.assertLessEqual(len(queries), 8)


class HtmlFileTests(unittest.TestCase):
    def test_sample_pages_share_shell(self):
        web = Path(__file__).resolve().parents[1] / "web"
        for name in ("index.html", "report.html", "history.html", "empty.html", "progress.html"):
            text = (web / name).read_text(encoding="utf-8")
            self.assertIn("pretendard", text)
            self.assertNotIn("Manrope", text)
            self.assertIn("styles.css", text)
            self.assertIn("Opinion Radar", text)
        report = (web / "report.html").read_text(encoding="utf-8")
        self.assertIn("video-card", report)
        self.assertIn("다른 영상과 다른 점", report)
        self.assertNotIn("json.loads", report)


class ArticleBodyTests(unittest.TestCase):
    def test_paragraphs_become_text_and_short_pages_are_dropped(self):
        html = "<html><title>금리 칼럼 | 신문</title><p>" + ("국채 발행이 늘어 금리는 상승 압력이 있다. " * 8) + "</p></html>"
        self.assertIn("상승 압력", html_to_text(html))
        links = [{"title": "news.example", "url": "https://news.example/a"}, {"title": "short", "url": "https://news.example/b"}]

        def fetch(url):
            if url.endswith("/b"):
                return url, "<html><title>짧음</title><p>한 줄.</p></html>"
            return "https://news.example/a", html

        articles, skipped = articles_from_pages(links, fetch, limit=4)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["publisher"], "news.example")
        self.assertEqual(articles[0]["section"], "column")
        self.assertIn("본문 확인 실패", skipped[0])
        dated = "<html><title>금리</title><p>" + ("국채 발행이 늘어 금리는 상승 압력이 있다. " * 8) + "</p></html>"
        dated_articles, _notes = articles_from_pages(
            [{"title": "금리", "url": "http://news.example/c", "published_at": "2026-10-01"}],
            lambda _url: ("https://news.example/c", dated),
            limit=1,
        )
        self.assertEqual(dated_articles[0]["published_at"], "2026-10-01")
        sidebar = "<p>" + ("사이드 금리 상승 압력 문장입니다. " * 8) + "</p>"
        body = "<div class=\"article-body\" itemprop=\"articleBody\">" + ("미국 30년 만기 국채금리가 최고치를 경신했다. " * 8) + "</div>"
        text = html_to_text(sidebar + body)
        self.assertIn("최고치를 경신", text)
        self.assertNotIn("사이드", text)
        byline = "<div class=\"article-body\">" + ("(서울=연합뉴스) 김가 기자 = 국채 발행이 늘어 금리는 상승 압력이 있다. " * 6) + "</div>"
        self.assertNotIn("기자", html_to_text(byline))
        self.assertIn("상승 압력", html_to_text(byline))
        noisy = "<div class=\"article-body\">" + ("미국 국채 금리가 5%로 올랐다. " * 8) + "관련기사 재배포 금지 by Taboola 실시간 급상승 뉴스</div>"
        cleaned = html_to_text(noisy)
        self.assertIn("5%", cleaned)
        self.assertNotIn("Taboola", cleaned)
        self.assertNotIn("급상승", cleaned)
        credited = "<div class=\"article-body\">" + ("/사진=뉴시스 미국 국채 금리가 장중 5%를 넘었다. " * 6) + "</div>"
        self.assertNotIn("사진=", html_to_text(credited))
        self.assertIn("5%", html_to_text(credited))
        self.assertEqual(page_title("<title>금리 쇼크 :: 공감언론 뉴시스 ::</title>"), "금리 쇼크")
        self.assertEqual(page_title("<title>미국 국채금리 상승이 우리 경제에 미치는 영향 < 기고 < 오피니언</title>"), "미국 국채금리 상승이 우리 경제에 미치는 영향")

    def test_naver_selection_keeps_overlapping_titles(self):
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        items = [
            {"title": "블랙록 3분기 실적", "canonical_url": "https://a.example/1", "published_at": "2026-10-03", "text": "블랙록"},
            {"title": "인플레에 美 국채 시장 최악의 한 달", "canonical_url": "https://b.example/2", "published_at": "2026-10-02", "text": "국채"},
            {"title": "떨어지는 칼날 美 국채 5%", "canonical_url": "https://c.example/3", "published_at": "2026-10-01", "text": "국채 금리"},
            {"title": "고 금리 시대 주식시장", "canonical_url": "https://d.example/4", "published_at": "2026-10-03", "text": "금리"},
            {"title": "오래된 미국 국채 금리", "canonical_url": "https://e.example/5", "published_at": "2026-08-01", "text": "미국 국채 금리"},
            {"title": "美주담대 금리 7%", "canonical_url": "https://f.example/6", "published_at": "2026-10-01", "text": "미국 금리"},
        ]
        urls = [item["canonical_url"] for item in select_naver_links(items, "미국 국채 금리", 14, now)]
        self.assertEqual(urls[0], "https://c.example/3")
        self.assertIn("https://b.example/2", urls)
        self.assertNotIn("https://a.example/1", urls)
        self.assertNotIn("https://d.example/4", urls)
        self.assertNotIn("https://e.example/5", urls)
        self.assertNotIn("https://f.example/6", urls)
        duplicated = select_naver_links(
            items + [{
                "title": "떨어지는 칼날 美 국채 5% 재송고",
                "canonical_url": "https://c.example/9",
                "published_at": "2026-10-03",
                "text": "미국 국채 금리",
            }],
            "미국 국채 금리",
            14,
            now,
        )
        dup_urls = [item["canonical_url"] for item in duplicated]
        self.assertIn("https://c.example/3", dup_urls)
        self.assertNotIn("https://c.example/9", dup_urls)
        crowded = [
            {"title": f"미국 국채 금리 기사 {index}", "canonical_url": f"https://a.example/{index}", "published_at": "2026-10-03", "text": "미국 국채 금리"}
            for index in range(6)
        ]
        column = [{"title": "재정 칼럼 미국 국채", "canonical_url": "https://b.example/column", "published_at": "2026-10-02", "text": "미국 국채 금리 재정"}]
        mixed = [item["canonical_url"] for item in mix_topic_links([crowded, column], "미국 국채 금리", 14, now, limit=4)]
        self.assertEqual(mixed[1], "https://b.example/column")

    def test_google_cse_keeps_article_links(self):
        payload = {"items": [
            {"title": "Treasury yields rise", "link": "https://www.ft.com/content/abc", "snippet": "Fiscal supply lifts yields."},
            {"title": "영상", "link": "https://www.youtube.com/watch?v=abc", "snippet": "영상"},
            {"title": "같은 기사", "link": "https://www.ft.com/content/abc", "snippet": "중복은 파서가 그대로 둔다"},
        ]}
        links = parse_google_cse(payload)
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]["canonical_url"], "https://www.ft.com/content/abc")
        self.assertEqual(links[0]["publisher"], "ft.com")

    def test_same_direction_still_splits_the_point(self):
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        articles = [
            {
                "title": "공급이 민다",
                "publisher": "한겨레",
                "canonical_url": "https://example.test/fiscal",
                "published_at": "2026-09-28",
                "section": "column",
                "text": "국채 발행이 늘어 금리는 상승 압력이 있다.",
            },
            {
                "title": "물가가 민다",
                "publisher": "매경",
                "canonical_url": "https://example.test/prices",
                "published_at": "2026-09-27",
                "section": "news",
                "text": "물가가 높아 금리는 상승 압력이 있다. 국채 발행도 함께 늘었다.",
            },
        ]
        report = build_brief("미국 국채 금리", articles=articles, videos=[], caption_fn=lambda _id: None, now=now)
        self.assertTrue(any(issue["status"] == "distinct" for issue in report["issues"]))
        self.assertTrue(all(article.get("delta") for article in report["articles"]))
        page = render_report(report, "")
        self.assertIn("다른 글과 다른 점", page)
        self.assertIn("공급이 민다", page)
        self.assertIn("물가가 민다", page)
        split = build_brief(
            "미국 국채 금리",
            articles=[
                {
                    "title": "연준 경로",
                    "publisher": "한경",
                    "canonical_url": "https://example.test/fed",
                    "published_at": "2026-09-28",
                    "section": "news",
                    "text": "기준금리를 올려 금리는 상승 압력이 있다.",
                },
                {
                    "title": "발행 경로",
                    "publisher": "매경",
                    "canonical_url": "https://example.test/supply",
                    "published_at": "2026-09-27",
                    "section": "column",
                    "text": "국채 발행이 늘어 금리는 상승 압력이 있다.",
                },
            ],
            videos=[],
            caption_fn=lambda _id: None,
            now=now,
        )
        self.assertTrue(any(issue["id"] == "points" for issue in split["issues"]))
        self.assertEqual(split["one_line"], "출처마다 짚는 문장이 다르다.")

    def test_video_understanding_keeps_a_card_without_captions(self):
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        videos = [{
            "video_id": "watch11",
            "title": "공급 영상",
            "channel": "데스크",
            "published_at": "2026-09-28",
            "duration_sec": 120,
            "url": "https://youtu.be/watch11",
        }]

        def watch(_video):
            return [{
                "quote": "국채 발행이 늘어 금리는 상승 압력이 있다.",
                "fact": "",
                "interpretation": "",
                "opinion": "공급이 금리를 밀어 올린다.",
                "forecast": "",
                "implication": "",
                "evidence": [],
                "assumptions": [],
                "quote_status": "video",
                "start_sec": 15,
                "stance": "up",
                "heard_scope": "영상 전체",
            }]

        report = build_brief(
            "미국 국채 금리",
            articles=[],
            videos=videos,
            caption_fn=lambda _id: None,
            video_fn=watch,
            now=now,
        )
        self.assertEqual(report["pool"]["youtube_kept"], 1)
        self.assertEqual(report["videos"][0]["caption_status"], "video")
        self.assertIn("모델이 영상 1개를 보고 정리함", " ".join(report["skipped"]))
        page = render_report(report, "")
        self.assertIn("모델이 영상 전체에서 들은 말입니다.", page)
        self.assertIn("0:15", page)

        called = {"n": 0}

        def unused(_video):
            called["n"] += 1
            return None

        captioned = build_brief(
            "미국 국채 금리",
            articles=[],
            videos=videos,
            caption_fn=lambda _id: [{"text": "국채 발행이 늘어 금리는 상승 압력이 있다.", "start": 12}],
            video_fn=unused,
            now=now,
        )
        self.assertEqual(called["n"], 0)
        self.assertEqual(captioned["videos"][0]["caption_status"], "ok")

    def test_video_claims_accept_a_bare_list(self):
        from radar.extract import _video_claims

        payload = {"candidates": [{"content": {"parts": [{"text": json.dumps([
            {"quote": "국채 금리가 공급 때문에 올랐다.", "opinion": "공급이 민다", "stance": "up", "start_sec": 4},
        ])}]}}]}
        claims = _video_claims(payload)
        self.assertEqual(claims[0]["start_sec"], 4)
        self.assertEqual(claims[0]["quote_status"], "video")

    def test_long_videos_stay_beside_short_ones(self):
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        videos = [
            {
                "video_id": "short11",
                "title": "짧은 국채 금리",
                "channel": "짧은채널",
                "published_at": "2026-09-28",
                "duration_sec": 120,
                "url": "https://youtu.be/short11",
            },
            {
                "video_id": "long222",
                "title": "긴 국채 금리 해설",
                "channel": "긴채널",
                "published_at": "2026-09-27",
                "duration_sec": 1002,
                "url": "https://youtu.be/long222",
            },
            {
                "video_id": "tiny333",
                "title": "국채 쇼츠",
                "channel": "쇼츠",
                "published_at": "2026-09-29",
                "duration_sec": 20,
                "url": "https://youtu.be/tiny333",
            },
        ]

        def captions(_video_id):
            return [{"text": "국채 발행이 늘어 금리는 상승 압력이 있다.", "start": 8}]

        report = build_brief("미국 국채 금리", articles=[], videos=videos, caption_fn=captions, now=now, max_videos=6)
        ids = [video["video_id"] for video in report["videos"]]
        self.assertIn("long222", ids)
        self.assertIn("short11", ids)
        self.assertNotIn("tiny333", ids)
        self.assertEqual(report["pool"]["youtube_seen"], 2)

    def test_grounding_keeps_https_chunks_only(self):
        payload = {"candidates": [{"groundingMetadata": {"groundingChunks": [
            {"web": {"uri": "https://news.example/a", "title": "예"}},
            {"web": {"uri": "http://news.example/b", "title": "버림"}},
            {"web": {"uri": "https://news.example/a", "title": "중복"}},
        ]}}]}
        links = grounding_links(payload)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["url"], "https://news.example/a")


if __name__ == "__main__":
    unittest.main()
