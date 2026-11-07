"""Refresh and manage isolated studies. Never authorize new paper entries."""
import fcntl,json,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from pmcc.runtime import atomic,read_json
from pmcc.ledger import validate_safety,SAFETY

def run_monitor(root,now,clock=None):
    from pmcc.collector import collect
    from pmcc.research import run_research
    root=Path(root);(root/'state/research').mkdir(parents=True,exist_ok=True)
    status_path=root/'results/pmcc_research_risk_status.json'
    clock=clock or (lambda:datetime.now(timezone.utc))
    cfg=read_json(root/'configs/pmcc-research.json',{});validate_safety(cfg)
    status=dict(started_at=now.isoformat(),paper_entry_requested=False,actions=[],**SAFETY)
    with (root/'state/research/risk-monitor.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {**status,'status':'ALREADY_RUNNING'}
        try:
            count=0
            for path in (root/'state/research').glob('*.sqlite3'):
                with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
                    count+=sum(json.loads(r[0]).get('status')!='CLOSED' for r in db.execute('SELECT payload FROM positions'))
            collection=collect(root,now,clock=clock,research=True) if count else {'status':'NO_OPEN_POSITIONS'}
            status.update(collection_status=collection['status'],held_before=count)
            if not count:
                from pmcc.research_risk import policy
                status.update(status='MONITORED',open_count=0,risk_policy=policy(cfg))
            elif collection['status'] in ['UNAVAILABLE','SESSION_UNAVAILABLE']:
                status.update(status='REFRESH_FAILED',open_count=count)
            else:
                r=run_research(root,clock(),enter=False)
                unavailable=any(v['performance']['status']=='DATA_UNAVAILABLE' for v in r.get('variants',[]))
                status.update(status='DATA_UNAVAILABLE' if unavailable else 'MONITORED',open_count=r.get('open_count',count),
                              market_session=r.get('market_session'),actions=r.get('actions',[]),risk_policy=r.get('risk_policy'))
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):
            # Provider/credential details never enter the public heartbeat.
            status.update(status='REFRESH_FAILED',reason='RISK_CYCLE_DATA_OR_STORAGE_UNAVAILABLE')
        status['completed_at']=clock().isoformat();atomic(status_path,status)
        return status
