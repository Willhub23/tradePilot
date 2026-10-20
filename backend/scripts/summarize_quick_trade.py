"""Read-only persisted Quick Trade diagnostics; no inference or broker operations."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

from app.services.strategy_diagnostics import load_evaluations, summarize_evaluations
from app.strategies.price_consistency import aware_time


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',default='.env')
    parser.add_argument('--from-json',help='Offline exported rows; skips database access')
    parser.add_argument('--since',help='Inclusive timezone-aware timestamp')
    parser.add_argument('--until',help='Exclusive timezone-aware timestamp')
    parser.add_argument('--max-time-skew-seconds',type=float,default=5)
    parser.add_argument('--meaningful-tp-fraction',type=float,default=.2,help='Diagnostic threshold only; never changes strategy rules')
    args=parser.parse_args(argv)
    if not math.isfinite(args.max_time_skew_seconds) or args.max_time_skew_seconds < 0:
        parser.error('max-time-skew-seconds must be finite and nonnegative')
    if not math.isfinite(args.meaningful_tp_fraction) or not 0 < args.meaningful_tp_fraction <= 1:
        parser.error('meaningful-tp-fraction must be in (0,1]')
    since,until=aware_time(args.since),aware_time(args.until)
    if (args.since and since is None) or (args.until and until is None):
        parser.error('date filters require timezone-aware timestamps')
    if since and until and since>=until: parser.error('since must be before until')
    if args.from_json:
        content=json.loads(Path(args.from_json).read_text())
        if isinstance(content,dict) and 'rows' not in content: parser.error('snapshot object must contain exported rows')
        rows=content['rows'] if isinstance(content,dict) else content
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows): parser.error('snapshot must contain a list of row objects')
        rows=[r for r in rows if (not since or aware_time(r.get('created_at')) is not None and aware_time(r['created_at'])>=since)
               and (not until or aware_time(r.get('created_at')) is not None and aware_time(r['created_at'])<until)]
        source={'type':'offline_snapshot','exported_at':content.get('exported_at') if isinstance(content,dict) else None}
    else:
        from dotenv import load_dotenv
        from sqlalchemy import create_engine
        load_dotenv(args.env_file)
        url=os.getenv('DATABASE_URL')
        if not url: parser.error('DATABASE_URL is not configured')
        engine=create_engine(url)
        try:
            rows=load_evaluations(engine,since=since,until=until)
        except Exception as exc:
            raise SystemExit(f'Read-only diagnostics failed ({type(exc).__name__}); verify database access/schema. No migration or runtime was started.') from None
        finally: engine.dispose()
        source={'type':'read_only_database','snapshot_completed_at':datetime.now(timezone.utc).isoformat()}
    report=summarize_evaluations(rows,max_time_skew=args.max_time_skew_seconds,meaningful_tp_fraction=args.meaningful_tp_fraction)
    report['source']=source
    report['window']={'since':since.isoformat() if since else None,'until':until.isoformat() if until else None}
    print(json.dumps(report,indent=2))
    return report


if __name__=='__main__': main()
