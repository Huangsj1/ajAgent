"""Independent black-box acceptance checks for the agent-authored example."""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory


def audit(project):
    project = Path(project).resolve()
    script = project / 'expense.py'
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        database = directory / 'audit.sqlite'
        def invoke(*args, success=True):
            result = subprocess.run([sys.executable, str(script), *map(str,args)], cwd=project,
                                    text=True, capture_output=True, timeout=30)
            assert (result.returncode == 0) == success, result.stderr
            assert 'Traceback' not in result.stderr
            return json.loads(result.stdout) if success else result.stderr
        for _ in range(2):
            invoke('import','--db',database,'--csv',project/'sample.csv')
        report = invoke('report','--db',database)
        assert report['count'] == 3 and report['total_cents'] == 3550, report
        may = invoke('report','--db',database,'--month','2026-05')
        assert may['count'] == 2 and may['total_cents'] == 3050, may
        with sqlite3.connect(database) as connection:
            names = connection.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall()
            assert names
            found = any('lunch, café' in str(connection.execute(f'SELECT * FROM "{name}"').fetchall()) for name, in names)
            assert found, 'Quoted Unicode note was not preserved'
        bad = directory / 'bad.csv'
        for value in ('NaN','Infinity','0','-1.00','1.001'):
            with bad.open('w',newline='') as stream:
                writer=csv.writer(stream)
                writer.writerow(['date','category','amount','note'])
                writer.writerow(['2026-07-01','food','1.00','valid row before invalid'])
                writer.writerow(['2026-07-02','food',value,'bad amount'])
            invoke('import','--db',database,'--csv',bad,success=False)
            assert invoke('report','--db',database)['count'] == 3, 'Invalid CSV partially imported rows'
        bad.write_text('date,category,amount,note\n2026-02-30,food,1.00,invalid date\n')
        invoke('import','--db',database,'--csv',bad,success=False)
        for month in ('2026-00','2026-13','2026-5','0000-01'):
            invoke('report','--db',database,'--month',month,success=False)
        assert invoke('report','--db',database)['total_cents'] == 3550
        print('Independent audit passed: exact totals, idempotency, Unicode CSV, atomic rollback, money/date/month validation.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', nargs='?', default='examples/projects/expense_tracker')
    audit(parser.parse_args().project)


if __name__ == '__main__':
    main()
