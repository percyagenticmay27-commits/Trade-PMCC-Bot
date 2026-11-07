"""Exercise the weekly queue through the production paper cycle."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from pmcc.runtime import run_cycle
from tests import test_runtime


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


FRIDAY = "2026-11-06"
MONDAY = "2026-11-09"
TUESDAY = "2026-11-10"


class WeeklyEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "configs").mkdir()
        (self.root / "inputs").mkdir()
        (self.root / "configs/pmcc-001.json").write_text(json.dumps({
            "initial_capital": 5000, "universe": ["TEST"], "weekly_entries": True,
            "long_dte": [180, 365],
        }))
        fixture = test_runtime.RuntimeTests()
        fixture.root = self.root
        fixture.snapshot()
        self.path = self.root / "inputs/robinhood_pmcc_research.json"
        self.fixture = json.loads(self.path.read_text())

    def scan(self, at, previous, following, bar_date=None, price=11, long_days=250,
             early_close=False):
        now = timestamp(at)
        day = now.date().isoformat()
        session = dict(date=day, open=day + "T14:30:00Z",
                       close=day + ("T18:00:00Z" if early_close else "T21:00:00Z"),
                       previous_session_date=previous, next_session_date=following,
                       verified=True, source="ALPACA_CALENDAR")
        data = self.fixture
        data["session"] = session
        symbol = next(iter(data["symbols"]))
        payload = data["symbols"][symbol]
        e = payload["evidence"]
        e.update(price=price, price_time=at, evidence_at=at,
                 daily_bars_as_of=bar_date or previous)
        for i, leg in enumerate(payload["contracts"]):
            leg["expiration_date"] = (timestamp(FRIDAY + "T00:00:00Z").date()
                                      + timedelta(days=long_days if i == 0 else 40)).isoformat()
            leg.update(previous_volume_date=previous, open_interest_date=previous)
            leg["quote"]["updated_at"] = at
        if symbol == "F":
            (self.root / "inputs/ford_paper_event_review.json").write_text(json.dumps({
                "source": "FORD_IR_SEC_ROBINHOOD_REVIEW", "observed_at": at,
                "coverage_through": (now.date()+timedelta(days=45)).isoformat(),
                "known_events": [], "earnings": {"date": "2027-02-09", "verified": False},
            }))
        self.path.write_text(json.dumps(data))
        (self.root / "inputs/alpaca_pmcc_research.json").write_text(json.dumps({
            "source": "ALPACA_MARKET_DATA", "research_only": True, "order_submission_enabled": False,
            "session": session, "symbols": {symbol: {"metadata": [], "snapshots": {}, "evidence": e}},
        }))
        return run_cycle(self.root, now, entry=True)

    def queue(self, next_day=MONDAY, closing_bar=True, early_close=False):
        start, finish = ("17:50", "18:05") if early_close else ("20:50", "21:05")
        a = self.scan(f"{FRIDAY}T{start}:00Z", "2026-11-05", next_day, early_close=early_close)
        self.assertEqual(a["watchlist_count"], 1)
        self.assertEqual(a["open_count"], 0)
        self.scan(f"{FRIDAY}T{finish}:00Z", "2026-11-05", next_day,
                  bar_date=FRIDAY if closing_bar else None, early_close=early_close)

    def test_verified_weekly_sequence_opens_once_on_first_session(self):
        self.queue()
        review = self.scan(MONDAY + "T13:55:00Z", FRIDAY, TUESDAY)
        self.assertEqual(review["open_count"], 0)
        result = self.scan(MONDAY + "T15:00:00Z", FRIDAY, TUESDAY)
        self.assertEqual(result["open_count"], 1)
        again = self.scan(MONDAY + "T15:01:00Z", FRIDAY, TUESDAY)
        self.assertEqual(again["open_count"], 1)

    def test_missed_entry_waits_until_next_queue(self):
        self.queue()
        self.scan(MONDAY + "T13:55:00Z", FRIDAY, TUESDAY)
        self.assertEqual(self.scan(MONDAY + "T15:05:00Z", FRIDAY, TUESDAY)["open_count"], 0)
        self.assertEqual(self.scan(TUESDAY + "T15:00:00Z", MONDAY, "2026-11-11")["open_count"], 0)

    def test_changed_bias_blocks_even_after_premarket_approval(self):
        self.queue()
        self.scan(MONDAY + "T13:55:00Z", FRIDAY, TUESDAY)
        result = self.scan(MONDAY + "T15:00:00Z", FRIDAY, TUESDAY, price=10.7)
        self.assertEqual(result["open_count"], 0)

    def test_review_and_completed_friday_bar_are_required(self):
        self.queue(closing_bar=False)
        self.scan(MONDAY + "T13:55:00Z", FRIDAY, TUESDAY)
        self.assertEqual(self.scan(MONDAY + "T15:00:00Z", FRIDAY, TUESDAY)["open_count"], 0)

    def test_verified_holiday_and_early_close(self):
        self.queue(next_day=TUESDAY, early_close=True)
        self.scan(TUESDAY + "T13:55:00Z", FRIDAY, "2026-11-11")
        self.assertEqual(self.scan(TUESDAY + "T15:00:00Z", FRIDAY, "2026-11-11")["open_count"], 1)

    def test_unqueued_or_unreviewed_candidate_cannot_enter(self):
        self.assertEqual(self.scan(MONDAY + "T15:00:00Z", FRIDAY, TUESDAY)["open_count"], 0)
        self.queue()
        self.assertEqual(self.scan(MONDAY + "T15:00:00Z", FRIDAY, TUESDAY)["open_count"], 0)

    def test_long_expiration_beyond_365_days_fails_the_weekly_screen(self):
        result = self.scan(FRIDAY + "T20:50:00Z", "2026-11-05", MONDAY, long_days=366)
        self.assertEqual(result["watchlist_count"], 0)

    def test_tentative_ford_earnings_survives_weekly_queue_and_entry_review(self):
        payload = self.fixture["symbols"].pop("TEST")
        self.fixture["symbols"]["F"] = payload
        payload["chains"][0]["symbol"] = "F"
        for leg in payload["contracts"]:
            leg["chain_symbol"] = "F"
        for key in ("earnings_verified", "next_earnings", "events_verified", "binary_event"):
            payload["evidence"].pop(key)
        self.queue()
        review = self.scan(MONDAY + "T13:55:00Z", FRIDAY, TUESDAY)
        self.assertEqual(review["open_count"], 0)
        result = self.scan(MONDAY + "T15:00:00Z", FRIDAY, TUESDAY)
        self.assertEqual(result["open_count"], 1)
