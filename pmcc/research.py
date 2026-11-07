"""Independent, explicitly labeled PMCC research; no broker-order capability."""
import copy,fcntl,json
from datetime import date,timedelta
from pathlib import Path
from pmcc.strategy import num,fresh,stamp,contract_reasons,evaluate_candidate,rank_candidates,ford_paper_event_reasons
from pmcc.market_clock import market_session
from pmcc.ledger import Ledger,SAFETY,validate_safety
from pmcc.research_risk import decision,policy,campaign_exit,dividend_date
from pmcc.linked_itm import PROFILE as LINKED_ITM_PROFILE

PROFILES=(('WEEKLY_OTM',7,14,'OTM'),('MONTHLY_OTM',30,45,'OTM'),('WEEKLY_ITM',7,14,'ITM'))
TIME_PREMIUM_PROFILE='LEAPS_TIME_PREMIUM'

def performance(snapshot,peak=5000,prior_drawdown=0):
    initial=float(num(snapshot['initial_capital']));equity=float(num(snapshot['equity']))
    peak=max(float(num(peak)),equity,initial);dd=max(float(num(prior_drawdown)),peak-equity)
    ps=snapshot['positions'];closed=[p for p in ps if p['status']=='CLOSED'];opened=[p for p in ps if p['status']!='CLOSED']
    current=all(p.get('quote_status')=='CURRENT' for p in opened)
    return dict(status='DATA_UNAVAILABLE' if not current else 'RUNNING' if ps else 'NOT_STARTED',initial_capital=initial,equity=equity,
        net_pnl=round(equity-initial,2),return_percent=round(100*(equity/initial-1),4),
        realized_pnl=round(sum(p.get('realized_pnl',0) for p in closed),2),
        fees=round(sum(p.get('fees',0) for p in ps),2),open_count=len(opened),completed_count=len(closed),
        win_rate_percent=round(100*sum(p['realized_pnl']>0 for p in closed)/len(closed),2) if closed else None,
        equity_peak=peak,max_drawdown_dollars=round(dd,2),
        quotes_current=all(p.get('quote_status')=='CURRENT' for p in opened),
        assignment_avoidance_events=sum(e.get('reason')=='ASSIGNMENT_AVOIDANCE' for e in snapshot['events']))

def _dte(c,now):return (date.fromisoformat(c['expiration'])-now.date()).days

def _event_blockers(e,s,now):
    if not all(e.get(k) is True for k in ['earnings_verified','events_verified','ex_dividend_verified']):return ['EVENT_EVIDENCE_UNAVAILABLE']
    try:
        if not fresh(e.get('evidence_at'),now,86400):return ['STALE_EVENT_EVIDENCE']
        if date.fromisoformat(e['next_earnings'])<=date.fromisoformat(s['expiration']):return ['EARNINGS_WINDOW']
        if 'next_ex_dividend' not in e:return ['EVENT_EVIDENCE_UNAVAILABLE']
        ex=dividend_date(e.get('next_ex_dividend'))
        if ex and 0<=(ex-now.date()).days<=1:return ['EX_DIVIDEND_IMMINENT']
    except (KeyError,ValueError,TypeError):return ['EVENT_EVIDENCE_UNAVAILABLE']
    return []

