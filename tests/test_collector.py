import unittest
from datetime import date,timedelta
from pmcc.collector import technical_evidence,iv_metrics,read_only_path

class CollectorTests(unittest.TestCase):
    def test_completed_daily_bars_supply_actual_sma_and_volume(self):
        bars=[{'t':(date(2026,3,28)+timedelta(days=i)).isoformat()+'T12:00:00Z','c':10+i*.01,'h':10.1+i*.01,'l':9.9+i*.01,'v':2000000} for i in range(220)]
        previous=bars[-1]['t'][:10]
        bars.append({'t':(date.fromisoformat(previous)+timedelta(days=1)).isoformat()+'T12:00:00Z','c':999,'h':999,'l':999,'v':999})
        e=technical_evidence(bars,previous)
        self.assertEqual(e.get('close'),12.19)
        self.assertAlmostEqual(e.get('sma50',0),11.945)
        self.assertAlmostEqual(e.get('sma200',0),11.195)
        self.assertEqual(e.get('avg_volume20'),2000000)
    def test_incomplete_iv_history_never_becomes_zero_rank(self):
        self.assertNotIn('iv_rank',iv_metrics(.25,[],['2026-11-02']))
    def test_iv_metrics_require_exact_sessions_and_consistent_series(self):
        dates=[str(date(2026,1,1)+timedelta(days=i)) for i in range(252)]
        history=[{'date':day,'iv':str(.1+i*.001),'series':'30_DAY_ATM_CONSTANT_MATURITY','method':'CALL_ATM_VARIANCE_INTERPOLATION','source':'ALPACA_MARKET_DATA'} for i,day in enumerate(dates)]
        result=iv_metrics(.25,history,dates)
        self.assertAlmostEqual(result.get('iv_rank',-1),59.7609561753)
        self.assertAlmostEqual(result.get('iv_percentile',-1),59.5238095238)
        history[-1]['source']='UNVERIFIED'
        self.assertNotIn('iv_rank',iv_metrics(.25,history,dates))
    def test_order_account_and_arbitrary_urls_are_forbidden(self):
        for kind,path in [('metadata','/v2/orders'),('metadata','/v2/account'),('market','https://example.com/collect'),('market','/v2/stocks/F/snapshot/../../orders')]:
            self.assertFalse(read_only_path(kind,path))
        self.assertTrue(read_only_path('metadata','/v2/calendar'))
        self.assertTrue(read_only_path('market','/v1beta1/options/snapshots'))
if __name__=='__main__':unittest.main()
