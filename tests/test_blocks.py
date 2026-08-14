"""블록 분해와 권한.

여기서 지키는 것은 파싱 정확도가 아니라 두 가지 원칙이다. 협찬 글을
근거로 쓰지 않는다는 것, 그리고 그 판단을 모델이 못 바꾼다는 것.
"""

from __future__ import annotations

from core import blocks


HTML = """
<html><body>
<style>.x{color:red}</style>
<h2>🔎 핵심만 콕콕</h2>
<p>코스닥지수가 이달 들어 18.72% 올라 전 세계 상승률 1위를 기록했습니다.</p>
<p>기관이 반등을 이끌었으며 제약·바이오가 인기를 끌었는데요.</p>
<h2>📆 일정</h2>
<p>미국의 7월 생산자물가지수(PPI)가 발표돼요.</p>
<h2>광고</h2>
<p>지금 가입하면 첫 달 무료! <a href="https://sponsor.example/buy">신청하기</a></p>
<p><a href="https://track.example/open?id=1">.</a></p>
<p><a href="https://list.example/unsubscribe">수신거부</a></p>
</body></html>
"""


def test_splits_on_headings():
    got = blocks.split(HTML)
    heads = [b.heading for b in got]
    assert "핵심만 콕콕" in heads
    assert "일정" in heads


def test_body_follows_its_heading():
    got = {b.heading: b.body for b in blocks.split(HTML)}
    assert "18.72%" in got["핵심만 콕콕"]
    assert "PPI" in got["일정"]


def test_style_and_script_are_gone():
    text = " ".join(b.text for b in blocks.split(HTML))
    assert "color:red" not in text


def test_tracking_and_unsubscribe_links_dropped():
    links = [url for b in blocks.split(HTML) for url in b.links]
    assert not any("track.example" in u for u in links)
    assert not any("unsubscribe" in u for u in links)
    assert any("sponsor.example" in u for u in links), \
        "광고주 링크는 남아야 성격 판단의 단서가 된다"


def test_permissions_come_from_policy_not_the_model():
    """모델은 성격만 고른다. 무엇에 쓸 수 있는지는 코드가 정한다."""
    b = blocks.Block(index=0, heading="", body="x")
    b.content_type = "sponsored"
    assert b.discovery_allowed, "협찬 글도 실마리로는 쓴다"
    assert not b.evidence_allowed, "파는 사람이 쓴 글을 근거로 쓰지 않는다"

    b.content_type = "footer"
    assert not b.discovery_allowed and not b.evidence_allowed


def test_calendar_informs_but_does_not_prove():
    b = blocks.Block(index=0, heading="", body="x")
    b.content_type = "calendar"
    assert b.discovery_allowed and not b.evidence_allowed


def test_unknown_defaults_to_cautious():
    b = blocks.Block(index=0, heading="", body="x")
    assert b.content_type == "unknown"
    assert b.discovery_allowed and not b.evidence_allowed, \
        "분류 실패한 블록을 근거로 쓰면 안 된다"


def test_every_content_type_has_a_policy():
    assert set(blocks.POLICY) == set(blocks.CONTENT_TYPES)


def test_no_type_grants_evidence_without_discovery():
    for name, (discover, evidence) in blocks.POLICY.items():
        assert not (evidence and not discover), \
            f"{name}: 근거로 쓰는데 발견에 못 쓰는 조합은 말이 안 된다"


def test_long_block_is_chunked_at_line_boundaries():
    line = "코스닥이 3% 올랐습니다. " * 30
    html = "<h2>제목</h2>" + "".join(f"<p>{line}</p>" for _ in range(6))
    got = blocks.split(html)
    assert len(got) > 1
    assert all(len(b.body) <= blocks.MAX_BLOCK_CHARS + len(line) for b in got)


def test_marks_ui_residue():
    b = blocks.Block(index=0, heading="", body="답변 보기", links=[])
    bs = blocks.mark_ui_residue([b])
    assert bs[0].content_type == "ui_residue"


def test_keeps_short_news_with_numbers():
    b = blocks.Block(index=0, heading="", body="美 CPI 3.4% 상승", links=[])
    assert not blocks.is_ui_residue(b)


def test_keeps_uppity_header_with_market_numbers():
    b = blocks.Block(
        index=1, heading="",
        body="2026. 8. 13. | 구독하기 | 코스피 6,579.04 ▲ 3.68%",
        links=[],
    )
    assert not blocks.is_ui_residue(b)


def test_render_skips_ui_residue():
    bs = [
        blocks.Block(0, "", "답변 보기"),
        blocks.Block(1, "핵심", "코스닥 3% 상승"),
    ]
    bs[0].content_type = "ui_residue"
    rendered = blocks.render(bs)
    assert "답변 보기" not in rendered
    assert "[1]" in rendered
