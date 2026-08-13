"""ECOS time parsing and the credential scrubbing it forced us to add.

No network here. These cover the two things that are easy to get wrong and
expensive to notice late: period alignment (a month-start index silently
breaks year-on-year comparisons) and secrets reaching the cache.
"""

import os
import unittest

import pandas as pd

from core import config
from core.sources.ecos import _period_end


class TestPeriodEnd(unittest.TestCase):
    """ECOS TIME strings must land on the last day of their period, because
    the transform layer only uses period-space arithmetic when it sees a
    month-end aligned index."""

    def test_monthly(self):
        self.assertEqual(_period_end("202602", "M"), pd.Timestamp("2026-02-28"))
        self.assertEqual(_period_end("202402", "M"), pd.Timestamp("2024-02-29"))
        self.assertEqual(_period_end("202612", "M"), pd.Timestamp("2026-12-31"))

    def test_daily(self):
        self.assertEqual(_period_end("20260813", "D"), pd.Timestamp("2026-08-13"))

    def test_quarterly_both_spellings(self):
        self.assertEqual(_period_end("2026Q1", "Q"), pd.Timestamp("2026-03-31"))
        self.assertEqual(_period_end("20261", "Q"), pd.Timestamp("2026-03-31"))
        self.assertEqual(_period_end("2026Q4", "Q"), pd.Timestamp("2026-12-31"))

    def test_annual_and_half(self):
        self.assertEqual(_period_end("2025", "A"), pd.Timestamp("2025-12-31"))
        self.assertEqual(_period_end("20251", "S"), pd.Timestamp("2025-06-30"))
        self.assertEqual(_period_end("20252", "S"), pd.Timestamp("2025-12-31"))

    def test_garbage_returns_none_rather_than_raising(self):
        # a single unparseable row should drop out, not kill the whole fetch
        self.assertIsNone(_period_end("", "M"))
        self.assertIsNone(_period_end("2026", "M"))
        self.assertIsNone(_period_end("nonsense", "D"))

    def test_monthly_index_stays_month_end_aligned(self):
        dates = [_period_end(f"2026{m:02d}", "M") for m in range(1, 13)]
        index = pd.DatetimeIndex(dates)
        self.assertTrue((index == index + pd.offsets.MonthEnd(0)).all())


class TestScrub(unittest.TestCase):
    """ECOS puts the API key in the URL path, so query-string redaction alone
    is not enough."""

    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["TEST_FAKE_API_KEY"] = "abcdef0123456789"
        os.environ["TEST_FAKE_TOKEN"] = "abcdef0123456789EXTRA"
        config._secret_values.cache_clear() if hasattr(
            config._secret_values, "cache_clear") else None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_removes_key_from_url_path(self):
        url = "https://ecos.bok.or.kr/api/StatisticSearch/abcdef0123456789/json/kr"
        self.assertNotIn("abcdef0123456789", config.scrub(url))
        self.assertIn("<redacted>", config.scrub(url))

    def test_longest_secret_replaced_whole(self):
        # the short key is a prefix of the long one; the long one must not be
        # left as a dangling fragment
        out = config.scrub("x=abcdef0123456789EXTRA")
        self.assertNotIn("EXTRA", out)

    def test_short_values_are_not_treated_as_secrets(self):
        os.environ["TEST_FAKE_PW"] = "abc"
        self.assertIn("abc", config.scrub("path/abc/here"))

    def test_non_secret_env_vars_are_left_alone(self):
        os.environ["TEST_HARMLESS_NAME"] = "somethinglongenough"
        self.assertIn("somethinglongenough", config.scrub("v=somethinglongenough"))

    def test_empty_input(self):
        self.assertEqual(config.scrub(""), "")


class TestRedact(unittest.TestCase):
    def test_hints_cover_the_credentials_we_actually_send(self):
        out = config.redact({
            "crtfc_key": "x", "serviceKey": "y", "AUTH_KEY": "z",
            "authToken": "w", "stat_code": "722Y001",
        })
        self.assertEqual(out["stat_code"], "722Y001")
        for field in ("crtfc_key", "serviceKey", "AUTH_KEY", "authToken"):
            self.assertEqual(out[field], "<redacted>", field)


if __name__ == "__main__":
    unittest.main()
