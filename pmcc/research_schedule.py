"""Date-bound regular-session cron plans; no broker orders or priced close fills."""
import fcntl,json,sqlite3,subprocess
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from pmcc.runtime import atomic,read_json
from pmcc.ledger import validate_safety,SAFETY
from pmcc.strategy import stamp
from pmcc.market_clock import market_session

CT=ZoneInfo('America/Chicago')
ET=ZoneInfo('America/New_York')
INSTALL_ROOT=Path('/home/tradebot/Trade-PMCC-Bot')
PREFIX='cd /home/tradebot/Trade-PMCC-Bot && /usr/bin/python3 -m pmcc '
SUFFIX=' >/dev/null 2>&1 # '
MARKERS=('PMCC_RESEARCH_RISK_001','PMCC_RESEARCH_SCHEDULE_001','PMCC_RESEARCH_CLOSE_001','PMCC_RESEARCH_CALENDAR_RETRY_001')
PLANNER_LINE='15,20,25 8 * * 1-5 '+PREFIX+'research-schedule --install'+SUFFIX+MARKERS[1]
RECOVERY_LINE='*/5 8-14 * * 1-5 '+PREFIX+'research-schedule --install'+SUFFIX+MARKERS[3]

def _safe(root):
    for name in ['pmcc-001.json','pmcc-research.json']:
        validate_safety(read_json(Path(root)/'configs'/name,{}))

def build_plan(session,now):
    result=dict(date=now.astimezone(ET).date().isoformat(),generated_at=now.isoformat(),
                session=session,risk_minutes=0,cron_lines=[],timezone='America/Chicago',
                status='CALENDAR_UNAVAILABLE',paper_entry_requested=False,**SAFETY)
    if market_session(session,now)=='DATA_UNAVAILABLE':return result
    if session.get('is_trading_day') is False:return {**result,'status':'NO_SESSION'}
    op=stamp(session['open']).astimezone(CT);cl=stamp(session['close']).astimezone(CT)
    if op.second or cl.second or op.microsecond or cl.microsecond or op.date()!=cl.date():return result
    # Date-bound cron must never use both restricted day-of-month and day-of-week.
    # The runtime gate also validates the calendar date before any market requests.
    start=max(op,now.astimezone(CT))
    if start.second or start.microsecond:start=start.replace(second=0,microsecond=0)+timedelta(minutes=1)
    minutes={};cursor=start
    while cursor<cl:
        minutes.setdefault(cursor.hour,[]).append(cursor.minute);cursor+=timedelta(minutes=1)
    for hour,values in sorted(minutes.items()):
        spec='*' if len(values)==60 else str(values[0]) if len(values)==1 else str(values[0])+'-'+str(values[-1])
        result['cron_lines'].append(f'{spec} {hour} {op.day} {op.month} * '+PREFIX+'research-risk --scheduled'+SUFFIX+MARKERS[0])
    if now<=cl:
        result['cron_lines'].append(f'{cl.minute} {cl.hour} {cl.day} {cl.month} * '+PREFIX+'research-close'+SUFFIX+MARKERS[2])
    result.update(status='PLANNED',risk_minutes=sum(map(len,minutes.values())),review_at=cl.isoformat())
    return result

def install_plan(root,plan):
    if Path(root).resolve()!=INSTALL_ROOT:raise ValueError('SCHEDULER_INSTALL_ROOT_MISMATCH')
    tz=subprocess.run(['timedatectl','show','--property=Timezone','--value'],capture_output=True,text=True,check=True).stdout.strip()
    if tz!='America/Chicago':raise ValueError('SCHEDULER_TIMEZONE_MISMATCH')
    prior=subprocess.run(['crontab','-l'],capture_output=True,text=True)
    if prior.returncode not in [0,1]:raise ValueError('CRONTAB_READ_FAILED')
    kept=[line for line in prior.stdout.splitlines() if not any(line.rstrip().endswith('# '+marker) for marker in MARKERS)]
    planned=[PLANNER_LINE]+plan['cron_lines']
    if plan['status']=='CALENDAR_UNAVAILABLE':planned.append(RECOVERY_LINE)
    content='\n'.join(kept+planned)+'\n'
    if subprocess.run(['crontab','-l'],capture_output=True,text=True).stdout!=prior.stdout:
        raise ValueError('CONCURRENT_CRONTAB_CHANGE')
    subprocess.run(['crontab','-'],input=content,text=True,check=True)
    return planned

