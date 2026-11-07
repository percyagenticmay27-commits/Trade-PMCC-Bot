import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from pmcc.runtime import run_cycle
from pmcc.research import run_research
from tests import test_runtime
from tests.test_strategy import NOW


class LinkedItmShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'configs').mkdir()
        (self.root/'inputs').mkdir()
        (self.root/'configs/pmcc-001.json').write_text(json.dumps({'universe':['TEST']}))
        (self.root/'configs/pmcc-research.json').write_text(json.dumps({'linked_itm_shadow':True}))
        fixture = test_runtime.RuntimeTests()
        fixture.root = self.root
        fixture.snapshot()
        self.path = self.root/'inputs/robinhood_pmcc_research.json'
        self.data = json.loads(self.path.read_text())
        stock = self.data['symbols']['TEST']
        stock['evidence']['avg_volume30'] = 2000000
        stock['contracts'][0]['quote'].update(bid_price=4.36, open_interest=6000)
        short = copy.deepcopy(stock['contracts'][1])
        short.update(instrument_id='itm-short', strike_price=10.5,
                     expiration_date=(NOW.date()+timedelta(days=14)).isoformat())
        short['quote'].update(instrument_id='itm-short', bid_price=.95, ask_price=.98,
                              delta=.70, open_interest=6000)
        stock['contracts'].append(short)
        self.path.write_text(json.dumps(self.data))
        self.shadow = self.root/'state/research/CLASSIC_LINKED_ITM.sqlite3'

    def positions(self, path):
        if not path.exists():
            return []
        with sqlite3.connect(path) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM positions')]

    def test_both_paper_campaigns_open_on_the_same_cycle_and_link_once(self):
        self.assertEqual(run_cycle(self.root,NOW,entry=False)['open_count'],0)
        self.assertEqual(self.positions(self.shadow),[])
        self.assertEqual(run_cycle(self.root,NOW,entry=True)['open_count'],1)
        classic, = self.positions(self.root/'state/pmcc.sqlite3')
        shadow, = self.positions(self.shadow)
        self.assertEqual(classic['opened_at'],shadow['opened_at'])
        self.assertEqual(shadow['evidence']['classic_position_id'],classic['id'])
        self.assertEqual((classic['short']['strike'],shadow['short']['strike']),(12,10.5))
        run_cycle(self.root,NOW,entry=True)
        self.assertEqual(len(self.positions(self.shadow)),1)

    def test_ineligible_itm_candidate_records_skip_without_delaying_classic(self):
        self.data['symbols']['TEST']['contracts'][-1]['quote']['open_interest']=0
        self.path.write_text(json.dumps(self.data))
        self.assertEqual(run_cycle(self.root,NOW,entry=True)['open_count'],1)
        self.assertEqual(self.positions(self.shadow),[])
        self.assertTrue(self.shadow.exists())
        with sqlite3.connect(self.shadow) as db:
            events = [json.loads(row[0]) for row in db.execute('SELECT payload FROM events')]
        self.assertEqual(events[0]['status'],'SHADOW_SKIPPED')
        self.assertIn('SHORT_LIQUIDITY',events[0]['reasons'])

    def test_shadow_risk_close_retains_link_strikes_pnl_and_win_rate(self):
        run_cycle(self.root,NOW,entry=True)
        classic, = self.positions(self.root/'state/pmcc.sqlite3')
        later = NOW+timedelta(minutes=5)
        stock = self.data['symbols']['TEST']
        stock['evidence']['price_time'] = later.isoformat()
        for contract in stock['contracts']:
            contract['quote']['updated_at'] = later.isoformat()
        stock['contracts'][0]['quote'].update(bid_price=6,ask_price=6.05)
        self.path.write_text(json.dumps(self.data))

        report = run_research(self.root,later)
        variant = next(v for v in report['variants'] if v['id']=='CLASSIC_LINKED_ITM')
        shadow, = variant['positions']
        self.assertEqual(shadow['status'],'CLOSED')
        self.assertEqual(shadow['classic_position_id'],classic['id'])
        self.assertEqual(variant['performance']['win_rate_percent'],100)
        close = next(e for e in variant['events'] if e['status']=='CLOSED')
        self.assertEqual((close['long']['strike'],close['short']['strike']),(7,10.5))
        self.assertAlmostEqual(close['long_realized_pnl']+close['short_realized_pnl'],shadow['realized_pnl'])
        self.assertEqual(self.positions(self.root/'state/pmcc.sqlite3')[0]['status'],'OPEN_DIAGONAL')

    def prepare_case(self, long_bid, short_ask):
        path = self.root/'configs/pmcc-001.json'
        path.write_text(json.dumps({'universe':['TEST'],'campaign_profit_target':.50,
                                    'short_profit_capture':.50}))
        run_cycle(self.root,NOW,entry=True)
        later = NOW+timedelta(minutes=5)
        stock = self.data['symbols']['TEST']
        stock['evidence']['price_time'] = later.isoformat()
        for row in stock['contracts']:
            row['quote']['updated_at'] = later.isoformat()
        stock['contracts'][0]['quote'].update(bid_price=long_bid,ask_price=long_bid+.03)
        stock['contracts'][-1]['quote'].update(bid_price=short_ask-.02,ask_price=short_ask)
        self.path.write_text(json.dumps(self.data))
        return later

    def linked(self, now):
        result = run_research(self.root,now)
        return next(v for v in result['variants'] if v['id']=='CLASSIC_LINKED_ITM')

    def test_linked_shadow_takes_short_profit_at_half_the_credit(self):
        now = self.prepare_case(4.36,.47)
        shadow = self.linked(now)
        self.assertEqual(shadow['positions'][0]['status'],'LONG_ONLY_MANAGED')
        self.assertEqual(shadow['events'][-1]['reason'],'SHORT_PROFIT_CAPTURE')

    def test_linked_shadow_waits_for_half_net_campaign_profit(self):
        now = self.prepare_case(6.18,.98)
        self.assertEqual(self.linked(now)['positions'][0]['status'],'OPEN_DIAGONAL')
        self.data['symbols']['TEST']['contracts'][0]['quote']['bid_price']=6.19
        self.path.write_text(json.dumps(self.data))
        shadow = self.linked(now)
        self.assertEqual(shadow['positions'][0]['status'],'CLOSED')
        self.assertEqual(shadow['events'][-1]['reason'],'POSITION_PROFIT_TARGET')
