import copy,json,tempfile,unittest
from pathlib import Path
from datetime import timedelta
from tests.test_research import sample,NOW
from pmcc.research import run_research


class ResearchRiskTests(unittest.TestCase):
    def setup_study(self,td,itm=False,policy=None):
        root=Path(td);(root/'inputs').mkdir();(root/'configs').mkdir()
        cfg={'allow_itm_width_risk':True}
        if policy:cfg['risk_policy']=policy
        (root/'configs/pmcc-research.json').write_text(json.dumps(cfg))
        d=sample()
        if itm:d['symbols']['F']['contracts'][-1].update(bid=.85,ask=.87)
        self.write(root,d);run_research(root,NOW,enter=True)
        later=NOW+timedelta(minutes=5)
        for c in d['symbols']['F']['contracts']:c['quote_time']=later.isoformat()
        d['symbols']['F']['evidence']['price_time']=later.isoformat()
        return root,d,later

    def write(self,root,d):
        (root/'inputs/pmcc_research_observations.json').write_text(json.dumps(d))

    def cycle(self,root,d,later,profile='MONTHLY_OTM'):
        self.write(root,d);r=run_research(root,later)
        return next(v for v in r['variants'] if v['id']==profile)

    def prepare_tested(self,d,later,roll=True):
        p=d['symbols']['F'];p['evidence']['price']=13.2
        p['contracts'][0].update(bid=6.5,ask=6.7)
        p['contracts'][2].update(bid=.78,ask=.8,delta=.65)
        if roll:
            c=copy.deepcopy(p['contracts'][2]);c.update(id='roll',quote_instrument_id='roll',strike=14,
                expiration=(later.date()+timedelta(days=50)).isoformat(),bid=.9,ask=.92,delta=.3)
            p['contracts'].append(c)

    def test_combined_loss_closes_even_when_long_has_not_hit_25_percent_stop(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td)
            d['symbols']['F']['contracts'][0].update(bid=5,ask=5.1)
            d['symbols']['F']['contracts'][2].update(bid=1.18,ask=1.2)
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0].get('exit_reason'),'POSITION_LOSS_LIMIT')

    def test_combined_profit_closes_entire_campaign_after_costs(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td);d['symbols']['F']['contracts'][0].update(bid=8,ask=8.1)
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0].get('exit_reason'),'POSITION_PROFIT_TARGET')
            self.assertGreater(v['positions'][0]['realized_pnl'],114)

    def test_tested_short_rolls_up_and_out_for_credit_after_fees_once(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td);self.prepare_tested(d,t)
            v=self.cycle(root,d,t);p=v['positions'][0]
            self.assertEqual(p['short']['id'],'roll')
            self.assertEqual(v['events'][-1]['reason'],'TESTED_SHORT_ROLL')
            self.assertAlmostEqual(v['events'][-1]['credit'],10)
            v2=self.cycle(root,d,t)
            self.assertEqual(v2['performance']['fees'],v['performance']['fees'])

    def test_tested_short_without_acceptable_roll_closes_both_legs(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td);self.prepare_tested(d,t,False)
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0].get('exit_reason'),'TESTED_SHORT_NO_SAFE_ROLL')

    def test_roll_net_debit_or_missing_events_does_not_extend_risk(self):
        for mode in ['debit','events','stale','identity']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as td:
                root,d,t=self.setup_study(td);self.prepare_tested(d,t)
                c=d['symbols']['F']['contracts'][-1]
                if mode=='debit':c.update(bid=.79,ask=.81)
                elif mode=='events':d['symbols']['F']['evidence']['earnings_verified']=False
                elif mode=='stale':c['quote_time']=NOW.isoformat()
                else:c['quote_instrument_id']='wrong'
                v=self.cycle(root,d,t)
                self.assertEqual(v['positions'][0].get('exit_reason'),'TESTED_SHORT_NO_SAFE_ROLL')

    def test_roll_count_cap_closes_instead_of_repeatedly_extending(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td,policy={'max_rolls':0});self.prepare_tested(d,t)
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0].get('exit_reason'),'TESTED_SHORT_ROLL_LIMIT')

    def test_itm_entry_is_not_tested_merely_because_it_is_itm(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td,itm=True)
            v=self.cycle(root,d,t,'WEEKLY_ITM')
            p=v['positions'][0]
            self.assertEqual(p.get('short_entry_delta'),.75)
            self.assertEqual(p['status'],'OPEN_DIAGONAL')

    def test_itm_delta_deterioration_triggers_roll_or_close(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td,itm=True)
            d['symbols']['F']['contracts'][-1]['delta']=.92
            v=self.cycle(root,d,t,'WEEKLY_ITM')
            self.assertEqual(v['positions'][0].get('exit_reason'),'TESTED_SHORT_NO_SAFE_ROLL')

    def test_stale_held_quotes_never_fabricate_protective_fill(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td);self.prepare_tested(d,t)
            d['symbols']['F']['contracts'][0]['quote_time']=NOW.isoformat()
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0]['quote_status'],'DATA_UNAVAILABLE')
            self.assertEqual(v['performance']['completed_count'],0)

    def test_urgent_close_one_minute_after_roll_is_not_suppressed(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td);self.prepare_tested(d,t)
            self.cycle(root,d,t)
            t+=timedelta(minutes=1)
            for c in d['symbols']['F']['contracts']:c['quote_time']=t.isoformat()
            d['symbols']['F']['evidence']['price_time']=t.isoformat()
            d['symbols']['F']['contracts'][0].update(bid=3,ask=3.1)
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0]['status'],'CLOSED')

    def test_roll_cannot_cross_verified_ex_dividend_date(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td);self.prepare_tested(d,t)
            d['symbols']['F']['evidence']['next_ex_dividend']=(t.date()+timedelta(days=4)).isoformat()
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0].get('exit_reason'),'TESTED_SHORT_NO_SAFE_ROLL')

    def test_malformed_dividend_date_does_not_abort_other_studies(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td,itm=True)
            d['symbols']['F']['evidence']['next_ex_dividend']='bad'
            v=self.cycle(root,d,t,'WEEKLY_ITM')
            self.assertEqual(v['positions'][0].get('exit_reason'),'EVENT_DATE_UNAVAILABLE')

    def test_falsey_non_date_dividend_values_cannot_authorize_a_roll(self):
        for bad in [{},[],False,'']:
            with self.subTest(value=bad),tempfile.TemporaryDirectory() as td:
                root,d,t=self.setup_study(td);self.prepare_tested(d,t)
                d['symbols']['F']['evidence']['next_ex_dividend']=bad
                v=self.cycle(root,d,t)
                self.assertEqual(v['positions'][0]['status'],'CLOSED')

    def test_malformed_stock_price_marks_unavailable_without_aborting_cycle(self):
        for bad in [None,'bad',-1]:
            with self.subTest(value=bad),tempfile.TemporaryDirectory() as td:
                root,d,t=self.setup_study(td)
                d['symbols']['F']['evidence']['price']=bad
                v=self.cycle(root,d,t)
                self.assertEqual(v['positions'][0]['quote_status'],'DATA_UNAVAILABLE')

    def test_protective_priced_stop_does_not_require_gamma(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td)
            for c in d['symbols']['F']['contracts']:c.pop('gamma',None)
            d['symbols']['F']['contracts'][0].update(bid=4,ask=4.1)
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0]['status'],'CLOSED')

    def test_protective_priced_stop_does_not_require_fresh_stock_quote(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td)
            d['symbols']['F']['evidence']['price_time']=NOW.isoformat()
            d['symbols']['F']['contracts'][0].update(bid=5,ask=5.1)
            d['symbols']['F']['contracts'][2].update(bid=1.18,ask=1.2)
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0].get('exit_reason'),'POSITION_LOSS_LIMIT')

    def test_expiration_day_fresh_quote_can_close_short(self):
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td)
            t=NOW+timedelta(days=31)
            for c in d['symbols']['F']['contracts']:c['quote_time']=t.isoformat()
            e=d['symbols']['F']['evidence'];e.update(price_time=t.isoformat(),evidence_at=t.isoformat())
            d['session'].update(date=t.date().isoformat(),open=t.replace(hour=14,minute=30).isoformat(),close=t.replace(hour=21).isoformat(),
                previous_session_date=(t.date()-timedelta(days=1)).isoformat(),next_session_date=(t.date()+timedelta(days=1)).isoformat())
            v=self.cycle(root,d,t)
            self.assertIsNone(v['positions'][0]['short'])
            self.assertEqual(v['events'][-1]['reason'],'SHORT_EXPIRY_AVOIDANCE')

    def test_assignment_liability_requires_reconciliation_not_option_only_exit(self):
        from pmcc.ledger import Ledger
        with tempfile.TemporaryDirectory() as td:
            root,d,t=self.setup_study(td)
            ledger=Ledger(root/'state/research/MONTHLY_OTM.sqlite3',{})
            pid=ledger.snapshot()['positions'][0]['id']
            ledger.assign(pid,12.67,0,'assignment',t)
            v=self.cycle(root,d,t)
            self.assertEqual(v['positions'][0].get('next_action'),'VERIFY_ASSIGNMENT_LIABILITY')
            self.assertEqual(v['performance']['status'],'DATA_UNAVAILABLE')
