import unittest,copy,tempfile
from pathlib import Path
from tests.test_strategy import NOW,evidence,contracts
from pmcc.strategy import evaluate_candidate
from pmcc.ledger import Ledger
from pmcc.management import decide

class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ledger=Ledger(Path(self.tmp.name)/'l.db',{})
        self.c=contracts();self.e=evidence();r=evaluate_candidate('TEST',self.e,self.c,NOW,{})
        self.ledger.open(r,'a',NOW);self.p=self.ledger.snapshot()['positions'][0]
        self.session={'next_session_date':'2026-11-04'}
    def tearDown(self):self.tmp.cleanup()
    def call(self):return decide(self.p,self.e,self.c,NOW,{},self.session)
    def test_long_loss_exits_both_legs(self):
        self.c[0]['bid']=3.2;self.c[0]['ask']=3.3
        self.assertEqual(self.call()['action'],'CLOSE_DIAGONAL')
        self.assertEqual(self.call()['reason'],'LONG_STOP')
    def test_sixty_percent_profit_buys_short_back(self):
        self.c[1]['ask']=.2;self.c[1]['bid']=.18
        self.assertEqual(self.call()['action'],'CLOSE_SHORT')
        self.assertEqual(self.call()['reason'],'SHORT_PROFIT')
    def test_full_campaign_target_is_net_of_fees_and_precedes_short_target(self):
        cfg = {'campaign_profit_target': .50, 'short_profit_capture': .50}
        self.c[1].update(bid=.23, ask=.25)
        self.c[0].update(bid=6.12, ask=6.15)
        d = decide(self.p,self.e,self.c,NOW,cfg,self.session)
        self.assertEqual((d['action'],d['reason']),('CLOSE_SHORT','SHORT_PROFIT'))
        self.c[0]['bid'] = 6.13
        d = decide(self.p,self.e,self.c,NOW,cfg,self.session)
        self.assertEqual((d['action'],d['reason']),('CLOSE_DIAGONAL','CAMPAIGN_PROFIT_TARGET'))
        self.e['next_ex_dividend'] = self.session['next_session_date']
        self.assertEqual(decide(self.p,self.e,self.c,NOW,cfg,self.session)['reason'],'EX_DIVIDEND_EXIT')

    def test_missing_exit_quotes_cannot_make_fictional_exit(self):
        self.c[0]['quote_time']='2026-11-03T14:58:00+00:00'
        self.assertEqual(self.call()['action'],'REFRESH')
    def test_earnings_next_session_exits(self):
        self.e['next_earnings']='2026-11-04'
        self.assertEqual(self.call()['reason'],'EARNINGS_EXIT')
    def test_tentative_earnings_week_exits_with_fresh_held_quotes(self):
        self.p['symbol']='F'
        self.e.pop('earnings_verified')
        self.e['paper_event_review']={
            'source':'FORD_IR_SEC_ROBINHOOD_REVIEW','observed_at':NOW.isoformat(),
            'coverage_through':'2026-12-18','known_events':[],
            'earnings':{'date':'2026-11-06','verified':False},
        }
        self.assertEqual(self.call()['reason'],'EARNINGS_WINDOW_EXIT')
        self.e['paper_event_review']['observed_at']='2026-10-29T15:00:00Z'
        self.assertNotEqual(self.call()['reason'],'EARNINGS_WINDOW_EXIT')

    def test_deeply_tested_short_without_roll_closes_both(self):
        self.e['price']=12.6;self.c[0].update(bid=5.5,ask=5.6)
        self.c[1].update(bid=1.1,ask=1.15,delta=.75)
        self.assertEqual(self.call()['action'],'CLOSE_DIAGONAL')
    def test_short_roll_requires_credit_and_updates_basis(self):
        self.e['price']=12.1;self.c[0].update(bid=5.05,ask=5.15)
        self.c[1].update(bid=.30,ask=.34,delta=.55)
        replacement=copy.deepcopy(self.c[1]);replacement.update(id='replacement',strike=13,expiration='2026-12-18',bid=.50,ask=.54,delta=.25)
        self.c.append(replacement)
        d=self.call();self.assertEqual(d['action'],'ROLL_SHORT')
        self.ledger.short_action(self.p['id'],d['replacement'],.34,'roll',NOW,'TESTED')
        p=self.ledger.snapshot()['positions'][0]
        self.assertEqual(p['cash_basis'],374)
        self.assertEqual(p['short']['id'],'replacement')
    def test_existing_short_liability_is_counted_in_nav(self):
        self.assertEqual(self.ledger.snapshot()['equity'],4984.7)

    def test_missing_greeks_do_not_block_a_priced_stop_exit(self):
        self.c[0].update(bid=3.2,ask=3.3,gamma=None)
        self.assertEqual(self.call()['action'],'CLOSE_DIAGONAL')
        self.assertEqual(self.call()['reason'],'LONG_STOP')

    def test_malformed_quote_returns_refresh(self):
        self.c[0]['quote_time']='malformed'
        self.assertEqual(self.call()['action'],'REFRESH')
    def test_malformed_replacement_does_not_abort_management(self):
        self.e['price']=12.1;self.c[0].update(bid=5.05,ask=5.15)
        self.c[1].update(bid=.30,ask=.34,delta=.55)
        bad=copy.deepcopy(self.c[1]);bad.update(id='bad',strike=None)
        self.c.append(bad)
        self.assertEqual(self.call()['action'],'CLOSE_DIAGONAL')
    def test_past_earnings_date_is_not_an_upcoming_exit(self):
        self.e['next_earnings']='2026-10-20'
        self.assertNotEqual(self.call()['reason'],'EARNINGS_EXIT')

    def test_stale_underlying_does_not_block_priced_short_profit_close(self):
        self.e['price_time']='2026-11-03T14:58:00Z';self.c[1].update(ask=.2,bid=.18)
        self.assertEqual(self.call()['action'],'CLOSE_SHORT')
    def test_malformed_held_delta_does_not_abort(self):
        self.c[1]['delta']='bad'
        self.assertEqual(self.call()['action'],'HOLD')

    def test_verified_provider_switch_can_close_same_economic_contract(self):
        from pmcc.alpaca_data import occ
        for c in self.c:
            c['id']=occ(c['symbol'],c['expiration'],c['strike']);c['quote_instrument_id']=c['id'];c['source']='ALPACA_MARKET_DATA'
        self.c[0].update(bid=3.2,ask=3.3)
        self.assertEqual(self.call()['action'],'CLOSE_DIAGONAL')
    def test_stale_original_quote_does_not_hide_fresh_verified_fallback(self):
        from pmcc.alpaca_data import occ
        alternate=[]
        for c in self.c:
            x=copy.deepcopy(c);x['id']=occ(c['symbol'],c['expiration'],c['strike']);x['quote_instrument_id']=x['id'];x['source']='ALPACA_MARKET_DATA';alternate.append(x)
            c['quote_time']='2026-11-03T14:58:00Z'
        alternate[0].update(bid=3.2,ask=3.3);self.c+=alternate
        self.assertEqual(self.call()['action'],'CLOSE_DIAGONAL')

    def test_unusable_newer_quote_cannot_hide_executable_fallback_stop(self):
        from pmcc.alpaca_data import occ
        from datetime import timedelta
        alternate=[]
        for c in self.c:
            x=copy.deepcopy(c);x['id']=occ(c['symbol'],c['expiration'],c['strike']);x['quote_instrument_id']=x['id'];x['source']='ALPACA_MARKET_DATA';x['quote_time']=(NOW-timedelta(seconds=1)).isoformat();alternate.append(x)
        self.c[0]['bid']=None;alternate[0].update(bid=3.2,ask=3.3);self.c+=alternate
        self.assertEqual(self.call()['action'],'CLOSE_DIAGONAL')

if __name__=='__main__':unittest.main()