def scan_time_premium(document,now,cfg,session):
    cap=num(cfg['long_time_premium_cap_dollars'])
    if cap<=0:raise ValueError('INVALID_LONG_TIME_PREMIUM_CAP')
    rows=[]
    for symbol,payload in sorted(document.get('symbols',{}).items()):
        try:
            row=evaluate_candidate(symbol,payload['evidence'],payload['contracts'],now,cfg,
                long_time_premium_cap=cap)
        except (KeyError,ValueError,TypeError,ArithmeticError):
            row=dict(symbol=symbol,status='DATA_UNAVAILABLE',reasons=['MALFORMED_SYMBOL_EVIDENCE'])
        rows.append(row)
    candidates=[]
    for row in rank_candidates(rows):
        blockers=[] if session=='OPEN' else ['MARKET_'+session]
        e,l=row['evidence'],row['long']
        premium=(num(l['ask'])-max(num(e['price'])-num(l['strike']),num(0)))*100
        candidates.append(dict(row,variant=TIME_PREMIUM_PROFILE,
            status='OBSERVATION' if blockers else 'PAPER_ENTRY_ELIGIBLE',
            long_extrinsic_dollars=float(premium),entry_blockers=blockers,
            warnings=['TENTATIVE_EARNINGS'] if e.get('paper_event_review',{}).get('earnings',{}).get('verified') is False else []))
    return dict(id=TIME_PREMIUM_PROFILE,starting_capital=5000,shadow_only=True,
        short_dte=[30,45],short_type='OTM',roll_dte=[30,45],
        long_time_premium_cap_dollars=float(cap),iv_history_required=False,candidates=candidates,
        evaluations=[{k:row[k] for k in ('symbol','status','reasons')} for row in rows])

