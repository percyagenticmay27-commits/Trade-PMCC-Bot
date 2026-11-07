"""Transactional local paper ledger. Money is stored as integer cents."""
import json,sqlite3,uuid
from pathlib import Path
from pmcc.strategy import num

SAFETY={'paper_trade_mode':True,'live_trading_enabled':False,'broker_execution_enabled':False,
        'order_placement_enabled':False,'capital_movement_enabled':False}

def validate_safety(config):
    for k,v in SAFETY.items():
        if config.get(k,v) is not v:raise ValueError('PAPER_ONLY_REQUIRED:'+k)

def cents(value):return int((num(value)*100).quantize(num(1)))
def money(value):return round(value/100,2)

class Ledger:
    def __init__(self,path,config):
        validate_safety(config);self.config=config;self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1),initial INTEGER NOT NULL,cash INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS positions(id TEXT PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events(key TEXT PRIMARY KEY,payload TEXT NOT NULL);')
            start=cents(config.get('initial_capital',5000))
            db.execute('INSERT OR IGNORE INTO account VALUES(1,?,?)',(start,start))
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        return db
    def snapshot(self,db=None):
        if db is None:
            with self.connect() as db:return self.snapshot(db)
        account=db.execute('SELECT * FROM account').fetchone()
        positions=[json.loads(r[0]) for r in db.execute('SELECT payload FROM positions ORDER BY rowid')]
        events=[json.loads(r[0]) for r in db.execute('SELECT payload FROM events ORDER BY rowid')]
        mv=sum(cents(p.get('market_value',0)) for p in positions if p['status']!='CLOSED')
        return dict(initial_capital=money(account['initial']),cash=money(account['cash']),equity=money(account['cash']+mv),positions=positions,events=events,**SAFETY)
    def transact(self,key,now,operation):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT payload FROM events WHERE key=?',(key,)).fetchone()
            if prior:return {**json.loads(prior[0]),'duplicate':True}
            result=operation(db)
            if result.get('status')=='BLOCKED':return result
            result={**result,'key':key,'at':now.isoformat(),'strategy_id':self.config.get('strategy_id','PMCC-001'),'paper_only':True}
            db.execute('INSERT INTO events VALUES(?,?)',(key,json.dumps(result,allow_nan=False)))
            return result
    def _save(self,db,p):
        db.execute('INSERT OR REPLACE INTO positions VALUES(?,?)',(p['id'],json.dumps(p,allow_nan=False)))
    def fee(self):return num(self.config.get('fee_per_contract',.65))+num(self.config.get('regulatory_fee_per_contract',0))
    def open(self,c,key,now):
        def operation(db):
            block=lambda reason:{'status':'BLOCKED','reason':reason}
            if c.get('status')!='PASS':return block('CANDIDATE_NOT_CLEARED')
            s=self.snapshot(db);opened=[p for p in s['positions'] if p['status']!='CLOSED']
            if any(p['symbol']==c['symbol'] for p in opened):return block('SYMBOL_ALREADY_OPEN')
            gross=max(num(c['gross_long_cost']),num(c['long']['ask'])*100);fee=self.fee()*2
            if gross+fee>num(s['equity'])*num(self.config.get('position_cap',.25)):return block('POSITION_CAP')
            if len(opened)>=self.config.get('max_underlyings',4):return block('POSITION_COUNT_CAP')
            if num(s['cash'])-gross-fee<num(s['equity'])*num(self.config.get('cash_reserve',.20)):return block('CASH_RESERVE')
            sector=c['evidence']['sector']
            used=sum(num(p['gross_long_cost']) for p in opened if p['sector']==sector)
            if used+gross+fee>num(s['equity'])*num(self.config.get('sector_cap',.40)):return block('SECTOR_CAP')
            debit=(num(c['long']['ask'])-num(c['short']['bid']))*100
            if debit<=0:return block('INVALID_DEBIT')
            pid=str(uuid.uuid4())
            p=dict(id=pid,symbol=c['symbol'],sector=sector,status='OPEN_DIAGONAL',quantity=1,
                long=c['long'],short=c['short'],entry_long_instrument_id=c['long']['id'],entry_short_instrument_id=c['short']['id'],long_entry=float(num(c['long']['ask'])),
                short_entry=float(num(c['short']['bid'])),short_entry_delta=c['short'].get('delta'),gross_long_cost=float(gross),
                initial_debit=float(debit),cash_basis=float(debit),fees=float(fee),realized_short_pnl=0,
                opened_at=now.isoformat(),market_value=float((num(c['long']['bid'])-num(c['short']['ask']))*100),
                quote_status='CURRENT',last_mark_at=now.isoformat(),evidence=c['evidence'])
            link={k:c[k] for k in ('classic_position_id',) if k in c}
            p.update(link)
            self._save(db,p);db.execute('UPDATE account SET cash=cash-? WHERE id=1',(cents(debit+fee),))
            return dict(status='OPEN',position_id=pid,symbol=c['symbol'],net_debit=float(debit),fees=float(fee),
                long=p['long'],short=p['short'],**link,**{k:c[k] for k in ('linked_itm_intent',) if k in c})
        return self.transact(key,now,operation)
    def change(self,pid,key,now,fn):
        def operation(db):
            row=db.execute('SELECT payload FROM positions WHERE id=?',(pid,)).fetchone()
            if not row:return {'status':'BLOCKED','reason':'POSITION_NOT_FOUND'}
            p=json.loads(row[0])
            if p['status']=='CLOSED':return {'status':'BLOCKED','reason':'POSITION_CLOSED'}
            result=fn(db,p)
            if result.get('status')!='BLOCKED':self._save(db,p)
            return {**result,'position_id':pid,'symbol':p['symbol']}
        return self.transact(key,now,operation)
    def close(self,pid,long_bid,short_ask,key,now,reason):
        def fn(db,p):
            if p.get('short_stock',0):return {'status':'BLOCKED','reason':'ASSIGNMENT_RECONCILIATION_REQUIRED'}
            value=num(long_bid)*100-(num(short_ask)*100 if p.get('short') else 0)
            fee=self.fee()*(2 if p.get('short') else 1)
            pnl=value-num(p['cash_basis'])-num(p['fees'])-fee
            short_pnl=(num(p['short_entry'])-num(short_ask))*100-self.fee()*2 if p.get('short') and p.get('short_entry') is not None else None
            long_pnl=(num(long_bid)-num(p['long_entry']))*100-self.fee()*2 if p.get('long_entry') is not None else None
            p.update(status='CLOSED',closed_at=now.isoformat(),exit_reason=reason,exit_value=float(value),
                realized_pnl=float(pnl.quantize(num('.01'))),fees=float(num(p['fees'])+fee),market_value=0)
            db.execute('UPDATE account SET cash=cash+? WHERE id=1',(cents(value-fee),))
            return dict(status='CLOSED',reason=reason,realized_pnl=p['realized_pnl'],cash_delta=float(value-fee),
                long=p['long'],short=p.get('short'),long_exit_bid=float(num(long_bid)),
                short_exit_ask=float(num(short_ask)) if p.get('short') else None,fees=float(fee),
                long_realized_pnl=float(long_pnl) if long_pnl is not None else None,
                short_realized_pnl=float(short_pnl) if short_pnl is not None else None)
        return self.change(pid,key,now,fn)
    def short_action(self,pid,replacement,old_ask,key,now,reason):
        def fn(db,p):
            old=p.get('short');fee=self.fee()*((1 if old else 0)+(1 if replacement else 0))
            credit=(num(replacement['bid'])*100 if replacement else num(0))-(num(old_ask)*100 if old else num(0))
            p['cash_basis']=float(num(p['cash_basis'])-credit);p['fees']=float(num(p['fees'])+fee)
            short_pnl=(num(p['short_entry'])-num(old_ask))*100-self.fee()*2 if old else None
            if old:p['realized_short_pnl']=float(num(p['realized_short_pnl'])+short_pnl)
            p['short']=replacement;p['short_entry']=float(num(replacement['bid'])) if replacement else None
            p['short_entry_delta']=replacement.get('delta') if replacement else None
            p['status']='OPEN_DIAGONAL' if replacement else 'LONG_ONLY_MANAGED'
            # Refresh market value in the same transaction; no stale short liability.
            long_bid=num(p['long']['bid'])
            p['market_value']=float(long_bid*100-(num(replacement['ask'])*100 if replacement else 0))
            db.execute('UPDATE account SET cash=cash+? WHERE id=1',(cents(credit-fee),))
            return dict(status='ROLLED' if old and replacement else 'SHORT_OPENED' if replacement else 'SHORT_CLOSED',
                reason=reason,credit=float(credit),fees=float(fee),replacement=replacement,short_closed=old,
                short_exit_ask=float(num(old_ask)) if old else None,
                short_realized_pnl=float(short_pnl) if old else None)
        return self.change(pid,key,now,fn)
    def mark(self,pid,long,short,evidence,now):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');p=json.loads(db.execute('SELECT payload FROM positions WHERE id=?',(pid,)).fetchone()[0])
            p.setdefault('entry_long_instrument_id',p['long']['id'])
            if p.get('short'):p.setdefault('entry_short_instrument_id',p['short']['id'])
            p.update(long=long,short=short,evidence=evidence,market_value=float(num(long['bid'])*100-(num(short['ask'])*100 if short else 0)),quote_status='CURRENT',last_mark_at=now.isoformat())
            self._save(db,p)
    def unavailable(self,pid,reasons):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');p=json.loads(db.execute('SELECT payload FROM positions WHERE id=?',(pid,)).fetchone()[0])
            p.update(quote_status='DATA_UNAVAILABLE',next_action='REFRESH_QUOTES',data_reasons=reasons);self._save(db,p)
    def assign(self,pid,stock_price,dividend_per_share,key,now):
        """Explicit modeled assignment only; never claims a broker assignment."""
        def fn(db,p):
            if not p.get('short'):return {'status':'BLOCKED','reason':'NO_SHORT_TO_ASSIGN'}
            proceeds=num(p['short']['strike'])*100
            dividend=num(dividend_per_share)*100
            p.update(status='ASSIGNED_SCENARIO',short_stock=-100,dividend_liability=float(dividend),
                assignment_strike=p['short']['strike'],assignment_mode='MODELED_SCENARIO',short=None)
            p['cash_basis']=float(num(p['cash_basis'])-proceeds)
            p['market_value']=float(num(p['long']['bid'])*100-num(stock_price)*100-dividend)
            db.execute('UPDATE account SET cash=cash+? WHERE id=1',(cents(proceeds),))
            return {'status':'ASSIGNED_SCENARIO','short_stock':-100,'dividend_liability':float(dividend),'not_a_broker_event':True}
        return self.change(pid,key,now,fn)
    def reconcile(self,pid,long_bid,stock_ask,key,now):
        def fn(db,p):
            if p.get('short_stock')!=-100:return {'status':'BLOCKED','reason':'NO_ASSIGNMENT_LIABILITY'}
            value=num(long_bid)*100-num(stock_ask)*100-num(p.get('dividend_liability',0))
            fee=self.fee();pnl=value-num(p['cash_basis'])-num(p['fees'])-fee
            p.update(status='CLOSED',short_stock=0,dividend_liability=0,market_value=0,
                closed_at=now.isoformat(),exit_reason='MODELED_ASSIGNMENT_RECONCILED',
                fees=float(num(p['fees'])+fee),realized_pnl=float(pnl.quantize(num('.01'))))
            db.execute('UPDATE account SET cash=cash+? WHERE id=1',(cents(value-fee),))
            return {'status':'CLOSED','realized_pnl':p['realized_pnl'],'not_a_broker_event':True}
        return self.change(pid,key,now,fn)
