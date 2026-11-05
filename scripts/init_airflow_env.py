"""Fill missing Airflow secrets in the local .env; preserve initialized values."""
import base64
import os
from pathlib import Path
import secrets

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / '.env'
    if not path.exists():
        raise RuntimeError('Create .env from .env.example first')
    values = dotenv_values(path)
    defaults = {
        'AIRFLOW_PORT': '8081',
        'AIRFLOW_METADATA_PASSWORD': secrets.token_urlsafe(32),
        'AIRFLOW_COLLECTOR_PASSWORD': secrets.token_urlsafe(32),
        'AIRFLOW_FERNET_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'AIRFLOW_JWT_SECRET': secrets.token_urlsafe(48),
        'AIRFLOW_LOCAL_ADMIN_USER': 'sanh',
        'AIRFLOW_LOCAL_ADMIN_PASSWORD': secrets.token_urlsafe(32),
    }
    lines = path.read_text().splitlines()
    changed = 0
    for key, value in defaults.items():
        if values.get(key) and not values[key].startswith('replace_'):
            continue
        indices = [i for i, line in enumerate(lines) if line.startswith(key + '=')]
        if len(indices) > 1:
            raise ValueError(f'Duplicate configuration key: {key}')
        if indices:
            lines[indices[0]] = key + '=' + value
        else:
            lines.append(key + '=' + value)
        changed += 1
    # The metadata password is interpolated into a SQLAlchemy URI by Compose.
    password = next(line.split('=', 1)[1] for line in lines if line.startswith('AIRFLOW_METADATA_PASSWORD='))
    if not password or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in password):
        raise ValueError('Use a URL-safe alphanumeric/_/- metadata password')
    path.write_text('\n'.join(lines) + '\n')
    os.chmod(path, 0o600)
    print(f'Airflow env ready: {changed} missing values initialized; existing values preserved.')


if __name__ == '__main__':
    main()
