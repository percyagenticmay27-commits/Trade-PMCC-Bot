import copy
import unittest
from datetime import datetime, timedelta, timezone
from pmcc.strategy import evaluate_candidate

NOW = datetime(2026, 11, 3, 15, tzinfo=timezone.utc)

def evidence():
    return dict(price=11, price_time=NOW.isoformat(), close=11, sma50=10.8,
        sma200=10.2, sma50_20_ago=10.5, atr14=.4, below_sma200_days=0,
        avg_volume20=2000000, avg_dollar_volume20=22000000,
        avg_option_volume20=3000, iv_rank=25, iv_percentile=30, iv_observations=252,
        iv_series='30_DAY_ATM_CONSTANT_MATURITY', operating_cash_flow_ttm=1000000,
        assets=10000000, equity=5000000, ebit=1000000, interest_expense=100000,
        security_type='COMMON_STOCK', exchange='NYSE', sector='Industrials',
        going_concern=False, bankruptcy=False, delisting=False, delinquent=False,
        binary_event=False, financials_current=True, earnings_verified=True,
        next_earnings='2027-01-20', events_verified=True, ex_dividend_verified=True,
        next_ex_dividend=None, evidence_at=NOW.isoformat(), source='VERIFIED_TEST_FIXTURE')

def contracts():
    def leg(id,strike,dte,bid,ask,delta,volume,oi):
        return dict(id=id, symbol='TEST', strike=strike, expiration=(NOW.date()+timedelta(days=dte)).isoformat(),
            bid=bid, ask=ask, bid_size=10, ask_size=10, delta=delta, gamma=.03,
            theta=-.01, vega=.02, iv=.25, volume_previous=volume, open_interest=oi,
            previous_volume_date='2026-11-02',previous_session_date='2026-11-02', open_interest_date='2026-11-02',
            quote_time=NOW.isoformat(),quote_instrument_id=id, right='call', multiplier=100, standard=True,
            exercise_style='american', settlement='physical', metadata_verified=True,
            state='active', tradability='tradable',can_open_position=True)
    return [leg('long',7,409,4.30,4.40,.85,100,1000),leg('short',12,31,.50,.54,.25,100,1000)]

class StrategyTests(unittest.TestCase):
    # Catches rejecting valid diagonals or confusing per-share/contract economics.
    def test_valid_setup_has_390_debit_and_110_width_cushion(self):
        r=evaluate_candidate('TEST',evidence(),contracts(),NOW,{})
        self.assertEqual(r['status'],'PASS')
        self.assertEqual(r['net_debit'],390)
        self.assertEqual(r['width_cushion'],110)

    # Catches using rank/board opinions to override incomplete source data.
    def test_missing_earnings_never_passes(self):
        e=evidence();e.pop('next_earnings')
        r=evaluate_candidate('TEST',e,contracts(),NOW,{})
        self.assertEqual(r['status'],'DATA_UNAVAILABLE')
        self.assertIn('MISSING_NEXT_EARNINGS',r['reasons'])

    def test_stale_quote_blocks(self):
        c=contracts(); c[0]['quote_time']=(NOW-timedelta(seconds=61)).isoformat()
        r=evaluate_candidate('TEST',evidence(),c,NOW,{})
        self.assertNotEqual(r['status'],'PASS')
        self.assertIn('STALE_QUOTES',r['reasons'])

    def test_bad_width_rejects(self):
        c=contracts();c[1]['strike']=11.01
        r=evaluate_candidate('TEST',evidence(),c,NOW,{})
        self.assertNotEqual(r['status'],'PASS')
        self.assertIn('WIDTH_RULE',r['reasons'])

    def test_wide_relative_short_spread_rejects(self):
        c=contracts();c[1]['bid']=.30;c[1]['ask']=.35
        r=evaluate_candidate('TEST',evidence(),c,NOW,{})
        self.assertNotEqual(r['status'],'PASS')
        self.assertIn('SHORT_SPREAD',r['reasons'])

    def test_current_volume_cannot_replace_previous_volume(self):
        c=contracts();c[0].pop('volume_previous');c[0]['volume']=1000
        r=evaluate_candidate('TEST',evidence(),c,NOW,{})
        self.assertNotEqual(r['status'],'PASS')
        self.assertIn('MISSING_VOLUME_PREVIOUS',r['reasons'])

    def test_ex_dividend_inside_short_expiry_blocks(self):
        e=evidence();e['next_ex_dividend']='2026-11-20'
        r=evaluate_candidate('TEST',e,contracts(),NOW,{})
        self.assertNotEqual(r['status'],'PASS')
        self.assertIn('EX_DIVIDEND_WINDOW',r['reasons'])

    def test_missing_greeks_are_not_zero(self):
        c=contracts();c[0]['gamma']=None
        r=evaluate_candidate('TEST',evidence(),c,NOW,{})
        self.assertNotEqual(r['status'],'PASS')
        self.assertIn('MISSING_GAMMA',r['reasons'])

    def test_nan_price_is_not_a_passing_candidate(self):
        e=evidence();e['price']=float('nan')
        r=evaluate_candidate('TEST',e,contracts(),NOW,{})
        self.assertNotEqual(r['status'],'PASS')

    def test_closed_chain_cannot_open_new_positions(self):
        c=contracts()
        for leg in c:leg['can_open_position']=False
        r=evaluate_candidate('TEST',evidence(),c,NOW,{})
        self.assertNotEqual(r['status'],'PASS')
        self.assertIn('CHAIN_OPENING_DISABLED',r['reasons'])

    def test_previous_volume_must_match_last_completed_session(self):
        c=contracts();c[0]['previous_volume_date']='2026-10-30'
        r=evaluate_candidate('TEST',evidence(),c,NOW,{})
        self.assertNotEqual(r['status'],'PASS')
        self.assertIn('STALE_LIQUIDITY',r['reasons'])

class FordTentativeEarningsTests(unittest.TestCase):
    def test_classic_accepts_only_before_the_tentative_earnings_week(self):
        stock, legs = evidence(), contracts()
        for key in ('earnings_verified','next_earnings','events_verified','binary_event'):
            stock.pop(key)
        for leg in legs:
            leg['symbol'] = 'F'
        review = {
            'source': 'FORD_IR_SEC_ROBINHOOD_REVIEW',
            'observed_at': NOW.isoformat(),
            'coverage_through': '2026-12-18',
            'known_events': [],
            'earnings': {'date': '2026-12-08', 'verified': False},
        }
        stock['paper_event_review'] = review
        self.assertEqual(evaluate_candidate('F', stock, legs, NOW, {})['status'], 'PASS')
        review['earnings']['date'] = '2026-12-03'
        self.assertIn('EARNINGS_WINDOW', evaluate_candidate('F', stock, legs, NOW, {})['reasons'])
        review['earnings']['date'] = '2026-12-08'
        review['observed_at'] = (NOW-timedelta(days=2)).isoformat()
        self.assertIn('PAPER_EVENT_REVIEW_UNAVAILABLE',
                      evaluate_candidate('F', stock, legs, NOW, {})['reasons'])

if __name__=='__main__':unittest.main()
