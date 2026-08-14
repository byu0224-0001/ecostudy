"""후보 풀 규칙 검사.

이 검사기가 지키는 것은 코드 품질이 아니라 모임의 형식이다. 후보에 결론이
들어가기 시작하면 세션은 토론이 아니라 발표가 되고, 그 변화는 한 번에
일어나지 않고 한 항목씩 스며든다. 그래서 사람이 아니라 코드가 막는다.
"""

import unittest

from core import topics


def valid_topic(**overrides) -> dict:
    base = {
        "id": "T-999",
        "axis": 1,
        "judgment": "국면",
        "title": "예시",
        "question": "지금 무엇이 달라졌나",
        "why_now": [{"text": "국고 10년 4.3%", "source": "KRX bond_kts"}],
        "splits": ["이렇게 볼 수 있다", "저렇게 볼 수도 있다"],
        "data": {"ready": "full", "series": [{"source": "krx", "dataset": "bond_kts"}]},
        "output": "내 기준을 한 문장으로",
        "prep": 3,
        "origin": "지표 스캔",
        "status": "candidate",
    }
    base.update(overrides)
    return base


def issues_for(topic: dict) -> list[str]:
    backlog = topics.Backlog(topics=[topic])
    return [i.message for i in topics.validate(backlog)
            if i.topic == topic.get("id", "?")]


class TestConclusionRejected(unittest.TestCase):
    """가장 중요한 검사. 후보 단계에서 답을 적으면 안 된다."""

    def test_english_conclusion_fields(self):
        for f in ("conclusion", "tldr", "verdict", "recommendation", "takeaway"):
            with self.subTest(field=f):
                found = issues_for(valid_topic(**{f: "금리가 이긴다"}))
                self.assertTrue(any("결론 필드" in m for m in found), f)

    def test_korean_conclusion_fields(self):
        for f in ("결론", "요약", "전망"):
            with self.subTest(field=f):
                found = issues_for(valid_topic(**{f: "하락 전환"}))
                self.assertTrue(any("결론 필드" in m for m in found), f)

    def test_clean_topic_has_no_issues(self):
        self.assertEqual(issues_for(valid_topic()), [])


class TestSplits(unittest.TestCase):
    def test_single_split_rejected(self):
        found = issues_for(valid_topic(splits=["금리가 이긴다"]))
        self.assertTrue(any("splits" in m for m in found))

    def test_personal_axis_may_have_one_split(self):
        found = issues_for(valid_topic(
            axis=4, judgment="리스크", splits=["개인별로 갈린다"],
            data={"ready": "external", "note": "운영자는 벤치마크만 준비"},
        ))
        self.assertEqual(found, [])

    def test_personal_axis_must_say_what_operator_prepares(self):
        found = issues_for(valid_topic(
            axis=4, judgment="리스크", splits=["개인별로 갈린다"],
            data={"ready": "external"},
        ))
        self.assertTrue(any("운영자" in m for m in found))


class TestWhyNow(unittest.TestCase):
    def test_bare_string_has_no_source(self):
        found = issues_for(valid_topic(why_now=["금리가 올랐다"]))
        self.assertTrue(any("출처" in m for m in found))

    def test_dict_without_source_rejected(self):
        found = issues_for(valid_topic(why_now=[{"text": "금리가 올랐다"}]))
        self.assertTrue(any("source" in m for m in found))


class TestDataReadiness(unittest.TestCase):
    def test_full_requires_series(self):
        found = issues_for(valid_topic(data={"ready": "full"}))
        self.assertTrue(any("series" in m for m in found))

    def test_partial_must_state_what_is_missing(self):
        found = issues_for(valid_topic(data={
            "ready": "partial", "series": [{"source": "krx"}]}))
        self.assertTrue(any("missing" in m for m in found))

    def test_partial_with_missing_is_fine(self):
        found = issues_for(valid_topic(data={
            "ready": "partial", "series": [{"source": "krx"}],
            "missing": "기재부 발행계획"}))
        self.assertEqual(found, [])

    def test_unknown_readiness_rejected(self):
        found = issues_for(valid_topic(data={"ready": "maybe"}))
        self.assertTrue(any("data.ready" in m for m in found))


class TestQuestionForm(unittest.TestCase):
    def test_statement_rejected(self):
        found = issues_for(valid_topic(question="금리가 올라서 주가가 빠졌다."))
        self.assertTrue(any("질문 형태" in m for m in found))

    def test_period_after_interrogative_is_fine(self):
        self.assertEqual(issues_for(valid_topic(question="지금 무엇이 달라졌나.")), [])

    def test_question_mark_is_fine(self):
        self.assertEqual(issues_for(valid_topic(question="지금 무엇이 달라졌나?")), [])


