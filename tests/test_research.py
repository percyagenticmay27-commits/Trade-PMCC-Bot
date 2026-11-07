import copy,json,tempfile,unittest,subprocess,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
from pmcc.research import scan,performance,run_research
from tests.test_strategy import contracts,evidence,NOW

def sample():
    e=evidence();e.update(price=12.67,avg_volume20=41000000,sector='Automobiles')
    long=contracts()[0];long.update(symbol='F',strike=7,bid=5.8,ask=6,delta=.98,volume_previous=0)
    short=contracts()[1];short.update(symbol='F',strike=13,bid=.26,ask=.28)
    def leg(id,k,dte,bid,ask,delta):
        c=copy.deepcopy(short);c.update(id=id,quote_instrument_id=id,strike=k,bid=bid,ask=ask,delta=delta,
            expiration=(NOW.date()+timedelta(days=dte)).isoformat());return c
    cs=[long,leg('weekly',13,9,.11,.12,.27),leg('monthly',13,31,.26,.28,.27),leg('itm',12,9,.70,.72,.75)]
    return {'symbols':{'F':{'evidence':e,'contracts':cs}},'session':dict(date=NOW.date().isoformat(),
        open='2026-11-03T14:30:00Z',close='2026-11-03T21:00:00Z',previous_session_date='2026-11-02',
        next_session_date='2026-11-04',verified=True,source='ALPACA_CALENDAR')}

