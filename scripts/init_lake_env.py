"""Fill only missing/placeholder object-storage env values; never rotate existing keys."""
from pathlib import Path
import os
import secrets

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / '.env'
    if not path.exists():
        raise RuntimeError('Create .env from .env.example first; configure POSTGRES_PASSWORD')
    values = dotenv_values(path)
    defaults = {'LAKE_PORT': '59000', 'LAKE_BUCKET': 'investment-lake',
                'LAKE_ACCESS_KEY': 'lake' + secrets.token_hex(8),
                'LAKE_SECRET_KEY': secrets.token_urlsafe(32)}
    lines = path.read_text().splitlines()
    changed = 0
    for key, value in defaults.items():
        if values.get(key) and not values[key].startswith('replace_'):
            continue
        indices = [i for i, line in enumerate(lines) if line.startswith(key + '=')]
        if indices:
            if len(indices) != 1:
                raise ValueError(f'Duplicate {key} in .env; resolve before initialization')
            lines[indices[0]] = key + '=' + value
        else:
            lines.append(key + '=' + value)
        changed += 1
    path.write_text('\n'.join(lines) + '\n')
    os.chmod(path, 0o600)
    print(f'Ready: initialized {changed} missing/placeholder values; existing credentials preserved; .env mode 0600.')


if __name__ == '__main__':
    main()
