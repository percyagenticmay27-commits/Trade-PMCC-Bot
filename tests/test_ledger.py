import tempfile,unittest
from pathlib import Path
from tests.test_strategy import NOW,evidence,contracts
from pmcc.strategy import evaluate_candidate
from pmcc.ledger import Ledger

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'ledger.sqlite3'
        self.ledger=Ledger(self.path,{})
        self.c=evaluate_candidate('TEST',evidence(),contracts(),NOW,{})
    def tearDown(self):self.tmp.cleanup()
    # Paired opening must debit cash by 390 plus two commissions, exactly once.
    def test_paired_open_is_atomic_and_idempotent(self):
        r=self.ledger.open(self.c,'open1',NOW)
        self.assertEqual(r['status'],'OPEN')
        self.ledger.open(self.c,'open1',NOW)
        s=self.ledger.snapshot()
        self.assertEqual(s['cash'],4608.7)
        self.assertEqual(len(s['positions']),1)
        self.assertEqual(len(s['events']),1)
    def test_reopening_same_symbol_is_blocked(self):
        self.ledger.open(self.c,'a',NOW)
        self.assertEqual(self.ledger.open(self.c,'b',NOW).get('reason'),'SYMBOL_ALREADY_OPEN')
    def test_gross_long_cost_cap_cannot_use_short_credit(self):
        self.c['gross_long_cost']=1300
        self.assertEqual(self.ledger.open(self.c,'a',NOW).get('reason'),'POSITION_CAP')
        self.assertEqual(self.ledger.snapshot()['cash'],5000)
    def test_close_books_total_net_pnl(self):
        r=self.ledger.open(self.c,'a',NOW)
        self.assertEqual(r['status'],'OPEN')
        self.ledger.close(r['position_id'],4.8,.2,'b',NOW,'TAKE_PROFIT')
        s=self.ledger.snapshot()
        self.assertEqual(s['cash'],5067.4)
        self.assertEqual(s['positions'][0]['status'],'CLOSED')
        self.assertEqual(s['positions'][0]['realized_pnl'],67.4)
    def test_live_flag_is_rejected(self):
        with self.assertRaises(ValueError):Ledger(self.path,{'live_trading_enabled':True})
    def test_failed_candidate_cannot_open(self):
        self.c['status']='FAIL'
        self.assertEqual(self.ledger.open(self.c,'a',NOW).get('reason'),'CANDIDATE_NOT_CLEARED')
    def test_cash_persists_across_processes(self):
        self.ledger.open(self.c,'a',NOW)
        self.assertEqual(Ledger(self.path,{}).snapshot()['cash'],4608.7)

if __name__=='__main__':unittest.main()