def scan(document,now,config=None):
    cfg=config or {};validate_safety(cfg);fee=float(num(cfg.get('fee_per_contract',.65)))
    if fee<0:raise ValueError('NEGATIVE_FEES')
    result=dict(generated_at=now.isoformat(),platform_allocation=5000,shadow_capital_is_not_platform_funding=True,
        winner=None,performance_status='FORWARD_OBSERVATION_REQUIRED',discovery_source='INDEPENDENT_PMCC_INPUT',
        market_session=market_session(document.get('session'),now),variants=[],similar_symbols=[],**SAFETY)
    for pid,lo,hi,kind in PROFILES:
        variant=dict(id=pid,starting_capital=5000,shadow_only=True,short_dte=[lo,hi],short_type=kind,candidates=[])
        for symbol,payload in sorted(document.get('symbols',{}).items()):
            try:
                e=payload['evidence'];spot=float(num(e['price']));cs=payload['contracts']
                if not 5<=spot<=20:continue
                longs=[c for c in cs if c.get('symbol')==symbol and c.get('right')=='call'
                    and 365<=_dte(c,now)<=730 and float(num(c['strike']))<spot
                    and float(num(c['ask']))>0 and (c.get('delta') is None or .8<=float(num(c['delta']))<=1)
                    and 0<=(float(num(c['ask']))-max(spot-float(num(c['strike'])),0))/float(num(c['ask']))<=.2]
                shorts=[c for c in cs if c.get('symbol')==symbol and c.get('right')=='call' and lo<=_dte(c,now)<=hi
                    and c.get('delta') is not None and (.15<=float(num(c['delta']))<=.5 if kind=='OTM' else .5<=float(num(c['delta']))<=1)
                    and (float(num(c['strike']))>spot if kind=='OTM' else float(num(c['strike']))<spot)]
                for l in longs:
                    for s in shorts:
                        a,b=float(num(l['ask'])),float(num(s['bid']));debit=(a-b)*100
                        if l['id']==s['id'] or not 0<debit<=1000 or float(num(s['strike']))<=float(num(l['strike'])):continue
                        ls=(a-float(num(l['bid'])))*100;ss=(float(num(s['ask']))-b)*100
                        if min(ls,ss)<0:continue
                        intrinsic=max(spot-float(num(s['strike'])),0)*100;time=b*100-intrinsic
                        longex=(a-max(spot-float(num(l['strike'])),0))*100
                        cushion=(float(num(s['strike']))-float(num(l['strike'])))*100-debit
                        roundtrip=ls+ss+4*fee
                        cyclecost=ls*_dte(s,now)/_dte(l,now)+ss+2*fee
                        blockers=contract_reasons(l,symbol,now)+contract_reasons(s,symbol,now)+_event_blockers(e,s,now)
                        if not isinstance(e.get('sector'),str) or not e['sector'].strip():blockers.append('SECTOR_UNAVAILABLE')
                        volume=e.get('avg_volume30',e.get('avg_volume20'))
                        if volume is None:blockers.append('STOCK_LIQUIDITY_UNAVAILABLE')
                        elif num(volume)<1000000:blockers.append('STOCK_LIQUIDITY')
                        if not fresh(e.get('price_time'),now):blockers.append('STALE_UNDERLYING')
                        for c in [l,s]:
                            if c.get('quote_instrument_id')!=c.get('id'):blockers.append('QUOTE_IDENTITY')
                            if c.get('can_open_position') is not True:blockers.append('CHAIN_OPENING_DISABLED')
                            if float(num(c.get('open_interest',0)))<100:blockers.append('LOW_OPEN_INTEREST')
                        if abs((stamp(l['quote_time'])-stamp(s['quote_time'])).total_seconds())>5:blockers.append('UNSYNCHRONIZED_QUOTES')
                        warnings=[]
                        if cushion<=4*fee+10:
                            warnings.append('NEGATIVE_EXERCISE_CUSHION' if cushion<0 else 'SMALL_EXERCISE_CUSHION')
                            # ITM is retained for analysis; not silently exempted from execution safety.
                            if kind=='ITM' and cfg.get('allow_itm_width_risk') is True:
                                warnings.append('ITM_EXERCISE_RISK_ACCEPTED_FOR_SHADOW_STUDY')
                            else:blockers.append('WIDTH_MARGIN')
                        if time<0:warnings.append('BID_BELOW_INTRINSIC');blockers.append('BID_BELOW_INTRINSIC')
                        if time<=cyclecost:blockers.append('COST_EXCEEDS_TIME_PREMIUM')
                        if ls/max(a*100,.01)>.1 or ss/max((float(num(s['ask']))+b)*50,.01)>.3:blockers.append('RELATIVE_SPREAD')
                        if e.get('iv_rank') is None:warnings.append('IV_RANK_UNAVAILABLE')
                        if e.get('next_ex_dividend') and e['next_ex_dividend']<=s['expiration']:warnings.append('DIVIDEND_MONITORING_REQUIRED')
                        if result['market_session']!='OPEN':blockers.append('MARKET_'+result['market_session'])
                        candidate=dict(symbol=symbol,variant=pid,status='OBSERVATION',long=l,short=s,evidence=e,
                            long_extrinsic_dollars=round(longex,2),short_credit_dollars=round(b*100,2),
                            short_intrinsic_dollars=round(intrinsic,2),short_time_premium_dollars=round(max(time,0),2),
                            net_debit=round(debit,2),gross_long_cost=round(a*100,2),exercise_cushion_dollars=round(cushion,2),
                            estimated_round_trip_cost_dollars=round(roundtrip,2),amortized_cycle_cost_dollars=round(cyclecost,2),
                            time_premium_after_estimated_cycle_cost=round(time-cyclecost,2),
                            score=round((time-cyclecost)/debit*30/_dte(s,now),6),
                            score_meaning='MONTH_EQUIVALENT_TIME_PREMIUM_LESS_COST_PROXY_NOT_FORECAST',
                            warnings=sorted(set(warnings)),entry_blockers=sorted(set(blockers)))
                        if not blockers:candidate['status']='PAPER_ENTRY_ELIGIBLE'
                        variant['candidates'].append(candidate)
            except (KeyError,ValueError,TypeError,ArithmeticError):continue
        variant['candidates'].sort(key=lambda r:(bool(r['entry_blockers']),-r['score'],r['symbol'],r['long']['id'],r['short']['id']))
        result['variants'].append(variant)
    for symbol,p in sorted(document.get('symbols',{}).items()):
        try:
            e=p['evidence'];price=float(num(e['price']));volume=float(num(e.get('avg_volume30',e.get('avg_volume20',0))))
            if not 5<=price<=20 or volume<1000000:continue
            matches=[c for v in result['variants'] for c in v['candidates'] if c['symbol']==symbol]
            result['similar_symbols'].append(dict(symbol=symbol,price=price,average_share_volume=volume,
                average_volume_basis=e.get('average_volume_basis','20_SESSIONS'),sector=e.get('sector'),
                observed_pair_count=len(matches),entry_eligible_pairs=sum(not c['entry_blockers'] for c in matches),
                similarity_basis='PRICE_LIQUIDITY_DEEP_ITM_TIME_COST_NOT_FUNDAMENTAL_RANK',
                best_observed_score=max((c['score'] for c in matches),default=None)))
        except (KeyError,ValueError,TypeError):continue
    result['similar_symbols'].sort(key=lambda r:(-r['entry_eligible_pairs'],-r['observed_pair_count'],-r['average_share_volume'],r['symbol']))
    if cfg.get('long_time_premium_cap_dollars') is not None:
        result['variants'].append(scan_time_premium(document,now,cfg,result['market_session']))
    return result


