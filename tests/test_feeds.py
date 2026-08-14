"""피드 파싱과 반복어 추출.

네트워크는 타지 않는다. 여기서 검사하는 것은 두 가지다. Atom 과 RSS 를
같은 Item 으로 뽑아내는지, 그리고 뉴스 RSS 의 물량이 유튜브 채널 신호를
덮지 않는지. 두 번째가 실제로 났던 문제다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import feeds

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>슈카월드</title>
  <entry>
    <title>엔화 방어 작전</title>
    <link rel="alternate" href="https://youtu.be/aaa"/>
    <published>2026-08-12T09:00:00+00:00</published>
  </entry>
  <entry>
    <title>반도체 순환매</title>
    <link rel="alternate" href="https://youtu.be/bbb"/>
    <published>2026-08-11T09:00:00+00:00</published>
  </entry>
</feed>"""

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>연합뉴스</title>
  <item>
    <title>엔화 급등에 수출주 희비</title>
    <link>https://yna.co.kr/1</link>
    <pubDate>Wed, 12 Aug 2026 09:00:00 +0900</pubDate>
  </item>
</channel></rss>"""


def _feed(fid="f", layer="통념"):
    return {"id": fid, "layer": layer, "name": fid}


def test_atom_parsed():
    items = feeds._parse(ATOM, _feed("syuka"))
    assert [i.title for i in items] == ["엔화 방어 작전", "반도체 순환매"]
    assert items[0].url == "https://youtu.be/aaa"
    assert items[0].published.year == 2026


def test_rss_parsed():
    items = feeds._parse(RSS, _feed("yna"))
    assert len(items) == 1
    assert items[0].published.tzinfo is not None


def test_broken_xml_names_the_feed():
    with pytest.raises(Exception) as err:
        feeds._parse("<feed><entry>", _feed("syuka"))
    assert "syuka" in str(err.value)


def _item(feed, title, days_ago=0):
    return feeds.Item(
        feed=feed, name=feed, layer="통념", title=title, url="",
        published=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


def test_theme_needs_more_than_one_feed():
    """한 채널이 열 번 말하는 것은 통념이 아니라 그 채널의 관심사다."""
    items = [_item("syuka", "엔화 이야기") for _ in range(10)]
    assert feeds.themes(items) == []

    items.append(_item("3pro", "엔화 방어"))
    assert [w for w, _, _ in feeds.themes(items)] == ["엔화"]


def test_news_volume_does_not_drown_channel_signal():
    """실제로 났던 문제. 연합 160건이 유튜브 35건을 덮어 상투어가 올라왔다."""
    flood = [_item("yna", f"코스피 특징주 리포트 {i}") for i in range(200)]
    flood += [_item("hankyung", f"코스피 마감 시황 {i}") for i in range(200)]
    signal = [_item("syuka", "엔화 방어 작전"), _item("3pro", "엔화 구출")]

    words = [w for w, _, _ in feeds.themes(flood + signal, cap=25)]
    assert "엔화" in words


def test_stopwords_drop_generic_finance_words():
    items = [_item("syuka", "투자 전망 상승"), _item("3pro", "투자 전망 상승")]
    assert feeds.themes(items) == []


def test_title_repeats_within_one_item_count_once_per_feed():
    items = [_item("syuka", "엔화 엔화 엔화"), _item("3pro", "엔화")]
    word, n_feeds, n = feeds.themes(items)[0]
    assert (word, n_feeds, n) == ("엔화", 2, 2)


def test_persistence_needs_distinct_days_not_wide_windows():
    """처음에 틀렸던 부분. 오늘 올라온 항목은 2일 창에도 30일 창에도 들어가서
    시간창 소속으로 재면 모든 단어가 같은 점수를 받는다."""
    same_day = [_item("syuka", "엔화 방어", 0), _item("3pro", "엔화 구출", 0)]
    spread = [_item("syuka", "일본 금리", 0), _item("3pro", "일본 부채", 4),
              _item("understanding", "일본 재정", 9)]

    rows = {p.word: p for p in feeds.persistence(same_day + spread)}
    assert rows["엔화"].days == 1
    assert rows["일본"].days == 3
    # 지속되는 쪽이 위로 온다
    assert feeds.persistence(same_day + spread)[0].word == "일본"


def test_persistence_ignores_undated_items():
    undated = feeds.Item(feed="f", name="f", layer="통념", title="엔화",
                         url="u", published=None)
    assert feeds.persistence([undated, _item("g", "엔화")]) == []


def test_record_appends_only_unseen_urls(tmp_path):
    store = tmp_path / "seen.jsonl"
    first = [_item("syuka", "엔화 방어"), _item("3pro", "반도체")]
    for n, it in enumerate(first):
        it.url = f"https://x/{n}"

    assert feeds.record(first, path=store) == 2
    assert feeds.record(first, path=store) == 0

    fresh = _item("syuka", "새 영상")
    fresh.url = "https://x/9"
    assert feeds.record(first + [fresh], path=store) == 1
    assert len(feeds.history(30, path=store)) == 3


def test_history_round_trips_layer_and_time(tmp_path):
    store = tmp_path / "seen.jsonl"
    item = _item("syuka", "엔화 방어", days_ago=3)
    item.url = "https://x/1"
    feeds.record([item], path=store)

    back = feeds.history(30, path=store)[0]
    assert (back.feed, back.layer, back.title) == ("syuka", "통념", "엔화 방어")
    assert 2.9 < back.age_days < 3.1


def test_history_drops_items_past_the_horizon(tmp_path):
    store = tmp_path / "seen.jsonl"
    old = _item("syuka", "작년 이야기", days_ago=100)
    old.url = "https://x/old"
    feeds.record([old], path=store)
    assert feeds.history(30, path=store) == []
    assert len(feeds.history(365, path=store)) == 1


def test_sources_file_declares_every_layer_it_uses():
    data = feeds.load_sources()
    declared = set(data["layers"])
    used = {f["layer"] for f in data["feeds"]}
    assert used <= declared
    for feed in data["feeds"]:
        assert feed.get("kind") in {"youtube", "rss", "atom"}
        assert feed.get("handle") or feed.get("url")
