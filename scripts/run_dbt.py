"""Load local env and run isolated dbt CLI; no credentials in command arguments."""
import os
from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def run_dbt(arguments, overrides=None, log_path=None):
    load_dotenv(ROOT / '.env')
    env = os.environ.copy()
    if overrides:
        env.update(overrides)
    env.setdefault('DBT_ENV_SECRET_PASSWORD', env['POSTGRES_PASSWORD'])
    env.setdefault('DBT_SEND_ANONYMOUS_USAGE_STATS', 'false')
    executable = ROOT / '.venv-dbt/bin/dbt'
    if not executable.exists():
        raise RuntimeError('Create .venv-dbt and install requirements-dbt.txt first')
    with (open(log_path, 'w') if log_path else nullcontext(None)) as output:
        return subprocess.run(
            [str(executable), *arguments, '--project-dir', str(ROOT / 'analytics'),
             '--profiles-dir', str(ROOT / 'analytics')],
            cwd=ROOT, env=env, check=False, stdout=output,
            stderr=subprocess.STDOUT if output else None,
        ).returncode


if __name__ == '__main__':
    sys.exit(run_dbt(sys.argv[1:]))
