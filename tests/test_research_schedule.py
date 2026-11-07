import json,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from pmcc.research_schedule import build_plan,run_scheduled,close_review,plan_day
from tests.test_research import NOW,sample
from pmcc.research import run_research


def session(day='2026-11-05',close='16:00',summer=False):
    offset='-04:00' if summer else '-05:00'
    d=datetime.fromisoformat(day).date()
    return dict(date=day,open=day+'T09:30:00'+offset,close=day+'T'+close+':00'+offset,
                verified=True,source='ALPACA_CALENDAR',previous_session_date=str(d-timedelta(days=1)),
                next_session_date=str(d+timedelta(days=1)),is_trading_day=True)


class ScheduleTests(unittest.TestCase):
    def test_regular_day_exactly_390_checks_and_one_close_review(self):
        p=build_plan(session(),datetime.fromisoformat('2026-11-05T14:15:00Z'))
        self.assertEqual(p['risk_minutes'],390)
        self.assertEqual(p['review_at'],'2026-11-05T15:00:00-06:00')
        self.assertTrue(any('30-59 8 5 11 *' in x for x in p['cron_lines']))
        self.assertEqual(sum('research-close' in x for x in p['cron_lines']),1)
        self.assertTrue(all(' 5 11 * ' in x for x in p['cron_lines']))

    def test_half_day_stops_at_noon_central(self):
        p=build_plan(session('2026-11-27','13:00'),datetime.fromisoformat('2026-11-27T14:15:00Z'))
        self.assertEqual(p['risk_minutes'],210)
        self.assertEqual(p['review_at'],'2026-11-27T12:00:00-06:00')
        self.assertFalse(any('research-risk' in x and x.split()[1] in ['12','13','14','15'] for x in p['cron_lines']))

    def test_summer_session_still_opens_at_830_central(self):
        p=build_plan(session('2026-07-06',summer=True),datetime.fromisoformat('2026-07-06T13:15:00Z'))
        self.assertEqual(p['risk_minutes'],390)
        self.assertIn('30-59 8 6 7 *',p['cron_lines'][0])

    def test_evening_after_utc_midnight_keeps_new_york_session_date(self):
        now=datetime.fromisoformat('2026-11-05T01:26:00Z')
        s=session('2026-11-04')
        p=build_plan(s,now)
        self.assertEqual(p['status'],'PLANNED')
        self.assertEqual(p['date'],'2026-11-04')
        self.assertEqual(p['risk_minutes'],0)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.write_plan(root,s)
            self.assertEqual(close_review(root,now)['status'],'CLOSE_REVIEW_COMPLETE')

    def test_holiday_unverified_wrong_day_and_after_close_do_not_schedule_risk(self):
        cases=[]
        for key,value in [('is_trading_day',False),('verified',False),('date','2026-11-04')]:
            s=session();s[key]=value;cases.append((s,datetime.fromisoformat('2026-11-05T14:15:00Z')))
        cases.append((session(),datetime.fromisoformat('2026-11-05T22:00:00Z')))
        for s,now in cases:
            with self.subTest(session=s,now=now):
                p=build_plan(s,now)
                self.assertEqual(p['risk_minutes'],0)
                self.assertFalse(any('research-risk' in x for x in p['cron_lines']))

    def write_plan(self,root,s):
        (root/'state/research').mkdir(parents=True,exist_ok=True)
        (root/'state/research/session_schedule.json').write_text(json.dumps({'session':s}))

    def test_scheduled_guard_skips_after_close_without_collecting_or_research(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.write_plan(root,session())
            with patch('pmcc.research_monitor.run_monitor') as monitor:
                r=run_scheduled(root,datetime.fromisoformat('2026-11-05T21:01:00Z'))
            self.assertEqual(r.get('status'),'SESSION_CLOSED')
            monitor.assert_not_called()
            self.assertFalse((root/'results/pmcc_research_risk_status.json').exists())

    def test_scheduled_guard_runs_only_verified_open_session(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.write_plan(root,session())
            with patch('pmcc.research_monitor.run_monitor',return_value={'status':'MONITORED'}) as monitor:
                r=run_scheduled(root,datetime.fromisoformat('2026-11-05T15:00:00Z'))
            self.assertEqual(r.get('status'),'MONITORED');monitor.assert_called_once()

    def test_close_review_once_unpriced_with_unresolved_positions_and_no_orders(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'inputs').mkdir()
            (root/'inputs/pmcc_research_observations.json').write_text(json.dumps(sample()))
            run_research(root,NOW,enter=True)
            self.write_plan(root,session('2026-11-03'))
            after=datetime.fromisoformat('2026-11-03T21:00:00Z')
            with patch('pmcc.collector.collect') as collector,patch('pmcc.research.run_research') as research:
                r=close_review(root,after);again=close_review(root,after+timedelta(minutes=2))
            self.assertEqual(r.get('status'),'CLOSE_REVIEW_COMPLETE')
            self.assertEqual(r['open_count'],1)
            self.assertEqual(r['positions'][0]['review_reason'],'OPEN_OVERNIGHT_EXPOSURE')
            self.assertFalse(r['prices_refreshed']);self.assertFalse(r['paper_entry_requested'])
            self.assertEqual(again.get('status'),'ALREADY_REVIEWED')
            collector.assert_not_called();research.assert_not_called()

    def test_calendar_planner_fetches_once_and_reuses_verified_daily_plan(self):
        class API:
            def get(self,*args):return [dict(date='2026-11-04',open='09:30',close='16:00'),
                dict(date='2026-11-05',open='09:30',close='16:00'),dict(date='2026-11-06',open='09:30',close='16:00')]
        with tempfile.TemporaryDirectory() as td:
            now=datetime.fromisoformat('2026-11-05T14:15:00Z');api=API()
            with patch.object(api,'get',wraps=api.get) as get:
                r=plan_day(Path(td),now,api=api);again=plan_day(Path(td),now+timedelta(minutes=5),api=api)
            self.assertEqual(r.get('risk_minutes'),390)
            self.assertEqual(again.get('status'),'ALREADY_PLANNED')
            get.assert_called_once()

    def test_empty_portfolio_does_not_rescan_full_universe(self):
        from pmcc.research_monitor import run_monitor
        with tempfile.TemporaryDirectory() as td:
            with patch('pmcc.research.run_research',wraps=run_research) as research,patch('pmcc.collector.collect') as collector:
                r=run_monitor(Path(td),NOW,clock=lambda:NOW)
            self.assertEqual(r['open_count'],0)
            research.assert_not_called();collector.assert_not_called()

    def test_calendar_failure_clears_old_jobs_and_can_retry(self):
        class API:
            def get(self,*args):raise OSError('offline')
        with tempfile.TemporaryDirectory() as td:
            now=datetime.fromisoformat('2026-11-05T14:15:00Z')
            with patch('pmcc.research_schedule.install_plan') as install:
                r=plan_day(Path(td),now,api=API(),install=True)
            self.assertEqual(r['status'],'CALENDAR_UNAVAILABLE')
            self.assertEqual(install.call_args.args[1]['cron_lines'],[])
            self.assertEqual(run_scheduled(Path(td),now)['status'],'SESSION_UNAVAILABLE')

    def test_crontab_install_preserves_unrelated_lines_and_replaces_owned_jobs(self):
        from pmcc.research_schedule import install_plan,INSTALL_ROOT,PLANNER_LINE
        import subprocess
        prior='0 16 * * * dividend-command\n* * * * * old-risk # PMCC_RESEARCH_RISK_001\n'
        calls=[]
        def run(args,**kw):
            calls.append((args,kw))
            if args[0]=='timedatectl':return subprocess.CompletedProcess(args,0,'America/Chicago\n','')
            return subprocess.CompletedProcess(args,0,prior,'')
        p=build_plan(session(),datetime.fromisoformat('2026-11-05T14:15:00Z'))
        with patch('pmcc.research_schedule.subprocess.run',side_effect=run):install_plan(INSTALL_ROOT,p)
        content=next(kw['input'] for args,kw in calls if args==['crontab','-'])
        self.assertIn('0 16 * * * dividend-command\n',content)
        self.assertNotIn('old-risk',content)
        self.assertEqual(content.count(PLANNER_LINE),1)
        self.assertEqual(content.count('research-close'),1)

    def test_calendar_failure_installs_daytime_recovery_without_risk_polling(self):
        from pmcc.research_schedule import install_plan,INSTALL_ROOT
        import subprocess
        writes=[]
        def run(args,**kw):
            if args[0]=='timedatectl':return subprocess.CompletedProcess(args,0,'America/Chicago\n','')
            if args==['crontab','-']:writes.append(kw['input'])
            return subprocess.CompletedProcess(args,0,'','')
        p=build_plan(None,datetime.fromisoformat('2026-11-05T14:15:00Z'))
        with patch('pmcc.research_schedule.subprocess.run',side_effect=run):install_plan(INSTALL_ROOT,p)
        self.assertTrue(any(line.startswith('*/5 8-14 * * 1-5 ') for line in writes[0].splitlines()))
        self.assertNotIn('research-risk',writes[0])

    def test_guard_skips_stale_plan_and_close_review_does_not_run_before_close(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.write_plan(root,session())
            with patch('pmcc.research_monitor.run_monitor') as monitor:
                r=run_scheduled(root,datetime.fromisoformat('2026-11-06T15:00:00Z'))
            self.assertEqual(r['status'],'SESSION_UNAVAILABLE');monitor.assert_not_called()
            self.assertEqual(close_review(root,datetime.fromisoformat('2026-11-05T15:00:00Z'))['status'],'BEFORE_CLOSE')
            self.assertFalse((root/'results/pmcc_research_close_review.json').exists())
