"""Transform primitives must be arithmetically correct and preserve provenance.

Run: python3 -m unittest discover -s tests -v
"""

import unittest

import pandas as pd

from core import transform
from core.series import Provenance, Series


def make_series(values, *, start="2024-01-31", freq="ME", series_id="test", unit="원"):
    index = pd.date_range(start=start, periods=len(values), freq=freq)
    return Series(
        id=series_id,
        values=pd.Series(values, index=index, dtype="float64"),
        prov=Provenance(source="test", dataset="unit", label="테스트", unit=unit,
                        frequency="monthly", citation="테스트 출처"),
    )


class TestChangeRates(unittest.TestCase):
    def test_yoy_of_constant_is_zero(self):
        s = make_series([100.0] * 24)
        out = transform.yoy(s)
        self.assertAlmostEqual(out.values.iloc[-1], 0.0, places=9)

    def test_yoy_of_doubling_is_100pct(self):
        # 13 monthly points: month 0 = 100, month 12 = 200
        values = [100.0] * 12 + [200.0]
        s = make_series(values)
        out = transform.yoy(s)
        self.assertAlmostEqual(out.values.iloc[-1], 100.0, places=6)

    def test_yoy_uses_calendar_not_row_count(self):
        # daily data: a naive positional shift(1) would compute a 1-day change
        s = make_series([100.0] * 400, freq="D", start="2025-01-01")
        s.values.iloc[-1] = 110.0
        out = transform.yoy(s)
        self.assertAlmostEqual(out.values.iloc[-1], 10.0, places=6)

    def test_yoy_matches_previous_february_across_a_leap_year(self):
        # Month-end index: 2024-02-29 exists, 2025-02-28 does not line up under
        # plain calendar subtraction. Regression guard for that bug.
        values = [100.0] * 14
        values[1] = 50.0     # 2024-02-29
        values[13] = 75.0    # 2025-02-28
        s = make_series(values, start="2024-01-31", freq="ME")
        out = transform.yoy(s)
        self.assertAlmostEqual(out.values.loc["2025-02-28"], 50.0, places=6)

    def test_mom_matches_previous_month_end_after_a_30_day_month(self):
        # 2024-04-30 minus one calendar month is 2024-03-30, not 2024-03-31.
        s = make_series([10.0, 10.0, 20.0, 40.0], start="2024-01-31", freq="ME")
        out = transform.mom(s)
        self.assertAlmostEqual(out.values.loc["2024-04-30"], 100.0, places=6)

    def test_yoy_is_nan_before_a_year_of_history(self):
        s = make_series([100.0, 101.0, 102.0])
        out = transform.yoy(s)
        self.assertTrue(out.values.isna().all())

    def test_annualized_compounds_correctly(self):
        # +1% over three months annualises to (1.01^4 - 1) = 4.060401%
        values = [100.0, 100.0, 100.0, 101.0]
        s = make_series(values)
        out = transform.annualized(s, months=3)
        self.assertAlmostEqual(out.values.iloc[-1], 4.060401, places=5)

    def test_mom(self):
        s = make_series([100.0, 105.0])
        out = transform.mom(s)
        self.assertAlmostEqual(out.values.iloc[-1], 5.0, places=6)

    def test_diff_returns_absolute_change(self):
        s = make_series([1.0] * 12 + [3.5])
        out = transform.diff(s, years=1)
        self.assertAlmostEqual(out.values.iloc[-1], 2.5, places=6)


class TestDistribution(unittest.TestCase):
    def test_zscore_of_known_sample(self):
        s = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
        out = transform.zscore(s)
        # sample std of 1..5 is 1.5811; (5 - 3) / 1.5811 = 1.2649
        self.assertAlmostEqual(out.values.iloc[-1], 1.264911, places=5)
        self.assertEqual(out.prov.unit, "σ")

    def test_percentile_rank_of_max_is_100(self):
        s = make_series([1.0, 2.0, 3.0, 9.0])
        out = transform.percentile_rank(s)
        self.assertAlmostEqual(out.values.iloc[-1], 100.0, places=6)

    def test_percentile_rank_of_min_is_lowest(self):
        s = make_series([9.0, 8.0, 7.0, 1.0])
        out = transform.percentile_rank(s)
        self.assertAlmostEqual(out.values.iloc[-1], 25.0, places=6)

    def test_rebase_sets_first_to_100(self):
        s = make_series([50.0, 75.0, 100.0])
        out = transform.rebase(s)
        self.assertAlmostEqual(out.values.iloc[0], 100.0, places=6)
        self.assertAlmostEqual(out.values.iloc[-1], 200.0, places=6)


class TestResample(unittest.TestCase):
    def test_daily_to_month_end_last(self):
        s = make_series(list(range(1, 63)), freq="D", start="2025-01-01")
        out = transform.resample(s, freq="ME", how="last")
        self.assertEqual(out.prov.frequency, "monthly")
        # January has 31 days, so the month-end value is the 31st observation
        self.assertAlmostEqual(out.values.iloc[0], 31.0, places=6)

    def test_rejects_unknown_aggregation(self):
        s = make_series([1.0, 2.0])
        with self.assertRaises(ValueError):
            transform.resample(s, how="median_ish")


