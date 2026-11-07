import argparse,json,sys
from pathlib import Path
from datetime import datetime,timezone
from pmcc.runtime import run_cycle,atomic,read_json
from pmcc.provider import normalize_document
from pmcc.ledger import validate_safety

def main():
    p=argparse.ArgumentParser(description='PMCC-001 paper-only local strategy')
    p.add_argument('command',choices=['cycle','manage','ingest','status','collection-plan','refresh','research','research-risk','research-schedule','research-close'])
    p.add_argument('--root',default=str(Path(__file__).resolve().parents[1]))
    p.add_argument('--source');p.add_argument('--paper-entry',action='store_true',help='Allow eligible isolated research paper fills; research command only')
    p.add_argument('--entry-variant',help='Restrict explicit research paper entries to one variant')
    p.add_argument('--scheduled',action='store_true',help='Apply verified session gate; research-risk only')
    p.add_argument('--install',action='store_true',help='Install date-bound user cron; research-schedule only')
    a=p.parse_args();root=Path(a.root)
    if a.paper_entry and a.command!='research':p.error('--paper-entry is only supported by research')
    if a.entry_variant and not a.paper_entry:p.error('--entry-variant requires --paper-entry')
    if a.scheduled and a.command!='research-risk':p.error('--scheduled is only supported by research-risk')
    if a.install and a.command!='research-schedule':p.error('--install is only supported by research-schedule')
    cfg=read_json(root/'configs/pmcc-001.json',{});validate_safety(cfg)
    if a.command in ['research-schedule','research-close']:
        from pmcc.research_schedule import plan_day,close_review
        r=plan_day(root,datetime.now(timezone.utc),install=a.install) if a.command=='research-schedule' else close_review(root,datetime.now(timezone.utc))
        print(json.dumps(r,indent=2));return 1 if r['status']=='CALENDAR_UNAVAILABLE' else 0
    elif a.command=='research-risk':
        from pmcc.research_monitor import run_monitor
        from pmcc.research_schedule import run_scheduled
        r=(run_scheduled if a.scheduled else run_monitor)(root,datetime.now(timezone.utc));print(json.dumps(r,indent=2))
        return 1 if r['status']=='REFRESH_FAILED' else 0
    elif a.command=='research':
        from pmcc.research import run_research
        r=run_research(root,datetime.now(timezone.utc),enter=a.paper_entry,entry_variant=a.entry_variant)
        print(json.dumps({k:v for k,v in r.items() if k!='variants'}|{'variants':[
            {k:v for k,v in row.items() if k not in ['positions','events','candidates']}|{'candidate_count':len(row['candidates']),
             'top_candidates':row['candidates'][:3]} for row in r.get('variants',[])]},indent=2))
    elif a.command=='refresh':
        from pmcc.collector import collect
        print(json.dumps(collect(root,datetime.now(timezone.utc)),indent=2))
    elif a.command=='ingest':
        if not a.source:p.error('--source is required for ingest')
        d=json.loads(Path(a.source).read_text());n=normalize_document(d)
        if n['reasons']:raise ValueError(','.join(n['reasons']))
        atomic(root/'inputs/robinhood_pmcc_research.json',d)
        print(json.dumps({'status':'INGESTED','symbols':list(n['symbols']),'quote_timestamps_preserved':True}))
    elif a.command=='status':print(json.dumps(read_json(root/'results/pmcc_summary.json',{'status':'NOT_RUN'}),indent=2))
    elif a.command=='collection-plan':
        print(json.dumps(read_json(root/'results/pmcc_refresh_request.json',{'status':'NOT_RUN'}),indent=2))
    else:print(json.dumps(run_cycle(root,datetime.now(timezone.utc),entry=a.command=='cycle'),indent=2))
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError) as e:print(json.dumps({'status':'BLOCKED','reason':str(e)}),file=sys.stderr);raise SystemExit(65)
