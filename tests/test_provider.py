import unittest
from pmcc.provider import normalize_document
from tests.test_strategy import NOW

class ProviderTests(unittest.TestCase):
    def fixture(self):
        q=dict(instrument_id='c1',bid_price='3.00',ask_price='3.10',bid_size=4,ask_size=5,
            delta='.85',gamma='.03',theta='-.01',vega='.02',implied_volatility='.25',
            volume=17,open_interest=400,updated_at=NOW.isoformat())
        row=dict(instrument_id='c1',chain_id='chain',chain_symbol='F',underlying_type='equity',
            expiration_date='2027-12-17',strike_price='10',type='call',state='active',tradability='tradable',quote=q)
        chain=dict(id='chain',symbol='F',trade_value_multiplier='100',cash_component=None,
            can_open_position=True,settle_on_open=False,underlying_instruments=[{'instrument':'https://example.test/instruments/00000000-0000-0000-0000-000000000001/'}])
        return dict(source='ROBINHOOD_MCP_RESEARCH',research_only=True,order_submission_enabled=False,
            symbols={'F':dict(chains=[chain],contracts=[row],evidence={})})
    # Catches renaming quote fields incorrectly or dropping non-0DTE expirations.
    def test_keeps_greeks_liquidity_timestamps_and_expiry(self):
        r=normalize_document(self.fixture())
        self.assertIn('F',r['symbols'])
        c=r['symbols']['F']['contracts'][0]
        self.assertEqual(c['gamma'],'.03')
        self.assertEqual(c['expiration'],'2027-12-17')
        self.assertEqual(c['quote_time'],NOW.isoformat())
        self.assertEqual(c['volume'],17)
        self.assertIsNone(c['volume_previous'])
        self.assertTrue(c['metadata_verified'])
    def test_real_order_authority_snapshot_is_forbidden(self):
        d=self.fixture();d['order_submission_enabled']=True
        self.assertEqual(normalize_document(d)['reasons'],['RESEARCH_ORDER_AUTHORITY_FORBIDDEN'])
    def test_adjusted_chain_is_not_approved(self):
        d=self.fixture();d['symbols']['F']['chains'][0]['cash_component']='5'
        r=normalize_document(d)
        self.assertIn('F',r['symbols'])
        self.assertFalse(r['symbols']['F']['contracts'][0]['metadata_verified'])
    def test_null_gamma_is_preserved(self):
        d=self.fixture();d['symbols']['F']['contracts'][0]['quote']['gamma']=None
        r=normalize_document(d)
        self.assertIn('F',r['symbols'])
        self.assertIsNone(r['symbols']['F']['contracts'][0]['gamma'])
    def test_mismatched_quote_instrument_is_rejected(self):
        d=self.fixture();d['symbols']['F']['contracts'][0]['quote']['instrument_id']='OTHER'
        r=normalize_document(d)
        self.assertFalse(r['symbols']['F']['contracts'][0]['metadata_verified'])
    def test_top_level_array_is_unavailable(self):
        self.assertEqual(normalize_document([]).get('reasons'),['RESEARCH_SNAPSHOT_MALFORMED'])

    def test_nonstandard_delivery_is_not_verified(self):
        d=self.fixture();d['symbols']['F']['chains'][0]['underlying_instruments']=[{'quantity':150,'instrument':'WRONG'}]
        self.assertFalse(normalize_document(d)['symbols']['F']['contracts'][0]['metadata_verified'])

    def test_nonstring_instrument_identifiers_are_unverified(self):
        d=self.fixture();r=d['symbols']['F']['contracts'][0];r['instrument_id']=['bad'];r['quote']['instrument_id']=['bad']
        self.assertFalse(normalize_document(d)['symbols']['F']['contracts'][0]['metadata_verified'])

if __name__=='__main__':unittest.main()
