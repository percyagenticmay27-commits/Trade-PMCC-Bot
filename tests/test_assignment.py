import tempfile,unittest
from pathlib import Path
from tests.test_strategy import NOW,evidence,contracts
from pmcc.strategy import evaluate_candidate
from pmcc.ledger import Ledger

class AssignmentTests(unittest.TestCase):
    def test_modeled_delivery_and_dividend_liability_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            l=Ledger(Path(tmp)/'isolated-scenario.db',{})
            r=l.open(evaluate_candidate('TEST',evidence(),contracts(),NOW,{}),'open',NOW)
            result=l.assign(r['position_id'],13,.15,'assignment',NOW)
            self.assertEqual(result['status'],'ASSIGNED_SCENARIO')
            p=l.snapshot()['positions'][0]
            self.assertEqual(p['short_stock'],-100)
            self.assertEqual(p['dividend_liability'],15)
            self.assertIsNone(p['short'])
            self.assertEqual(l.snapshot()['cash'],5808.7)
            # Sell long for 650, buy back 100 shares for 1300, pay 15 dividend and .65 commission.
            result=l.reconcile(p['id'],6.5,13,'reconcile',NOW)
            self.assertEqual(result['status'],'CLOSED')
            self.assertEqual(l.snapshot()['cash'],5143.05)
            self.assertEqual(l.snapshot()['positions'][0]['realized_pnl'],143.05)
            l.reconcile(p['id'],6.5,13,'reconcile',NOW)
            self.assertEqual(l.snapshot()['cash'],5143.05)
if __name__=='__main__':unittest.main()
