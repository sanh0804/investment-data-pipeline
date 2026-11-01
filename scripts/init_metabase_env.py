"""Fill only missing/placeholder Metabase env values; never rotate existing keys."""
from pathlib import Path
import os
import secrets

from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]


def main():
    path=ROOT/'.env'
    if not path.exists():
        raise RuntimeError('Create .env from .env.example first; configure POSTGRES_PASSWORD')
    values=dotenv_values(path)
    defaults={'METABASE_PORT':'3000','METABASE_DB_PASSWORD':secrets.token_urlsafe(32),
        'METABASE_ENCRYPTION_KEY':secrets.token_hex(32),'METABASE_READER_PASSWORD':secrets.token_urlsafe(32),
        'METABASE_ADMIN_EMAIL':'admin@investment.local','METABASE_ADMIN_PASSWORD':'Aa1!'+secrets.token_urlsafe(28)}
    lines=path.read_text().splitlines()
    changed=0
    for key,value in defaults.items():
        if values.get(key) and not values[key].startswith('replace_'):
            continue
        indices=[i for i,line in enumerate(lines) if line.startswith(key+'=')]
        if indices:
            if len(indices)!=1:
                raise ValueError(f'Duplicate {key} in .env; resolve before initialization')
            lines[indices[0]]=key+'='+value
        else:
            lines.append(key+'='+value)
        changed+=1
    path.write_text('\n'.join(lines)+'\n');os.chmod(path,0o600)
    print(f'Ready: initialized {changed} missing/placeholder values; existing credentials preserved; .env mode 0600.')


if __name__=='__main__':
    main()