def run_research(root,now,enter=False,entry_variant=None):
    from pmcc.runtime import atomic,read_json
    root=Path(root);(root/'state/research').mkdir(parents=True,exist_ok=True)
    with (root/'state/research/study.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'RESEARCH_ALREADY_RUNNING'}
        cfg=read_json(root/'configs/pmcc-research.json',{});validate_safety(cfg);risk_policy=policy(cfg)
        linked_enabled=(cfg.get('linked_itm_shadow') is True
            or (root/'state/research'/f'{LINKED_ITM_PROFILE}.sqlite3').exists())
        baseline={}
        if cfg.get('long_time_premium_cap_dollars') is not None or linked_enabled:
            baseline=read_json(root/'configs/pmcc-001.json',{});validate_safety(baseline)
            if 'long_dte' in baseline:cfg['long_dte']=baseline['long_dte']
        explicit=read_json(root/'inputs/pmcc_research_observations.json',{'symbols':{}})
        # No CIO/Board shortlist or financial gates are read by this research command.
        from pmcc.provider import normalize_document
        from pmcc.alpaca_data import normalize_alpaca
        for raw,normalizer in [('alpaca_pmcc_research.json',normalize_alpaca),('robinhood_pmcc_research.json',normalize_document),('alpaca_pmcc_risk.json',normalize_alpaca)]:
            d=normalizer(read_json(root/'inputs'/raw,{}))
            for symbol,p in d.get('symbols',{}).items():
                dest=explicit.setdefault('symbols',{}).setdefault(symbol,{'evidence':{},'contracts':[]})
                # Do not combine identity metadata with another provider's quote.
                dest.setdefault('contracts',[]).extend(p.get('contracts',[]))
                if fresh(p.get('evidence',{}).get('price_time'),now):dest['evidence'].update(p['evidence'])
            if market_session(d.get('session'),now)!='DATA_UNAVAILABLE':explicit['session']=d['session']
        ford=explicit.get('symbols',{}).get('F')
        if ford is not None and cfg.get('long_time_premium_cap_dollars') is not None:
            review=read_json(root/'inputs/ford_paper_event_review.json',{})
            if isinstance(review,dict) and review:ford['evidence']['paper_event_review']=review
        r=scan(explicit,now,cfg);prior=read_json(root/'state/research/performance.json',{});allmetrics={};actions=[]
        if entry_variant is not None and not any(v['id']==entry_variant for v in r['variants']):
            raise ValueError('RESEARCH_VARIANT_UNAVAILABLE')
        if linked_enabled:
            r['variants'].append(dict(id=LINKED_ITM_PROFILE,starting_capital=5000,shadow_only=True,
                short_type='ITM',short_dte=[7,30],roll_dte=[7,30],candidates=[],
                opening_policy='SAME_CYCLE_AS_CLASSIC_PAPER_OPEN'))
        for v in r['variants']:
            linked=v['id']==LINKED_ITM_PROFILE
            capture=baseline.get('short_profit_capture') if linked else None
            exit_cfg=cfg
            if linked and baseline.get('campaign_profit_target') is not None:
                exit_cfg={**cfg,'risk_policy':{**cfg.get('risk_policy',{}),
                    'profit_fraction':baseline['campaign_profit_target']}}
            allow_entry=enter and (entry_variant==v['id'] or
                entry_variant is None and v['id'] not in (TIME_PREMIUM_PROFILE,LINKED_ITM_PROFILE))
            lc={**cfg,**SAFETY,'initial_capital':5000,'position_cap':.25,'sector_cap':.4,'cash_reserve':.2,'max_underlyings':4}
            if v['id']==LINKED_ITM_PROFILE:lc['strategy_id']=LINKED_ITM_PROFILE
            ledger=Ledger(root/'state/research'/(v['id']+'.sqlite3'),lc)
            for p in [p for p in ledger.snapshot()['positions'] if p['status']!='CLOSED']:
                if p.get('short_stock',0):
                    def assignment_required(db,position):
                        position.update(quote_status='DATA_UNAVAILABLE',next_action='VERIFY_ASSIGNMENT_LIABILITY',
                            data_reasons=['ASSIGNMENT_RECONCILIATION_REQUIRED'])
                        return dict(status='RECONCILIATION_REQUIRED',reason='ASSIGNMENT_RECONCILIATION_REQUIRED',cash_delta=0)
                    ledger.change(p['id'],f'assignment-reconcile:{v["id"]}:{p["id"]}',now,assignment_required)
                    continue
                expired=any(_dte(c,now)<0 for c in [p['long'],p.get('short')] if c)
                if p['status']=='EXPIRY_RECONCILIATION_REQUIRED' or expired:
                    def reconcile_required(db,position):
                        position.update(status='EXPIRY_RECONCILIATION_REQUIRED',quote_status='DATA_UNAVAILABLE',
                            next_action='VERIFY_EXPIRATION_AND_ASSIGNMENT',data_reasons=['OFFICIAL_EXPIRATION_RESOLUTION_REQUIRED'])
                        return {'status':'RECONCILIATION_REQUIRED','reason':'OFFICIAL_EXPIRATION_RESOLUTION_REQUIRED','cash_delta':0}
                    ledger.change(p['id'],f'expiry-reconcile:{v["id"]}:{p["id"]}',now,reconcile_required)
                    continue
                payload=explicit.get('symbols',{}).get(p['symbol'],{});e=payload.get('evidence',{});cs=payload.get('contracts',[])
                def find(old):
                    choices=[c for c in cs if c.get('id')==old.get('id') and c.get('quote_instrument_id')==old.get('id')]
                    def same(c):
                        keys=['symbol','expiration','right','exercise_style','settlement']
                        return all(c.get(k)==old.get(k) for k in keys) and num(c['strike'])==num(old['strike']) and num(c['multiplier'])==num(old['multiplier'])
                    return next((c for c in choices if not contract_reasons(c,p['symbol'],now,closing=True) and same(c)),None)
                l=find(p['long']);s=find(p['short']) if p.get('short') else None
                if r['market_session']!='OPEN' or not l or (p.get('short') and not s):
                    ledger.unavailable(p['id'],['CURRENT_MATCHED_QUOTES_REQUIRED']);continue
                ledger.mark(p['id'],l,s,e,now);key=f'research:{v["id"]}:{int(now.timestamp())//300}:{p["id"]}:{s["id"] if s else "long-only"}'
                if float(num(l['bid']))<=p['long_entry']*.75 or _dte(l,now)<=180:
                    actions.append(ledger.close(p['id'],l['bid'],s['ask'] if s else 0,key+':close',now,'LONG_RISK_EXIT'));continue
                priced_exit=campaign_exit(p,l,s,exit_cfg)
                if priced_exit['action']=='CLOSE':
                    actions.append(ledger.close(p['id'],l['bid'],s['ask'] if s else 0,key+':close',now,priced_exit['reason']));continue
                if not fresh(e.get('price_time'),now):
                    ledger.unavailable(p['id'],['CURRENT_UNDERLYING_REQUIRED_FOR_TESTED_SHORT']);continue
                try:
                    if num(e.get('price'))<=0:raise ValueError('INVALID_UNDERLYING')
                except (ValueError,TypeError,ArithmeticError):
                    ledger.unavailable(p['id'],['VALID_UNDERLYING_REQUIRED_FOR_TESTED_SHORT']);continue
                event_gate=_event_blockers
                if v['id'] in (TIME_PREMIUM_PROFILE,LINKED_ITM_PROFILE) and p['symbol']=='F' and 'paper_event_review' in e:
                    def event_gate(evidence,short,at):
                        return ford_paper_event_reasons(evidence,short['expiration'],at)
                risk=decision(p,l,s,e,cs,v,ledger.snapshot()['events'],now,exit_cfg,event_gate)
                if risk['action']=='CLOSE':
                    actions.append(ledger.close(p['id'],l['bid'],s['ask'] if s else 0,key+':close',now,risk['reason']));continue
                # Expiration and dividend exits take priority over extending a tested short.
                try:
                    ex=dividend_date(e.get('next_ex_dividend'))
                    imminent=ex and 0<=(ex-now.date()).days<=1
                except (TypeError,ValueError):
                    actions.append(ledger.close(p['id'],l['bid'],s['ask'] if s else 0,key+':close',now,'EVENT_DATE_UNAVAILABLE'));continue
                urgent=s and (_dte(s,now)<=1 or (float(num(e['price']))>float(num(s['strike'])) and imminent))
                if risk['action']=='ROLL' and not urgent:
                    actions.append(ledger.short_action(p['id'],risk['replacement'],s['ask'],key+':roll',now,risk['reason']));continue
                if s:
                    itm=float(num(e['price']))>float(num(s['strike']))
                    if linked:
                        short_profit=capture is not None and num(s['ask'])<=num(p['short_entry'])*(1-num(capture))
                    else:
                        short_profit=v['short_type']=='OTM' and float(num(s['ask']))<=p['short_entry']*.4
                    reason='ASSIGNMENT_AVOIDANCE' if itm and imminent else 'SHORT_EXPIRY_AVOIDANCE' if _dte(s,now)<=1 else 'SHORT_PROFIT_CAPTURE' if short_profit else None
                    if reason:actions.append(ledger.short_action(p['id'],None,s['ask'],key,now,reason))
                elif allow_entry:
                    choices=[c for c in v['candidates'] if c['symbol']==p['symbol'] and c['long']['id']==l['id'] and not c['entry_blockers']]
                    if choices:
                        c=choices[0];newbasis=p['cash_basis']-float(num(c['short']['bid']))*100
                        width_ok=(float(num(c['short']['strike']))-float(num(l['strike'])))*100>newbasis+p['fees']+10
                        itm_exception=v['short_type']=='ITM' and cfg.get('allow_itm_width_risk') is True
                        if width_ok or itm_exception:
                            reason='NEXT_SHORT_CYCLE' if width_ok else 'NEXT_SHORT_CYCLE_ITM_WIDTH_RISK'
                            actions.append(ledger.short_action(p['id'],c['short'],0,key,now,reason))
            exited={a.get('symbol') for a in actions if a.get('status')=='CLOSED'}
            if allow_entry and r['market_session']=='OPEN':
                for c in v['candidates']:
                    if not c['entry_blockers'] and c['symbol'] not in exited:
                        c={**c,'status':'PASS','evidence':{**c['evidence'],'research_variant':v['id'],'research_entry_warnings':c['warnings']}}
                        actions.append(ledger.open(c,f'research-entry:{now.date()}:{c["symbol"]}',now))
            state=ledger.snapshot();old=prior.get(v['id'],{})
            m=performance(state,old.get('equity_peak',5000),old.get('max_drawdown_dollars',0));allmetrics[v['id']]=m
            v.update(performance=m,positions=state['positions'],events=state['events'])
        r.update(open_count=sum(m['open_count'] for m in allmetrics.values()),actions=actions,
            risk_policy=risk_policy,
            paper_entry_requested=bool(enter),paper_entry_variant=entry_variant,winner=None,provisional_leader=None,
            comparison_note='No winner is inferred from premiums. Compare forward net equity, costs, drawdown and assignment handling; mark stale valuations unavailable.')
        atomic(root/'state/research/performance.json',allmetrics)
        atomic(root/'results/pmcc_research_report.json',r)
        return r
