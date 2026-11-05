from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.verify_ci import ci_settings, connect
from scripts import run_dbt


ENV = {'CI_POSTGRES_HOST':'127.0.0.1','CI_POSTGRES_PORT':'55434',
       'CI_POSTGRES_DB':'investment_ci','CI_POSTGRES_USER':'ci_runner',
       'CI_POSTGRES_PASSWORD':'fixture_only'}


@pytest.mark.parametrize('key,value', [('CI_POSTGRES_DB','investment'),
    ('CI_POSTGRES_USER','investment'),('CI_POSTGRES_HOST','postgres'),
    ('CI_POSTGRES_PORT','55432'),('CI_POSTGRES_PORT','55433')])
def test_ci_refuses_demo_connection_settings(key,value):
    with pytest.raises(AssertionError):
        ci_settings({**ENV,key:value})


def test_ci_requires_explicit_ci_credentials():
    with pytest.raises(KeyError):
        ci_settings({})
    settings, bootstrap = ci_settings(ENV)
    assert settings['port'] == 55434 and bootstrap == 'investment_ci'
    with pytest.raises(AssertionError):
        connect(settings,'investment')


def test_ci_dbt_wrapper_skips_dotenv_and_uses_explicit_test_secret(monkeypatch,tmp_path):
    executable=tmp_path/'.venv-dbt/bin/dbt'
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(run_dbt,'ROOT',tmp_path)
    monkeypatch.setattr(run_dbt,'load_dotenv',Mock(side_effect=AssertionError('CI must not load .env')))
    monkeypatch.setenv('DBT_ENV_SECRET_PASSWORD','stale-local-value')
    process=Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(run_dbt.subprocess,'run',process)
    assert run_dbt.run_dbt(['build'], {'POSTGRES_PASSWORD':'fixture-only',
         'DBT_ENV_SECRET_PASSWORD':'fixture-only','DBT_DATABASE':'ci_verify_test'},load_local_env=False)==0
    kwargs=process.call_args.kwargs
    assert kwargs['env']['DBT_ENV_SECRET_PASSWORD']=='fixture-only'
    assert kwargs['env']['DBT_DATABASE']=='ci_verify_test'
    assert not any('fixture-only' in arg for arg in process.call_args.args[0])
