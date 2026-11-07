import json
import tempfile
import unittest
from pathlib import Path

from pmcc.research import run_research, scan
from pmcc.strategy import evaluate_candidate
from tests.test_research import sample
from tests.test_strategy import NOW, contracts, evidence


CAP = {'long_time_premium_cap_dollars': 60}
PROFILE = 'LEAPS_TIME_PREMIUM'


def document():
    result = sample()
    stock = evidence()
    for key in ('iv_rank', 'iv_percentile', 'iv_observations', 'iv_series'):
        stock.pop(key)
    result['symbols'] = {'TEST': {'evidence': stock, 'contracts': contracts()}}
    return result


def variant(report):
    return next((v for v in report['variants'] if v['id'] == PROFILE), {})


class TimePremiumTests(unittest.TestCase):
    def test_study_can_open_without_history_and_uses_an_isolated_ledger(self):
        data = document()
        stock = data['symbols']['TEST']
        classic = evaluate_candidate('TEST', stock['evidence'], stock['contracts'], NOW, {})
        self.assertEqual(classic['status'], 'DATA_UNAVAILABLE')
        self.assertIn('MISSING_IV_RANK', classic['reasons'])

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'configs').mkdir()
            (root/'inputs').mkdir()
            (root/'configs/pmcc-research.json').write_text(json.dumps(CAP))
            (root/'inputs/pmcc_research_observations.json').write_text(json.dumps(data))
            v = variant(run_research(root, NOW))
            self.assertEqual(v.get('id'), PROFILE)
            self.assertEqual(v['candidates'][0]['long_extrinsic_dollars'], 40)
            self.assertEqual(v['performance']['open_count'], 0)
            report = run_research(root, NOW, enter=True, entry_variant=PROFILE)
            self.assertEqual(variant(report)['performance']['open_count'], 1)
            self.assertEqual(report['open_count'], 1)
            self.assertFalse((root/'state/pmcc.sqlite3').exists())

    def test_cap_and_other_entry_gates_still_apply(self):
        data = document()
        stock = data['symbols']['TEST']
        stock['contracts'][0].update(bid=4.51, ask=4.61)
        stock['contracts'][1]['strike'] = 11.01
        v = variant(scan(data, NOW, CAP))
        self.assertEqual(v.get('id'), PROFILE)
        self.assertEqual(v['candidates'], [])
        self.assertIn('LONG_TIME_PREMIUM_CAP', v['evaluations'][0]['reasons'])
        self.assertIn('WIDTH_RULE', v['evaluations'][0]['reasons'])

        stock['contracts'][0].update(bid=4.50, ask=4.60)
        stock['contracts'][1]['strike'] = 12
        self.assertEqual(len(variant(scan(data, NOW, CAP))['candidates']), 1)
        stock['evidence']['earnings_verified'] = False
        v = variant(scan(data, NOW, CAP))
        self.assertEqual(v['candidates'], [])
        self.assertIn('UNVERIFIED_EARNINGS_VERIFIED', v['evaluations'][0]['reasons'])

    def test_ford_tentative_review_is_bounded_and_keeps_dividend_block(self):
        data = document()
        stock = data['symbols'].pop('TEST')
        data['symbols']['F'] = stock
        for contract in stock['contracts']:
            contract['symbol'] = 'F'
        e = stock['evidence']
        for key in ('earnings_verified', 'next_earnings', 'events_verified', 'binary_event'):
            e.pop(key)
        e['paper_event_review'] = {
            'source': 'FORD_IR_SEC_ROBINHOOD_REVIEW',
            'observed_at': NOW.isoformat(),
            'coverage_through': '2026-12-18',
            'earnings': {'date': '2027-02-09', 'verified': False},
            'known_events': [],
        }
        classic = evaluate_candidate('F', e, stock['contracts'], NOW, {})
        self.assertIn('MISSING_IV_RANK', classic['reasons'])
        self.assertEqual(len(variant(scan(data, NOW, CAP))['candidates']), 1)
        e['paper_event_review']['earnings']['date'] = '2026-12-06'
        self.assertIn('EARNINGS_WINDOW', variant(scan(data, NOW, CAP))['evaluations'][0]['reasons'])
        e['paper_event_review']['earnings']['date'] = '2027-02-09'
        e['next_ex_dividend'] = '2026-11-10'
        self.assertIn('EX_DIVIDEND_WINDOW', variant(scan(data, NOW, CAP))['evaluations'][0]['reasons'])
