import unittest

from pmcc import ford_evidence


class OccVolumeTest(unittest.TestCase):
    def test_both_trade_sides_are_counted_once_and_expiration_filters_rejected(self):
        rows = (
            'quantity,underlying,symbol,actype,porc,exchange,actdate\n'
            '4,F,F,C,C,CBOE,11/06/2026\n'
            '2,F,F,F,C,CBOE,11/06/2026\n'
            '2,F,F,M,C,CBOE,11/06/2026\n'
        )
        parse = getattr(ford_evidence, 'occ_ford_contract_volume', None)
        self.assertIsNotNone(parse)
        self.assertEqual(parse(rows, '2026-11-06'), 4)
        self.assertEqual(parse(rows.replace('11/06/2026', '11/06/2026,'), '2026-11-06'), 4)
        with self.assertRaises(ValueError):
            parse(rows.replace('actdate','actdate,contractDate'), '2026-11-06')
        with self.assertRaises(ValueError):
            parse(rows.replace('4,F,F', '3,F,F'), '2026-11-06')
