"""Initialize persisted local login credentials without printing or rotating them."""
import json
import os
from pathlib import Path

path = Path(os.environ['AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE'])
username = os.environ['AIRFLOW_LOCAL_ADMIN_USER']
password = os.environ['AIRFLOW_LOCAL_ADMIN_PASSWORD']
if path.exists():
    if json.loads(path.read_text()).get(username) != password:
        raise RuntimeError('Persisted auth differs from env; restore matching configuration before startup')
else:
    path.write_text(json.dumps({username: password}))
path.chmod(0o600)
print('Local auth file ready; existing credentials preserved.')
