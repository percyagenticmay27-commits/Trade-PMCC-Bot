import unittest,copy
from tests.test_strategy import NOW,evidence
from pmcc.alpaca_data import normalize_alpaca,combine

class AlpacaDataTests(unittest.TestCase):
    def fixture(self):
        metadata={'symbol':'TEST271217C00007000','underlying_symbol':'TEST','root_symbol':'TEST',
            'underlying_asset_id':'00000000-0000-0000-0000-000000000001','expiration_date':'2027-12-17',
            'strike_price':'7','type':'call','style':'american','size':'100','multiplier':'100',
            'status':'active','tradable':True,'open_interest':'1000','open_interest_date':'2026-11-02'}
        quote={'latestQuote':{'bp':4.3,'ap':4.4,'bs':10,'as':10,'t':NOW.isoformat()},
            'greeks':{'delta':.85,'gamma':.03,'theta':-.01,'vega':.02},'impliedVolatility':.25,
            'prevDailyBar':{'t':'2026-11-02T05:00:00Z','v':100},'dailyBar':{'t':'2026-11-03T05:00:00Z','v':2}}
        return {'source':'ALPACA_MARKET_DATA','research_only':True,'order_submission_enabled':False,
            'session':{'date':'2026-11-03','previous_session_date':'2026-11-02','next_session_date':'2026-11-04','verified':True,'source':'ALPACA_CALENDAR'},
            'symbols':{'TEST':{'metadata':[metadata],'snapshots':{'TEST271217C00007000':quote},'evidence':evidence()}}}
    def test_normalizes_real_snapshot_fields_without_robinhood_attribution(self):
        result=normalize_alpaca(self.fixture())
        self.assertIn('TEST',result['symbols'])
        c=result['symbols']['TEST']['contracts'][0]
        self.assertEqual(c['source'],'ALPACA_MARKET_DATA')
        self.assertEqual(c['gamma'],.03)
        self.assertEqual(c['volume_previous'],100)
        self.assertEqual(c['quote_time'],NOW.isoformat())
        self.assertTrue(c['metadata_verified'])
    def test_occ_strike_must_match_metadata(self):
        d=self.fixture();d['symbols']['TEST']['metadata'][0]['strike_price']='8'
        result=normalize_alpaca(d)
        self.assertIn('TEST',result['symbols'])
        self.assertFalse(result['symbols']['TEST']['contracts'][0]['metadata_verified'])
    def test_current_daily_volume_does_not_substitute_for_missing_previous_bar(self):
        d=self.fixture();d['symbols']['TEST']['snapshots']['TEST271217C00007000'].pop('prevDailyBar')
        result=normalize_alpaca(d);self.assertIn('TEST',result['symbols'])
        self.assertIsNone(result['symbols']['TEST']['contracts'][0]['volume_previous'])
    def test_good_fallback_supplies_session_and_does_not_inherit_primary_error(self):
        primary={'symbols':{},'reasons':['RESEARCH_SOURCE_UNVERIFIED'],'session':None}
        fallback={'symbols':{'TEST':{'evidence':evidence(),'contracts':[],'reasons':[]}},'reasons':[],'session':self.fixture()['session'],'source':'ALPACA_MARKET_DATA'}
        result=combine(primary,fallback,NOW)
        self.assertEqual(result['reasons'],[])
        self.assertIn('TEST',result['symbols'])
        self.assertEqual(result['session']['source'],'ALPACA_CALENDAR')
    def test_unverified_primary_calendar_cannot_hide_valid_fallback(self):
        from pmcc.runtime import market_session
        valid={**self.fixture()['session'],'open':'2026-11-03T14:30:00Z','close':'2026-11-03T21:00:00Z'}
        primary={'symbols':{},'reasons':[],'session':{**valid,'verified':False}}
        fallback={'symbols':{},'reasons':[],'session':valid}
        self.assertEqual(market_session(combine(primary,fallback,NOW)['session'],NOW),'OPEN')
    def test_stale_primary_options_cannot_hide_fresh_fallback_surface(self):
        from tests.test_strategy import contracts
        good={'evidence':evidence(),'contracts':contracts(),'reasons':[]}
        stale=copy.deepcopy(good)
        for c in stale['contracts']:c['quote_time']='2026-11-03T14:58:00Z'
        stale['evidence']['source']='ROBINHOOD_MCP_RESEARCH';good['evidence']['source']='ALPACA_MARKET_DATA'
        p={'symbols':{'TEST':stale},'reasons':[],'session':None};f={'symbols':{'TEST':good},'reasons':[],'session':None}
        self.assertEqual(combine(p,f,NOW)['symbols']['TEST']['evidence']['source'],'ALPACA_MARKET_DATA')

if __name__=='__main__':unittest.main()
