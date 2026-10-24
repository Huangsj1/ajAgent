"""Run one real coding task at three budgets; audit independently of model claims."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

PROFILES = {
    'tiny': (2, 2, 8000),
    'small': (6, 8, 24000),
    'normal': (14, 24, 80000),
}
SPEC = '''Create slugify.py with slugify(text: str) -> str using only the standard library.
Normalize Unicode using NFKD, remove non-ASCII characters, lowercase, replace each
run of non-alphanumeric characters with a single hyphen and strip edge hyphens.
Examples: "  Hello, World! " -> "hello-world", "Café déjà vu" -> "cafe-deja-vu",
"你好" -> "", "a___b---c" -> "a-b-c", "Crème 42" -> "creme-42".
CLI: python3 slugify.py "Hello World" prints hello-world and exits 0.
Add test_slugify.py with at least five unittest tests and README.md with usage.
Run the tests. Do not modify this specification. Report only observed results.
'''
AUDIT = '''import importlib.util, pathlib, subprocess, sys
p=pathlib.Path.cwd()
spec=importlib.util.spec_from_file_location('slugify',p/'slugify.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for text,want in [('  Hello, World! ','hello-world'),('Café déjà vu','cafe-deja-vu'),('你好',''),('a___b---c','a-b-c'),('Crème 42','creme-42'),('',''),('---',''),('A123 B','a123-b')]:
 assert m.slugify(text)==want,(text,m.slugify(text))
r=subprocess.run([sys.executable,'slugify.py','Hello World'],capture_output=True,text=True)
assert r.returncode==0 and r.stdout.strip()=='hello-world'
assert (p/'README.md').is_file()
import unittest
suite=unittest.defaultTestLoader.discover('.',pattern='test_slugify.py')
assert suite.countTestCases()>=5
assert unittest.TextTestRunner().run(suite).wasSuccessful()
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config/config.local.toml')
    parser.add_argument('--output', required=True, help='New directory for isolated workspaces and reports')
    args = parser.parse_args()
    root = Path.cwd()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for name, (steps, calls, tokens) in PROFILES.items():
        workspace = output / name
        workspace.mkdir()
        (workspace / 'REQUIREMENTS.md').write_text(SPEC)
        # Temporary configuration contains only a reference to the existing secrets file.
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'trial.toml'
            config.write_text(f'''include = [{json.dumps(str(Path(args.config).resolve()))}]
[runtime]
workspace = {json.dumps(str(workspace))}
memory = false
max_steps = {steps}
output_tokens = 2048
[tools]
enabled = ["files", "execution"]
[harness]
max_tool_calls = {calls}
max_tokens = {tokens}
max_seconds = 180
# Trial workspace only.
auto_approve_tools = ["write_file", "run_command", "run_python"]
[coordination]
enabled = false
''')
            started = time.monotonic()
            with (output / f'{name}.log').open('w') as log:
                run = subprocess.run([sys.executable, '-c', 'from ajagent.cli import main; raise SystemExit(main())', '--config', str(config),
                                      'Read REQUIREMENTS.md, implement the project, and run its tests.'],
                                     cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=240)
        audit = subprocess.run([sys.executable, '-c', AUDIT], cwd=workspace,
                               capture_output=True, text=True, timeout=20)
        (workspace / 'audit.log').write_text(audit.stdout + audit.stderr)
        events = [json.loads(line) for path in (workspace / '.ajagent/runs').glob('*/events.jsonl')
                  for line in path.read_text().splitlines()]
        end = next((e for e in reversed(events) if e['event'] == 'run_finished'), {})
        row = {'profile': name, 'max_steps': steps, 'max_tools': calls, 'max_tokens': tokens,
               'exit_code': run.returncode, 'audit_passed': audit.returncode == 0,
               'seconds': round(time.monotonic()-started, 2),
               'status': end.get('reason'), 'usage': end.get('usage'),
               'phases': [e.get('phase') for e in events if e['event'] in {'budget_phase_changed', 'budget_finalizing'}],
               'tool_calls': sum(e['event'] == 'tool_started' for e in events)}
        rows.append(row)
        print(json.dumps(row), flush=True)
        (output / 'results.json').write_text(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