class ResearchTests(unittest.TestCase):
    def profiles(self,d=None,now=NOW):
        r=scan(d or sample(),now);self.assertEqual(len(r['variants']),3)
        return {v['id']:v for v in r['variants']}
    def test_three_equal_shadow_studies_do_not_allocate_15000(self):
        r=scan(sample(),NOW);self.assertEqual(len(r['variants']),3)
        self.assertEqual(r['platform_allocation'],5000)
        self.assertEqual([v['starting_capital'] for v in r['variants']],[5000]*3)
        self.assertTrue(all(v['shadow_only'] for v in r['variants']))
    def test_deep_itm_long_above_90_delta_and_zero_daily_volume_is_observed(self):
        p=self.profiles();v=p['WEEKLY_OTM'];self.assertTrue(v['candidates'])
        c=v['candidates'][0];self.assertEqual(c['long']['strike'],7)
        self.assertAlmostEqual(c['long_extrinsic_dollars'],33)
    def test_itm_cash_credit_is_not_all_time_income_and_width_risk_surfaces(self):
        v=self.profiles()['WEEKLY_ITM'];self.assertTrue(v['candidates'])
        c=v['candidates'][0];self.assertEqual(c['short_credit_dollars'],70)
        self.assertAlmostEqual(c['short_time_premium_dollars'],3)
        self.assertIn('NEGATIVE_EXERCISE_CUSHION',c['warnings'])
        self.assertEqual(c['exercise_cushion_dollars'],-30)
    def test_monthly_candidate_economics_use_ask_buy_bid_sell(self):
        v=self.profiles()['MONTHLY_OTM'];self.assertTrue(v['candidates'])
        c=v['candidates'][0];self.assertEqual(c['net_debit'],574)
        self.assertEqual(c['exercise_cushion_dollars'],26)
        self.assertGreater(c['estimated_round_trip_cost_dollars'],0)
    def test_stale_quotes_remain_observations_never_entry_clearance(self):
        p=self.profiles(now=NOW+timedelta(minutes=2))
        self.assertTrue(p['MONTHLY_OTM']['candidates'])
        self.assertIn('STALE_QUOTES',p['MONTHLY_OTM']['candidates'][0]['entry_blockers'])
    def test_missing_delta_is_not_invented_from_intrinsic_value(self):
        d=sample();d['symbols']['F']['contracts'][0]['delta']=None
        p=self.profiles(d);self.assertTrue(p['MONTHLY_OTM']['candidates'])
        c=p['MONTHLY_OTM']['candidates'][0];self.assertIsNone(c['long']['delta'])
        self.assertIn('MISSING_DELTA',c['entry_blockers'])
    def test_candidates_do_not_depend_on_cio_financial_or_momentum_scores(self):
        d=sample();d['symbols']['F']['evidence']={k:v for k,v in d['symbols']['F']['evidence'].items()
            if k in ['price','price_time','avg_volume20','sector']}
        p=self.profiles(d);self.assertTrue(p['MONTHLY_OTM']['candidates'])
        self.assertIn('EVENT_EVIDENCE_UNAVAILABLE',p['MONTHLY_OTM']['candidates'][0]['entry_blockers'])
    def test_negative_intrinsic_adjusted_bid_is_not_counted_as_income(self):
        d=sample();s=d['symbols']['F']['contracts'][-1];s.update(strike=11.5,bid=1.12,ask=1.34,delta=.95)
        p=self.profiles(d);c=p['WEEKLY_ITM']['candidates'][0]
        self.assertEqual(c['short_time_premium_dollars'],0)
        self.assertIn('BID_BELOW_INTRINSIC',c['warnings'])
    def test_performance_counts_equity_not_gross_premium_and_drawdown(self):
        s={'initial_capital':5000,'equity':4900,'positions':[{'status':'CLOSED','realized_pnl':-100,'fees':2.6}],
           'events':[{'status':'SHORT_OPENED','credit':70}]}
        p=performance(s,peak=5100,prior_drawdown=150)
        self.assertEqual(p.get('net_pnl'),-100)
        self.assertEqual(p.get('max_drawdown_dollars'),200)
        self.assertEqual(p.get('fees'),2.6)
    def test_empty_study_has_no_winner_or_made_up_profit(self):
        p=performance({'initial_capital':5000,'equity':5000,'positions':[],'events':[]})
        self.assertIsNone(p.get('win_rate_percent'))
        self.assertEqual(p.get('status'),'NOT_STARTED')
    def test_research_command_creates_three_isolated_accounts_without_fills(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir();(root/'configs').mkdir()
            (root/'inputs/pmcc_research_observations.json').write_text(json.dumps(sample()))
            r=run_research(root,NOW+timedelta(hours=8),enter=True)
            self.assertEqual(r.get('open_count'),0)
            self.assertTrue((root/'results/pmcc_research_report.json').exists())
            self.assertEqual(len(list((root/'state/research').glob('*.sqlite3'))),3)
    def test_research_entry_rejects_mismatched_quote_identity(self):
        d=sample();d['symbols']['F']['contracts'][0]['quote_instrument_id']='WRONG'
        p=self.profiles(d);self.assertIn('QUOTE_IDENTITY',p['MONTHLY_OTM']['candidates'][0]['entry_blockers'])
    def test_cli_publishes_research_without_changing_baseline_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            p=subprocess.run([sys.executable,'-m','pmcc','research','--root',td],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertTrue((Path(td)/'results/pmcc_research_report.json').exists())
            self.assertFalse((Path(td)/'state/pmcc.sqlite3').exists())
    def test_stale_open_portfolio_reports_unavailable_performance(self):
        p=performance({'initial_capital':5000,'equity':5000,'positions':[{'status':'OPEN_DIAGONAL','quote_status':'DATA_UNAVAILABLE'}],'events':[]})
        self.assertEqual(p['status'],'DATA_UNAVAILABLE')
    def test_explicit_itm_research_exception_is_labeled_and_not_for_otm(self):
        d=sample();d['symbols']['F']['contracts'][-1].update(bid=.85,ask=.87)
        p={v['id']:v for v in scan(d,NOW,{'allow_itm_width_risk':True})['variants']}
        c=p['WEEKLY_ITM']['candidates'][0]
        self.assertNotIn('WIDTH_MARGIN',c['entry_blockers'])
        self.assertIn('ITM_EXERCISE_RISK_ACCEPTED_FOR_SHADOW_STUDY',c['warnings'])
    def test_live_config_is_rejected(self):
        with self.assertRaises(ValueError):scan(sample(),NOW,{'live_trading_enabled':True})
    def test_forward_monthly_entry_mark_stop_and_repeated_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir();(root/'configs').mkdir();path=root/'inputs/pmcc_research_observations.json'
            d=sample();path.write_text(json.dumps(d))
            r=run_research(root,NOW,enter=True)
            self.assertEqual(r['open_count'],1)
            self.assertEqual(run_research(root,NOW,enter=True)['open_count'],1)
            later=NOW+timedelta(minutes=5)
            for c in d['symbols']['F']['contracts']:c['quote_time']=later.isoformat()
            d['symbols']['F']['evidence']['price_time']=later.isoformat()
            d['symbols']['F']['contracts'][0].update(bid=4,ask=4.1)
            path.write_text(json.dumps(d));r=run_research(root,later)
            v=next(v for v in r['variants'] if v['id']=='MONTHLY_OTM')
            self.assertEqual(v['performance']['completed_count'],1)
            self.assertAlmostEqual(v['performance']['net_pnl'],-204.6)
    def test_missing_sector_blocks_shadow_entry_before_ledger(self):
        d=sample();d['symbols']['F']['evidence'].pop('sector')
        p=self.profiles(d)
        self.assertIn('SECTOR_UNAVAILABLE',p['MONTHLY_OTM']['candidates'][0]['entry_blockers'])
    def test_missing_stock_liquidity_cannot_clear_entry(self):
        d=sample();d['symbols']['F']['evidence'].pop('avg_volume20')
        p=self.profiles(d);self.assertIn('STOCK_LIQUIDITY_UNAVAILABLE',p['MONTHLY_OTM']['candidates'][0]['entry_blockers'])
    def test_same_uuid_with_changed_strike_cannot_mark_holding(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir();path=root/'inputs/pmcc_research_observations.json'
            d=sample();path.write_text(json.dumps(d));run_research(root,NOW,enter=True)
            later=NOW+timedelta(minutes=5)
            for c in d['symbols']['F']['contracts']:c['quote_time']=later.isoformat()
            d['symbols']['F']['evidence']['price_time']=later.isoformat()
            d['symbols']['F']['contracts'][0]['strike']=99
            path.write_text(json.dumps(d));r=run_research(root,later)
            v=next(v for v in r['variants'] if v['id']=='MONTHLY_OTM')
            self.assertEqual(v['positions'][0]['long']['strike'],7)
            self.assertEqual(v['performance']['status'],'DATA_UNAVAILABLE')
    def test_missed_short_expiry_requires_explicit_reconciliation_without_settlement(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir();path=root/'inputs/pmcc_research_observations.json'
            d=sample();path.write_text(json.dumps(d));r=run_research(root,NOW,enter=True)
            before=next(v for v in r['variants'] if v['id']=='MONTHLY_OTM')['performance']['equity']
            r=run_research(root,NOW+timedelta(days=40));v=next(v for v in r['variants'] if v['id']=='MONTHLY_OTM')
            self.assertEqual(v['positions'][0]['status'],'EXPIRY_RECONCILIATION_REQUIRED')
            self.assertEqual(v['performance']['completed_count'],0)
            self.assertEqual(v['performance']['equity'],before)
    def test_itm_replacement_retains_explicit_width_risk_policy(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir();(root/'configs').mkdir()
            (root/'configs/pmcc-research.json').write_text(json.dumps({'allow_itm_width_risk':True}))
            d=sample();d['symbols']['F']['contracts'][-1].update(bid=.85,ask=.87)
            path=root/'inputs/pmcc_research_observations.json';path.write_text(json.dumps(d));run_research(root,NOW,enter=True)
            later=NOW+timedelta(days=8)
            for c in d['symbols']['F']['contracts']:c['quote_time']=later.isoformat()
            e=d['symbols']['F']['evidence'];e.update(price_time=later.isoformat(),evidence_at=later.isoformat())
            d['session'].update(date=later.date().isoformat(),open=later.replace(hour=14,minute=30).isoformat(),close=later.replace(hour=21).isoformat(),
                previous_session_date=(later.date()-timedelta(days=1)).isoformat(),next_session_date=(later.date()+timedelta(days=1)).isoformat())
            replacement=copy.deepcopy(d['symbols']['F']['contracts'][-1]);replacement.update(id='replacement',quote_instrument_id='replacement',expiration=(later.date()+timedelta(days=9)).isoformat())
            d['symbols']['F']['contracts'].append(replacement);path.write_text(json.dumps(d));run_research(root,later,enter=True)
            later+=timedelta(minutes=5)
            for c in d['symbols']['F']['contracts']:c['quote_time']=later.isoformat()
            e['price_time']=later.isoformat();path.write_text(json.dumps(d));r=run_research(root,later,enter=True)
            v=next(v for v in r['variants'] if v['id']=='WEEKLY_ITM')
            self.assertEqual(v['positions'][0]['short']['id'] if v['positions'][0]['short'] else None,'replacement')
