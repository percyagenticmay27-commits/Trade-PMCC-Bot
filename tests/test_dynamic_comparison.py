import unittest
from datetime import datetime, timezone
from pmcc.strategy import dynamic_pair_reasons

class DynamicComparison(unittest.TestCase):
    def test_mode_and_width(self):
        now=datetime(2026,11,6,16,tzinfo=timezone.utc)
        stamp=now.isoformat()
        e={'price':12,'next_earnings':'2027-02-01'}
        l={'strike':8,'ask':4.5,'expiration':'2027-11-05','quote_time':stamp}
        s={'strike':13,'bid':.70,'expiration':'2026-11-20','quote_time':stamp}
        self.assertEqual(dynamic_pair_reasons(e,l,s,now,{},'DYNAMIC_OTM'),[])
        self.assertIn('STRIKE_ORDER',dynamic_pair_reasons(e,l,s,now,{},'DYNAMIC_ITM'))
        s.update(strike=11,bid=1.5)
        self.assertIn('WIDTH_RULE',dynamic_pair_reasons(e,l,s,now,{'fee_per_contract':0},'DYNAMIC_ITM'))
        s['bid']=1.6
        self.assertEqual(dynamic_pair_reasons(e,l,s,now,{},'DYNAMIC_ITM'),[])