class TestTwoSeries(unittest.TestCase):
    def test_spread_aligns_mismatched_indexes(self):
        a = make_series([3.0, 4.0, 5.0], start="2025-01-31", series_id="a")
        b = make_series([1.0, 1.0], start="2025-02-28", series_id="b")
        out = transform.spread(a, b)
        # at 2025-03-31 both have data: 5 - 1 = 4
        self.assertAlmostEqual(out.values.loc["2025-03-31"], 4.0, places=6)
        # at 2025-01-31 b has no history yet
        self.assertTrue(pd.isna(out.values.loc["2025-01-31"]))

    def test_spread_carries_both_citations(self):
        a = make_series([1.0], series_id="a")
        a.prov.citation = "출처 A"
        b = make_series([1.0], series_id="b")
        b.prov.citation = "출처 B"
        out = transform.spread(a, b)
        self.assertIn("출처 A", out.prov.citation)
        self.assertIn("출처 B", out.prov.citation)

    def test_ratio(self):
        a = make_series([10.0], series_id="a")
        b = make_series([4.0], series_id="b")
        out = transform.ratio(a, b)
        self.assertAlmostEqual(out.values.iloc[-1], 2.5, places=6)


class TestProvenance(unittest.TestCase):
    def test_lineage_accumulates_in_order(self):
        s = make_series([100.0] * 24)
        out = transform.rolling_mean(transform.yoy(s), window=3)
        self.assertEqual(out.prov.lineage, ["yoy", "rolling_mean(3)"])

    def test_citation_and_source_survive_transforms(self):
        s = make_series([100.0] * 24)
        out = transform.zscore(transform.yoy(s))
        self.assertEqual(out.prov.citation, "테스트 출처")
        self.assertEqual(out.prov.source, "test")

    def test_unit_updates_to_percent_after_yoy(self):
        s = make_series([100.0] * 24, unit="원")
        self.assertEqual(transform.yoy(s).prov.unit, "%")

    def test_apply_named_matches_direct_call(self):
        s = make_series([100.0] * 12 + [110.0])
        direct = transform.yoy(s).values.iloc[-1]
        named = transform.apply_named(s, "yoy").values.iloc[-1]
        self.assertAlmostEqual(direct, named, places=9)

    def test_apply_named_rejects_unknown(self):
        s = make_series([1.0])
        with self.assertRaises(ValueError):
            transform.apply_named(s, "make_it_go_up")

    def test_apply_named_forwards_kwargs(self):
        s = make_series([100.0, 100.0, 100.0, 101.0])
        out = transform.apply_named(s, "annualized", months=3)
        self.assertAlmostEqual(out.values.iloc[-1], 4.060401, places=5)


class TestSeriesBasics(unittest.TestCase):
    def test_series_sorts_unordered_input(self):
        index = pd.to_datetime(["2025-03-31", "2025-01-31", "2025-02-28"])
        s = Series(id="x", values=pd.Series([3.0, 1.0, 2.0], index=index),
                   prov=Provenance(source="t", dataset="d", label="l"))
        self.assertEqual(list(s.values), [1.0, 2.0, 3.0])

    def test_latest_skips_trailing_nan(self):
        s = make_series([1.0, 2.0, float("nan")])
        when, value = s.latest
        self.assertAlmostEqual(value, 2.0, places=6)
        self.assertEqual(when.strftime("%Y-%m"), "2024-02")

    def test_asof_returns_last_value_at_or_before(self):
        s = make_series([1.0, 2.0, 3.0], start="2025-01-31")
        self.assertAlmostEqual(s.asof("2025-02-28"), 2.0, places=6)
        self.assertAlmostEqual(s.asof("2025-03-15"), 2.0, places=6)


class TestDuplicateDateGuard(unittest.TestCase):
    """A row filter that matches two instruments must not become one series.

    Regression guard: a 10-year government bond filter matched both the
    nominal and the inflation-linked issue, and the resulting spread against
    the 3-year looked plausible while mixing a real yield with a nominal one.
    """

    def _duplicated(self):
        index = pd.to_datetime(["2026-08-11", "2026-08-11", "2026-08-12", "2026-08-12"])
        return pd.Series([4.303, 1.720, 4.295, 1.708], index=index)

    def test_duplicate_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            Series(id="ktb_10y", values=self._duplicated(),
                   prov=Provenance(source="krx", dataset="bond_kts", label="10년"))

    def test_error_names_the_series_and_shows_conflicting_values(self):
        with self.assertRaises(ValueError) as ctx:
            Series(id="ktb_10y", values=self._duplicated(),
                   prov=Provenance(source="krx", dataset="bond_kts", label="10년"))
        message = str(ctx.exception)
        self.assertIn("ktb_10y", message)
        self.assertIn("4.295", message)
        self.assertIn("1.708", message)

    def test_unique_dates_are_accepted(self):
        index = pd.to_datetime(["2026-08-11", "2026-08-12"])
        s = Series(id="ok", values=pd.Series([4.303, 4.295], index=index),
                   prov=Provenance(source="krx", dataset="bond_kts", label="10년"))
        self.assertEqual(len(s), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
