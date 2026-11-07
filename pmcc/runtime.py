"""Process-locked cycles; only the local paper ledger can change positions."""
import fcntl,json,os,sqlite3,tempfile
from pathlib import Path
from datetime import date,datetime,timezone
from zoneinfo import ZoneInfo
from pmcc.provider import normalize_document
from pmcc.alpaca_data import normalize_alpaca,combine
from pmcc.strategy import evaluate_candidate,rank_candidates,stamp,num,fresh,long_horizon
from pmcc.ledger import Ledger,SAFETY,validate_safety
from pmcc.management import decide
from pmcc.market_clock import market_session
from pmcc.weekly_entry import advance,approved_symbols

def atomic(path,value,text=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    content=value if text else json.dumps(value,indent=2,allow_nan=False)+'\n'
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
        f.write(content);f.flush();os.fsync(f.fileno());tmp=f.name
    os.replace(tmp,path)

def read_json(path,default):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return default




def board(candidate):
    groups={
        'Financial Health':['FINANCIAL','GOING_CONCERN','BANKRUPTCY','DELISTING','DELINQUENT','EQUITY','ASSETS','EBIT','INTEREST'],
        'Opportunity Quality':['TREND','IV_','VOLUME','LIQUIDITY','STOCK_PRICE','UNSUPPORTED'],
        'Options Structure':['DELTA','GAMMA','THETA','VEGA','WIDTH','EXTRINSIC','CONTRACT','EXPIRATION','SPREAD','SHORT_'],
        'Risk and Events':['EARNINGS','DIVIDEND','EVENT','STALE','UNVERIFIED','BINARY'],
        'Entry Clearance':['ENTRY_','QUOTE','UNDERLYING']}
    decisions=[]
    for name,terms in groups.items():
        reasons=[r for r in candidate['reasons'] if any(t in r for t in terms)]
        decisions.append({'committee':name,'status':'DATA_UNAVAILABLE' if any(r.startswith(('MISSING','STALE','UNVERIFIED')) for r in reasons) else 'FAIL' if reasons else 'PASS' if candidate['status']=='PASS' else 'NOT_CLEARED','reasons':reasons})
    return {'symbol':candidate['symbol'],'status':candidate['status'],'committees':decisions,'reasons':candidate['reasons']}


def run_cycle(root,now,entry=False):
    root=Path(root);(root/'state').mkdir(parents=True,exist_ok=True)
    with (root/'state/cycle.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'CYCLE_ALREADY_RUNNING'}
        return _run(root,now,entry)


def _run(root,now,entry):
    cfg=read_json(root/'configs/pmcc-001.json',{});validate_safety(cfg)
    ledger=Ledger(root/'state/pmcc.sqlite3',cfg)
    try:raw=read_json(root/'inputs/robinhood_pmcc_research.json',{})
    except (ValueError,OSError):raw={}
    try:fallback=read_json(root/'inputs/alpaca_pmcc_research.json',{})
    except (ValueError,OSError):fallback={}
    alpaca=normalize_alpaca(fallback)
    normalized=combine(normalize_document(raw),alpaca,now,long_horizon(cfg));symbols=normalized['symbols'];session=normalized.get('session')
    if 'F' in symbols:
        try:review=read_json(root/'inputs/ford_paper_event_review.json',{})
        except (OSError,ValueError):review={}
        if isinstance(review,dict) and review:
            symbols['F']['evidence']['paper_event_review']=review
    ms=market_session(session,now);actions=[];alerts=[];requests=[]
    state=ledger.snapshot();opened=[p for p in state['positions'] if p['status']!='CLOSED']
    bucket=int(now.timestamp())//300
    for p in opened:
        payload=symbols.get(p['symbol'],{})
        e=payload.get('evidence',{});cs=payload.get('management_contracts',payload.get('contracts',[]))
        if ms!='OPEN':
            ledger.unavailable(p['id'],['MARKET_'+ms]);continue
        try:action=decide(p,e,cs,now,cfg,session)
        except (ValueError,TypeError,KeyError,ArithmeticError,AttributeError):action={'action':'REFRESH','reason':'MALFORMED_POSITION_EVIDENCE'}
        actions.append({'position_id':p['id'],**action})
        key=f'manage:{bucket}:{p["id"]}'
        if action['action']=='REFRESH':
            ledger.unavailable(p['id'],[action['reason']]);requests.append({'symbol':p['symbol'],'reason':action['reason']});continue
        if action['action']=='RECONCILE_ASSIGNMENT':
            alerts.append({'symbol':p['symbol'],'reason':'ASSIGNMENT_RECONCILIATION_REQUIRED'});continue
        ledger.mark(p['id'],action['long'],action['short'],e,now)
        if action['action']=='CLOSE_DIAGONAL':
            ledger.close(p['id'],action['long']['bid'],action['short']['ask'] if action['short'] else 0,key,now,action['reason'])
        elif action['action'] in ['ROLL_SHORT','OPEN_SHORT','CLOSE_SHORT']:
            ledger.short_action(p['id'],action.get('replacement'),action['short']['ask'] if action['short'] else 0,key,now,action['reason'])
        elif action['action']=='ALERT':alerts.append({'symbol':p['symbol'],'reason':action['reason']})
    # Host market snapshot is a neutral shortlist only, never entry evidence.
    discovered=set()
    try:
        host=read_json(cfg.get('discovery_source','/nonexistent'),{})
        for item in host.get('records',[]):
            if isinstance(item,dict) and item.get('symbol') and item.get('quote_type') in ['EQUITY','COMMON_STOCK'] and item.get('price') is not None and 5<=num(item['price'])<=20:
                discovered.add(item['symbol'])
    except (ValueError,OSError,AttributeError):pass
    universe=sorted(set(cfg.get('universe',[]))|set(symbols)|{p['symbol'] for p in opened}|discovered)
    rows=[]
    for symbol in universe:
        payload=symbols.get(symbol,{})
        try:row=evaluate_candidate(symbol,payload.get('evidence',{}),payload.get('contracts',[]),now,cfg)
        except (ValueError,TypeError,KeyError,ArithmeticError,AttributeError):row={'symbol':symbol,'status':'DATA_UNAVAILABLE','reasons':['MALFORMED_SYMBOL_EVIDENCE']}
        if payload.get('reasons'):row.update(status='DATA_UNAVAILABLE',reasons=sorted(set(row['reasons']+payload['reasons'])))
        if normalized['reasons']:row.update(status='DATA_UNAVAILABLE',reasons=normalized['reasons'])
        rows.append(row)
        if row['status']=='DATA_UNAVAILABLE':requests.append({'symbol':symbol,'reasons':row['reasons']})
    ranked=rank_candidates(rows)
    et=now.astimezone(ZoneInfo('America/New_York'))
    entry_window=et.hour==10 and et.minute<5
    weekly=cfg.get('weekly_entries') is True
    queue={}
    approved={r['symbol'] for r in ranked}
    if weekly:
        path=root/'state/weekly_entry_queue.json'
        try:prior=read_json(path,{})
        except (OSError,ValueError):prior={}
        if not isinstance(prior,dict):prior={}
        closing_evidence={symbol:p.get('evidence',{}) for symbol,p in alpaca['symbols'].items()
                          if not p.get('reasons')}
        live_evidence={symbol:p.get('evidence',{}) for symbol,p in symbols.items()
                       if not p.get('reasons')}
        queue=advance(prior,session,now,ranked,closing_evidence,live_evidence)
        if queue!=prior:atomic(path,queue)
        approved=approved_symbols(queue,session,now,ranked) if entry_window else set()
    held_marks_valid=all(p.get('quote_status')=='CURRENT' for p in ledger.snapshot()['positions'] if p['status']!='CLOSED')
    from pmcc.linked_itm import candidate_for_open, record_openings
    try:research_cfg=read_json(root/'configs/pmcc-research.json',{})
    except (OSError,ValueError):research_cfg={}
    linked=isinstance(research_cfg,dict) and research_cfg.get('linked_itm_shadow') is True
    for row in ranked:
        if entry and entry_window and ms=='OPEN' and held_marks_valid and row['symbol'] in approved:
            if linked:
                row={**row,'linked_itm_intent':candidate_for_open(row['symbol'],symbols[row['symbol']],now,cfg)}
            action=ledger.open(row,f'entry:{et.date()}:{row["symbol"]}',now);actions.append(action)
    state=ledger.snapshot();positions=[p for p in state['positions'] if p['status']!='CLOSED'];closed=[p for p in state['positions'] if p['status']=='CLOSED']
    linked_report=None
    if linked:
        try:linked_report=record_openings(root,state,now,research_cfg)
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
            alerts.append({'reason':'LINKED_ITM_SHADOW_UNAVAILABLE'})
    degraded=bool(requests or normalized['reasons'] or ms=='DATA_UNAVAILABLE')
    if ms=='DATA_UNAVAILABLE':requests.append({'reason':'VERIFIED_MARKET_CALENDAR_REQUIRED'})
    status='DATA_UNAVAILABLE' if degraded else 'READY' if ms=='OPEN' else 'MARKET_CLOSED'
    realized=round(sum(p.get('realized_pnl',0) for p in closed),2)
    # Use campaign cash basis, including realized short cashflows, for open P/L.
    unrealized=round(sum(float(num(p['market_value'])-num(p['cash_basis'])-num(p['fees'])) for p in positions),2)
    summary=dict(schema_version='1.0',strategy_id='PMCC-001',display_name='Trade PMCC Bot',strategy_type='PMCC',
        generated_at=now.isoformat(),display_time=now.astimezone(ZoneInfo('America/Chicago')).isoformat(),
        status=status,health='DEGRADED' if degraded else 'HEALTHY',market_session=ms,
        allocated_capital=state['initial_capital'],portfolio_value=state['equity'],cash_balance=state['cash'],
        invested_capital=round(sum(p['gross_long_cost'] for p in positions),2),
        positions_market_value=round(sum(p['market_value'] for p in positions),2),
        realized_gain=realized,unrealized_gain=unrealized,total_pnl=round(state['equity']-state['initial_capital'],2),
        position_count=len(positions),open_count=len(positions),completed_count=len(closed),watchlist_count=len(ranked),
        opportunity_quality=ranked[0]['opportunity_quality'] if ranked else None,
        win_rate_percent=round(100*sum(p['realized_pnl']>0 for p in closed)/len(closed),2) if closed else None,
        entry_window_open=entry_window and ms=='OPEN' and (not weekly or bool(approved)),source=normalized.get('source','ROBINHOOD_MCP_RESEARCH'),data_reasons=normalized['reasons'],
        fill_model='LONG_ASK_SHORT_BID_ENTRY_LONG_BID_SHORT_ASK_EXIT',fee_per_contract=cfg.get('fee_per_contract',.65),
        **SAFETY)
    observations=[{**c,'quote_status':'CURRENT' if fresh(c.get('quote_time'),now) else 'STALE_OR_UNAVAILABLE'} for payload in symbols.values() for c in payload.get('contracts',[])]
    report={**summary,'contract_observations':observations,'discovery':rows,'watchlist':ranked,'board':[board(r) for r in rows],
        'positions':positions,'completed':closed,'actions':actions,'alerts':alerts,'events':state['events'],
        'weekly_entry':{'enabled':weekly,'queue':queue,'approved_symbols':sorted(approved) if weekly else []},
        'settings':cfg,'dashboard_sections':{'holdings':{'open_positions':positions}},
        'decision_journal':{'decisions':state['events'],'decision_count':len(state['events'])}}
    result=root/'results'
    if linked_report is not None:report['linked_itm_shadow']=linked_report
    atomic(result/'pmcc_refresh_request.json',dict(strategy_id='PMCC-001',requested_at=now.isoformat(),status='REQUESTED' if requests else 'SATISFIED',
        research_only=True,order_submission_enabled=False,requests=requests,
        requirements=['chains and instrument IDs for long/short expiries','current quotes and Greeks for all held and candidate contracts','prior-session contract volume with date','252-session constant-maturity IV history','verified earnings/ex-dividend/issuer fundamentals','verified session calendar'],
        destination=str(root/'inputs/robinhood_pmcc_research.json'),collector_boundary='EXTERNAL_READ_ONLY_MCP_COLLECTOR'))
    from pmcc.dashboard import sections
    report['workspace_panels']=sections(report)
    comparison={'evaluated_at':now.isoformat(),'mode':'SHADOW_NO_FILLS','classic':[
        {k:r[k] for k in ('symbol','status','reasons')} for r in rows], 'variants':{}}
    for profile in ('DYNAMIC_OTM','DYNAMIC_ITM'):
        variants=[]
        for symbol in universe:
            payload=symbols.get(symbol,{})
            blockers=normalized['reasons'] or payload.get('reasons')
            try:
                v=({'symbol':symbol,'status':'DATA_UNAVAILABLE','reasons':blockers} if blockers else
                    evaluate_candidate(symbol,payload.get('evidence',{}),payload.get('contracts',[]),now,cfg,profile))
            except (ValueError,TypeError,KeyError,ArithmeticError,AttributeError):
                v={'symbol':symbol,'status':'DATA_UNAVAILABLE','reasons':['MALFORMED_SYMBOL_EVIDENCE']}
            variants.append({k:v[k] for k in ('symbol','status','reasons','long','short','net_debit','width_cushion') if k in v})
        comparison['variants'][profile]=variants
    atomic(result/'pmcc_strategy_comparison.json',comparison)
    atomic(result/'pmcc_report.json',report)
    with ledger.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS cycles(bucket INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO cycles VALUES(?,?)',(bucket,json.dumps({'summary':summary,'board':report['board'],'actions':actions},allow_nan=False)))
    atomic(result/'pmcc_summary.json',summary)
    atomic(result/'pmcc_watchlist.json',{'strategy_id':'PMCC-001','watchlist':ranked})
    atomic(result/'pmcc_positions.json',{'strategy_id':'PMCC-001','dashboard_sections':report['dashboard_sections']})
    atomic(result/'pmcc_board.json',{'strategy_id':'PMCC-001','board':report['board']})
    from pmcc.dashboard import render
    atomic(result/'pmcc_dashboard.html',render(report),text=True)
    return summary
