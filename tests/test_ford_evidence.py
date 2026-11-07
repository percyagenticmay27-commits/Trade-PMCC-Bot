import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from pmcc.collector import read_only_path


class FordEvidenceTest(unittest.TestCase):
    def test_corporate_action_data_route_is_allowed(self):
        self.assertTrue(read_only_path('market', '/v1/corporate-actions'))

    def test_iv_history_accepts_only_fresh_closing_session_observations(self):
        from pmcc.ford_evidence import record_iv_close

        session = {'date': '2026-11-06', 'close': '2026-11-06T16:00:00-05:00',
                   'open': '2026-11-06T09:30:00-05:00', 'verified': True}
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            before = datetime(2026, 11, 6, 20, 40, tzinfo=timezone.utc)
            close = datetime(2026, 11, 6, 20, 57, tzinfo=timezone.utc)
            self.assertFalse(record_iv_close(path, session, before, .27))
            self.assertTrue(record_iv_close(path, session, close, .28))
            self.assertTrue(record_iv_close(path, session, close, .29))
            import json
            rows = json.loads(path.read_text())['F']
            self.assertEqual([(r['date'], r['iv']) for r in rows], [('2026-11-06', .29)])

    def test_tentative_earnings_and_empty_dividends_stay_unverified(self):
        from pmcc.ford_evidence import event_evidence

        today = datetime(2026, 11, 6, tzinfo=timezone.utc).date()
        earnings = [{'symbol': 'F', 'report': {'date': '2027-02-09', 'verified': False}}]
        result = event_evidence(today, earnings, [{'symbol': 'F', 'ex_date': '2026-11-10'}])
        self.assertNotIn('earnings_verified', result)
        self.assertEqual(result['next_ex_dividend'], '2026-11-10')
        self.assertEqual(event_evidence(today, earnings, []), {})
