import json,tempfile,unittest
from pathlib import Path
from datetime import timedelta
from tests.test_strategy import NOW,evidence,contracts
from pmcc.runtime import run_cycle

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'configs').mkdir();(self.root/'inputs').mkdir()
        (self.root/'configs/pmcc-001.json').write_text(json.dumps({'initial_capital':5000,'universe':['TEST']}))
    def tearDown(self):self.tmp.cleanup()
    def snapshot(self,stale=False):
        rows=[]
        for c in contracts():
            q={k:c[k] for k in ['bid_size','ask_size','delta','gamma','theta','vega','open_interest']}
            q.update(instrument_id=c['id'],bid_price=c['bid'],ask_price=c['ask'],updated_at=(NOW-timedelta(seconds=61) if stale else NOW).isoformat(),implied_volatility=c['iv'])
            rows.append(dict(instrument_id=c['id'],chain_id='chain',chain_symbol='TEST',underlying_type='equity',expiration_date=c['expiration'],strike_price=c['strike'],type='call',state='active',tradability='tradable',volume_previous=100,previous_volume_date='2026-11-02',open_interest_date='2026-11-02',quote=q))
        d=dict(source='ROBINHOOD_MCP_RESEARCH',research_only=True,order_submission_enabled=False,
            session=dict(date='2026-11-03',open='2026-11-03T14:30:00Z',close='2026-11-03T21:00:00Z',next_session_date='2026-11-04',previous_session_date='2026-11-02',verified=True,source='ALPACA_CALENDAR'),
            symbols={'TEST':{'evidence':evidence(),'contracts':rows,'chains':[dict(id='chain',symbol='TEST',trade_value_multiplier=100,cash_component=None,can_open_position=True,settle_on_open=False,underlying_instruments=[{'instrument':'https://example.test/instruments/00000000-0000-0000-0000-000000000001/'}])]}})
        (self.root/'inputs/robinhood_pmcc_research.json').write_text(json.dumps(d))
    def test_missing_snapshot_initializes_5000_and_requests_refresh(self):
        r=run_cycle(self.root,NOW,entry=True)
        self.assertEqual(r['status'],'DATA_UNAVAILABLE')
        self.assertTrue((self.root/'results/pmcc_refresh_request.json').exists())
        self.assertTrue((self.root/'results/pmcc_dashboard.html').exists())
    def test_valid_snapshot_opens_local_diagonal_once_and_publishes(self):
        self.snapshot();r=run_cycle(self.root,NOW,entry=True)
        self.assertEqual(r['open_count'],1)
        r=run_cycle(self.root,NOW,entry=True)
        self.assertEqual(r['open_count'],1)
        self.assertEqual(r['cash_balance'],4608.7)
    def test_stale_snapshot_never_opens(self):
        self.snapshot(stale=True);r=run_cycle(self.root,NOW,entry=True)
        self.assertEqual(r['open_count'],0)
        self.assertEqual(r['cash_balance'],5000)
    def test_closed_session_never_opens(self):
        self.snapshot();r=run_cycle(self.root,NOW+timedelta(hours=7),entry=True)
        self.assertEqual(r['open_count'],0)
        self.assertEqual(r['market_session'],'CLOSED')
    def test_wrong_uuid_quote_cannot_liquidate_a_holding(self):
        self.snapshot();self.assertEqual(run_cycle(self.root,NOW,entry=True)['open_count'],1)
        path=self.root/'inputs/robinhood_pmcc_research.json';d=json.loads(path.read_text())
        q=d['symbols']['TEST']['contracts'][0]['quote'];q.update(instrument_id='WRONG_UUID',bid_price=3.2,ask_price=3.3)
        path.write_text(json.dumps(d));r=run_cycle(self.root,NOW,entry=False)
        self.assertEqual(r['open_count'],1)
        self.assertEqual(r['completed_count'],0)
    def test_malformed_symbol_payload_still_publishes(self):
        self.snapshot();path=self.root/'inputs/robinhood_pmcc_research.json';d=json.loads(path.read_text())
        d['symbols']['TEST']['contracts']=7;d['symbols']['TEST']['evidence']=[1]
        path.write_text(json.dumps(d));r=run_cycle(self.root,NOW,entry=True)
        self.assertEqual(r['open_count'],0)
        self.assertEqual(r['status'],'DATA_UNAVAILABLE')

    def test_malformed_calendar_publishes_unavailable(self):
        self.snapshot();path=self.root/'inputs/robinhood_pmcc_research.json';d=json.loads(path.read_text())
        d['session']['next_session_date']=None;path.write_text(json.dumps(d))
        self.assertEqual(run_cycle(self.root,NOW,entry=True)['status'],'DATA_UNAVAILABLE')
    def test_malformed_contract_type_is_isolated(self):
        self.snapshot();path=self.root/'inputs/robinhood_pmcc_research.json';d=json.loads(path.read_text())
        d['symbols']['TEST']['contracts'][0]['type']=['call'];path.write_text(json.dumps(d))
        self.assertEqual(run_cycle(self.root,NOW,entry=True)['open_count'],0)
    def test_malformed_chain_id_is_isolated(self):
        self.snapshot();path=self.root/'inputs/robinhood_pmcc_research.json';d=json.loads(path.read_text())
        d['symbols']['TEST']['contracts'][0]['chain_id']=['bad'];path.write_text(json.dumps(d))
        self.assertEqual(run_cycle(self.root,NOW,entry=True)['open_count'],0)
    def test_malformed_collection_requests_refresh_even_with_valid_stock(self):
        self.snapshot();path=self.root/'inputs/robinhood_pmcc_research.json';d=json.loads(path.read_text())
        d['symbols']['TEST']['contracts']=7;path.write_text(json.dumps(d))
        r=run_cycle(self.root,NOW,entry=True)
        self.assertEqual(r['status'],'DATA_UNAVAILABLE')
        request=json.loads((self.root/'results/pmcc_refresh_request.json').read_text())
        self.assertEqual(request['status'],'REQUESTED')

    def test_fresh_alpaca_fallback_supplies_real_source_and_session(self):
        self.snapshot();rh=self.root/'inputs/robinhood_pmcc_research.json';d=json.loads(rh.read_text())
        payload=d['symbols']['TEST'];metadata=[];snapshots={}
        from pmcc.alpaca_data import occ
        for r in payload['contracts']:
            key=occ('TEST',r['expiration_date'],r['strike_price'])
            metadata.append({'symbol':key,'underlying_symbol':'TEST','root_symbol':'TEST','underlying_asset_id':'00000000-0000-0000-0000-000000000001','expiration_date':r['expiration_date'],'strike_price':r['strike_price'],'type':'call','style':'american','size':'100','status':'active','tradable':True,'open_interest':'1000','open_interest_date':'2026-11-02'})
            q=r['quote'];snapshots[key]={'latestQuote':{'bp':q['bid_price'],'ap':q['ask_price'],'bs':10,'as':10,'t':q['updated_at']},'greeks':{k:q[k] for k in ['delta','gamma','theta','vega']},'impliedVolatility':q['implied_volatility'],'prevDailyBar':{'t':'2026-11-02T05:00:00Z','v':100}}
        payload['evidence']['source']='ALPACA_MARKET_DATA'
        fallback={'source':'ALPACA_MARKET_DATA','research_only':True,'order_submission_enabled':False,'session':d['session'],'symbols':{'TEST':{'metadata':metadata,'snapshots':snapshots,'evidence':payload['evidence']}}}
        (self.root/'inputs/alpaca_pmcc_research.json').write_text(json.dumps(fallback));rh.unlink()
        result=run_cycle(self.root,NOW,entry=True)
        self.assertEqual(result['open_count'],1)
        self.assertEqual(result['market_session'],'OPEN')

if __name__=='__main__':unittest.main()