class TestFieldConstraints(unittest.TestCase):
    def test_axis_out_of_range(self):
        self.assertTrue(any("axis" in m for m in issues_for(valid_topic(axis=7))))

    def test_unknown_judgment(self):
        found = issues_for(valid_topic(judgment="느낌"))
        self.assertTrue(any("judgment" in m for m in found))

    def test_missing_required_field(self):
        t = valid_topic()
        del t["output"]
        self.assertTrue(any("output" in m for m in issues_for(t)))

    def test_duplicate_ids(self):
        backlog = topics.Backlog(topics=[valid_topic(), valid_topic()])
        self.assertTrue(any("중복" in i.message for i in topics.validate(backlog)))


class TestPoolCoverage(unittest.TestCase):
    """한 축만 채워두면 아무리 잘 골라도 결국 그 축만 나온다."""

    def test_missing_axes_reported(self):
        backlog = topics.Backlog(topics=[valid_topic()])
        missing = [i.message for i in topics.validate(backlog) if i.topic == "풀 전체"]
        self.assertEqual(len(missing), 5)

    def test_dropped_topics_do_not_count_as_coverage(self):
        backlog = topics.Backlog(topics=[
            valid_topic(id="T-1"),
            valid_topic(id="T-2", axis=4, judgment="리스크", status="dropped"),
        ])
        self.assertEqual(topics.coverage(backlog)[4], 0)


def captured(**overrides) -> dict:
    base = {
        "id": "N-999",
        "headline": "장기금리 급등, 재정 우려",
        "claim": "30년 금리 상승의 주된 원인은 국채 발행 증가다",
        "source": "예시 기사",
        "seen": "2026-08-13",
        "origin": "뉴스",
        "verify": "krx bond_kts 커브",
        "check": None,
        "status": "raw",
    }
    base.update(overrides)
    return base


def inbox_issues(item: dict) -> list[str]:
    return [i.message for i in topics.validate_inbox(topics.Inbox(items=[item]))]


class TestCapture(unittest.TestCase):
    """포착 단계에서 거르는 것이 가장 싸다."""

    def test_clean_capture_passes(self):
        self.assertEqual(inbox_issues(captured()), [])

    def test_unfalsifiable_claim_is_rejected_at_intake(self):
        # verify 를 못 채우는 주장은 탈락 조건 2에 이미 걸려 있다
        found = inbox_issues(captured(verify=None))
        self.assertTrue(any("verify" in m for m in found))

    def test_claim_is_required_not_just_headline(self):
        found = inbox_issues(captured(claim=None))
        self.assertTrue(any("claim" in m for m in found))

    def test_source_is_required(self):
        found = inbox_issues(captured(source=None))
        self.assertTrue(any("source" in m for m in found))

    def test_unknown_check_result(self):
        found = inbox_issues(captured(check="아마도"))
        self.assertTrue(any("check" in m for m in found))

    def test_unverifiable_must_be_dropped(self):
        found = inbox_issues(captured(check="unverifiable", status="raw"))
        self.assertTrue(any("dropped" in m for m in found))

    def test_unverifiable_and_dropped_is_fine(self):
        self.assertEqual(
            inbox_issues(captured(check="unverifiable", status="dropped")), [])

    def test_contradicts_stays_raw_until_promoted(self):
        self.assertEqual(inbox_issues(captured(check="contradicts")), [])


class TestInboxHelpers(unittest.TestCase):
    def test_next_id_increments(self):
        inbox = topics.Inbox(items=[captured(id="N-001"), captured(id="N-007")])
        self.assertEqual(inbox.next_id(), "N-008")

    def test_next_id_on_empty_inbox(self):
        self.assertEqual(topics.Inbox().next_id(), "N-001")

    def test_next_id_ignores_malformed(self):
        inbox = topics.Inbox(items=[captured(id="N-002"), captured(id="주제")])
        self.assertEqual(inbox.next_id(), "N-003")

    def test_pending_excludes_checked(self):
        inbox = topics.Inbox(items=[
            captured(id="N-001", check="contradicts"),
            captured(id="N-002"),
        ])
        self.assertEqual([i["id"] for i in inbox.pending()], ["N-002"])

    def test_roundtrip_preserves_korean(self, ):
        import tempfile
        from pathlib import Path
        inbox = topics.Inbox(items=[captured()])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "inbox.yaml"
            topics.save_inbox(inbox, path)
            again = topics.load_inbox(path)
        self.assertEqual(again.items[0]["claim"], inbox.items[0]["claim"])

    def test_missing_file_gives_empty_inbox(self):
        from pathlib import Path
        self.assertEqual(topics.load_inbox(Path("/nonexistent/inbox.yaml")).items, [])


