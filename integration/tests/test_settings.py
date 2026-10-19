from integration.settings import settings, service_environment


def test_dotenv_and_environment_precedence(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text('AUTOPILOT_LLM_API_KEY=file-key\nPOSTGRES_PORT=54349\n')
    monkeypatch.delenv('AUTOPILOT_LLM_API_KEY', raising=False)
    assert settings(path)['AUTOPILOT_LLM_API_KEY'] == 'file-key'
    assert settings(path)['POSTGRES_PORT'] == '54349'
    monkeypatch.setenv('AUTOPILOT_LLM_API_KEY', 'environment-key')
    assert settings(path)['AUTOPILOT_LLM_API_KEY'] == 'environment-key'


def test_provider_and_admin_credentials_are_not_passed_to_agent_service(monkeypatch, tmp_path):
    monkeypatch.setenv('AUTOPILOT_LLM_API_KEY', 'provider-secret')
    monkeypatch.setenv('AUTOPILOT_LLM_BASE_URL', 'https://llm.example/v1')
    monkeypatch.setenv('ADMIN_SECRET', 'admin-secret')
    values = settings(tmp_path / 'missing.env')
    cfg = {'POSTGRES_PASSWORD': 'pass@word', 'SIMULATOR_SECRET': 'sim-secret',
           'ADMIN_SECRET': 'admin-secret'}
    sdk = service_environment('sdk', cfg, 'unity', values)
    assert 'AUTOPILOT_LLM_API_KEY' not in sdk and 'ADMIN_SECRET' not in sdk
    assert 'AUTOPILOT_LLM_BASE_URL' not in sdk
    assert 'SIMULATOR_SECRET' not in sdk and 'DATABASE_URL' not in sdk
    gateway = service_environment('gateway', cfg, 'unity', values)
    assert gateway['AUTOPILOT_LLM_API_KEY'] == 'provider-secret'
    assert gateway['AUTOPILOT_LLM_BASE_URL'] == 'https://llm.example/v1'
    backend = service_environment('backend', cfg, 'unity', values)
    assert 'pass%40word' in backend['DATABASE_URL']
    assert 'AUTOPILOT_LLM_API_KEY' not in backend
    assert 'AUTOPILOT_LLM_BASE_URL' not in backend
