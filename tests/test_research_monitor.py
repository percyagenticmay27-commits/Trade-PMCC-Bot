import json,tempfile,unittest,subprocess,sys
from pathlib import Path
from unittest.mock import patch
from pmcc.research import run_research
from tests.test_research import sample,NOW


class ResearchMonitorTests(unittest.TestCase):
    def test_risk_cli_emits_heartbeat_without_opening_new_positions(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir()
            (root/'inputs/pmcc_research_observations.json').write_text(json.dumps(sample()))
            p=subprocess.run([sys.executable,'-m','pmcc','research-risk','--root',td],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            report=json.loads((root/'results/pmcc_research_risk_status.json').read_text())
            self.assertFalse(report['paper_entry_requested'])
            self.assertEqual(report['collection_status'],'NO_OPEN_POSITIONS')
            self.assertFalse((root/'state/pmcc.sqlite3').exists())
            self.assertEqual(json.loads(p.stdout)['open_count'],0)

    def test_research_collection_refreshes_only_shadow_holdings_not_host_discovery(self):
        from pmcc.collector import collect
        class API:
            def __init__(self):self.calls=[]
            def get(self,kind,path,params=None):
                self.calls.append((path,params))
                if path=='/v2/calendar':return [
                    {'date':'2026-11-02','open':'09:30','close':'16:00'},
                    {'date':'2026-11-03','open':'09:30','close':'16:00'},
                    {'date':'2026-11-04','open':'09:30','close':'16:00'}]
                return {}
            def pages(self,kind,path,params,field):
                self.calls.append((path,params));return []
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir();(root/'configs').mkdir()
            (root/'inputs/pmcc_research_observations.json').write_text(json.dumps(sample()))
            (root/'configs/pmcc-001.json').write_text(json.dumps({'universe':['WRONG']}))
            run_research(root,NOW,enter=True);api=API()
            result=collect(root,NOW,api=api,clock=lambda:NOW,research=True)
            self.assertEqual(set(result['symbols']),{'F'})
            ranges=[c[1] for c in api.calls if c[0]=='/v2/options/contracts']
            self.assertTrue(any(c['expiration_date_gte']=='2026-11-04' and c['expiration_date_lte']=='2027-01-02' for c in ranges))
            self.assertTrue((root/'inputs/alpaca_pmcc_risk.json').exists())
            self.assertFalse((root/'inputs/alpaca_pmcc_research.json').exists())
            self.assertTrue(any(c[0].endswith('/F261204C00013000') for c in api.calls))

    def test_monitor_reports_refresh_failure_without_stale_fills(self):
        from pmcc import research_monitor
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir()
            (root/'inputs/pmcc_research_observations.json').write_text(json.dumps(sample()))
            run_research(root,NOW,enter=True)
            with patch('pmcc.collector.collect',side_effect=OSError('provider unavailable')):
                r=research_monitor.run_monitor(root,NOW)
            self.assertEqual(r.get('status'),'REFRESH_FAILED')
            self.assertEqual(r['actions'],[])
            self.assertTrue((root/'results/pmcc_research_risk_status.json').exists())

    def test_refresh_then_risk_management_closes_using_new_provider_quotes(self):
        from pmcc.research_monitor import run_monitor
        from pmcc.alpaca_data import occ
        from datetime import timedelta
        class API:
            def get(self,kind,path,params=None):
                if path=='/v2/calendar':return [{'date':day,'open':'09:30','close':'16:00'} for day in ['2026-11-02','2026-11-03','2026-11-04']]
                if path.endswith('/snapshot'):return {'latestQuote':{'bp':12.66,'ap':12.68,'t':t.isoformat()}}
                if path=='/v1beta1/options/snapshots':return {'snapshots':{key:{'latestQuote':{'bp':4 if c['id']==long_id else 1.18,'ap':4.1 if c['id']==long_id else 1.2,'bs':10,'as':10,'t':t.isoformat()},'greeks':{'delta':c['delta'],'gamma':.03,'theta':-.01,'vega':.02},'impliedVolatility':.25} for key,c in legs.items() if key in params['symbols'].split(',')}}
                c=legs[path.rsplit('/',1)[-1]]
                return {'symbol':c['id'],'underlying_symbol':'F','root_symbol':'F','underlying_asset_id':'11111111-1111-4111-8111-111111111111',
                        'expiration_date':c['expiration'],'strike_price':str(c['strike']),'type':'call','size':100,'style':'american','status':'active','tradable':True,'open_interest':1000}
            def pages(self,*args):return []
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir();d=sample()
            for c in d['symbols']['F']['contracts']:
                c['id']=c['quote_instrument_id']=occ('F',c['expiration'],c['strike'])
            legs={c['id']:c for c in d['symbols']['F']['contracts']};long_id=d['symbols']['F']['contracts'][0]['id']
            (root/'inputs/pmcc_research_observations.json').write_text(json.dumps(d));run_research(root,NOW,enter=True)
            t=NOW+timedelta(minutes=5)
            with patch('pmcc.collector.ReadOnlyAlpaca',return_value=API()):
                r=run_monitor(root,t,clock=lambda:t)
            self.assertEqual(r['status'],'MONITORED')
            self.assertEqual(r['open_count'],0)
            self.assertEqual(r['actions'][0]['reason'],'LONG_RISK_EXIT')
            report=json.loads((root/'results/pmcc_research_report.json').read_text())
            v=next(v for v in report['variants'] if v['id']=='MONTHLY_OTM')
            self.assertAlmostEqual(v['positions'][0]['exit_value'],280)