class TestRealBacklog(unittest.TestCase):
    """저장소에 실제로 들어 있는 후보 풀이 규칙을 지키는지."""

    def test_backlog_is_clean(self):
        backlog = topics.load()
        found = topics.validate(backlog)
        self.assertEqual(found, [], "\n".join(f"{i.topic}: {i.message}" for i in found))

    def test_every_axis_has_a_candidate(self):
        for axis, n in topics.coverage(topics.load()).items():
            self.assertGreater(n, 0, f"{axis}축 후보 없음")

    def test_lookup_is_case_insensitive(self):
        self.assertIsNotNone(topics.load().get("t-001"))


def promoted(**overrides) -> dict:
    kw = {
        "topic_id": "T-001",
        "axis": 4,
        "judgment": "리스크",
        "question": "내 포트폴리오에서 무엇이 먼저 무너지나",
        "splits": ["되돌림이 남았다", "실적이 받친다"],
        "output": "유지할 것과 줄일 것",
        "ready": "partial",
        "series": [{"source": "krx", "dataset": "bond_kts"}],
    }
    kw.update(overrides)
    item = captured(check="contradicts", check_note="커브가 뒤쪽만 들렸다")
    return topics.promote(item, **kw)


class TestPromote(unittest.TestCase):
    """승격은 확인한 근거를 옮기고, 판단은 사람에게 남긴다."""

    def test_evidence_is_carried_forward(self):
        topic = promoted()
        texts = [w["text"] for w in topic["why_now"]]
        self.assertIn(captured()["claim"], texts)
        self.assertIn("커브가 뒤쪽만 들렸다", texts)
        self.assertTrue(any("contradicts" in w["source"] for w in topic["why_now"]))
        self.assertEqual(topic["links"], ["N-999"])

    def test_result_passes_the_format_contract(self):
        self.assertEqual(issues_for(promoted()), [])

    def test_single_split_is_still_caught(self):
        found = issues_for(promoted(splits=["한쪽뿐"]))
        self.assertTrue(any("splits" in m for m in found), found)

    def test_partial_without_series_is_still_caught(self):
        found = issues_for(promoted(series=[]))
        self.assertTrue(any("series" in m for m in found), found)

    def test_no_conclusion_field_sneaks_in(self):
        self.assertFalse(set(promoted()) & topics.BANNED_FIELDS)

    def test_every_axis_has_a_framing(self):
        self.assertEqual(set(topics.FRAMINGS), set(topics.AXES))

    def test_next_id_continues_the_sequence(self):
        backlog = topics.Backlog(topics=[{"id": "T-003"}, {"id": "T-011"}])
        self.assertEqual(topics.next_topic_id(backlog), "T-012")
        self.assertEqual(topics.next_topic_id(topics.Backlog()), "T-001")


class TestSeriesSpec(unittest.TestCase):
    """세 번째 조각의 이름이 소스마다 다르다."""

    def test_ecos_uses_item_codes(self):
        self.assertEqual(
            topics.parse_series("ecos:901Y055:S22CC:VA"),
            {"source": "ecos", "dataset": "901Y055", "items": ["S22CC", "VA"]},
        )

    def test_krx_uses_a_column(self):
        self.assertEqual(
            topics.parse_series("krx:index_kospi:CLSPRC_IDX"),
            {"source": "krx", "dataset": "index_kospi", "field": "CLSPRC_IDX"},
        )

    def test_dataset_alone_is_enough(self):
        self.assertEqual(
            topics.parse_series("dart:financials"),
            {"source": "dart", "dataset": "financials"},
        )

    def test_bare_source_is_rejected(self):
        with self.assertRaises(ValueError):
            topics.parse_series("ecos")


class TestBacklogRoundTrip(unittest.TestCase):
    """축별로 파일을 다시 쓰므로 기존 항목이 변형되면 안 된다."""

    def test_nothing_is_lost_or_changed(self):
        import tempfile
        from pathlib import Path

        original = topics.load()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "backlog.yaml"
            topics.save(original, path)
            reloaded = topics.load(path)

        self.assertEqual(reloaded.meta, original.meta)
        self.assertEqual(reloaded.books, original.books)
        before = {t["id"]: t for t in original.topics}
        self.assertEqual({t["id"] for t in reloaded.topics}, set(before))
        for topic in reloaded.topics:
            self.assertEqual(topic, before[topic["id"]], topic["id"])
        self.assertEqual(topics.validate(reloaded), [])


if __name__ == "__main__":
    unittest.main()