def plan_day(root,now,api=None,install=False):
    from pmcc.collector import ReadOnlyAlpaca,session_from_calendar
    root=Path(root);_safe(root);(root/'state/research').mkdir(parents=True,exist_ok=True)
    path=root/'state/research/session_schedule.json';day=now.astimezone(ET).date()
    with (root/'state/research/schedule.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        prior=read_json(path,{})
        if prior.get('date')==str(day) and prior.get('status') in ['PLANNED','NO_SESSION']:
            if install:install_plan(root,prior)
            return {**prior,'status':'ALREADY_PLANNED'}
        try:
            cfg=read_json(root/'configs/pmcc-001.json',{})
            api=api or ReadOnlyAlpaca(cfg.get('market_data_environment_file','/etc/trade-option-bot.env'))
            calendar=api.get('metadata','/v2/calendar',{'start':str(day-timedelta(days=10)),'end':str(day+timedelta(days=10))})
            s=session_from_calendar(calendar,day)
            plan=build_plan(s,now)
        except (ValueError,OSError,KeyError,TypeError,AttributeError):
            plan=build_plan(None,now)
        # A failed calendar lookup clears old minute entries and retains planner retries.
        if install:install_plan(root,plan)
        atomic(path,plan);atomic(root/'results/pmcc_research_schedule_status.json',plan)
        return plan

def run_scheduled(root,now):
    from pmcc.research_monitor import run_monitor
    root=Path(root);_safe(root)
    s=read_json(root/'state/research/session_schedule.json',{}).get('session')
    status=market_session(s,now)
    if status!='OPEN':return dict(status='SESSION_CLOSED' if status=='CLOSED' else 'SESSION_UNAVAILABLE',
                                  actions=[],paper_entry_requested=False,**SAFETY)
    return run_monitor(root,now)

def close_review(root,now):
    root=Path(root);_safe(root);(root/'state/research').mkdir(parents=True,exist_ok=True)
    path=root/'results/pmcc_research_close_review.json'
    with (root/'state/research/close-review.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        plan=read_json(root/'state/research/session_schedule.json',{});s=plan.get('session')
        if market_session(s,now)=='DATA_UNAVAILABLE' or s.get('is_trading_day') is False:
            return {'status':'SESSION_UNAVAILABLE'}
        if now<stamp(s['close']):return {'status':'BEFORE_CLOSE'}
        day=s['date']
        if read_json(path,{}).get('date')==day:return {'status':'ALREADY_REVIEWED','date':day}
        rows=[]
        for dbpath in sorted((root/'state/research').glob('*.sqlite3')):
            with sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True) as db:
                for record in db.execute('SELECT payload FROM positions'):
                    p=json.loads(record[0])
                    if p.get('status')=='CLOSED':continue
                    short=p.get('short');reason='OPEN_OVERNIGHT_EXPOSURE'
                    if p.get('short_stock') or p.get('status')=='EXPIRY_RECONCILIATION_REQUIRED':reason='RECONCILIATION_REQUIRED'
                    elif short and short['expiration']<=day:reason='EXPIRATION_REVIEW_REQUIRED'
                    rows.append(dict(study=dbpath.stem,position_id=p['id'],symbol=p['symbol'],review_reason=reason,
                        status=p['status'],short_expiration=short.get('expiration') if short else None,
                        short_call_assignment_exposure=short is not None,last_mark_at=p.get('last_mark_at'),quote_status=p.get('quote_status')))
        result=dict(status='CLOSE_REVIEW_COMPLETE',date=day,generated_at=now.isoformat(),open_count=len(rows),
                    positions=rows,prices_refreshed=False,paper_entry_requested=False,actions=[],**SAFETY)
        atomic(path,result);return result
