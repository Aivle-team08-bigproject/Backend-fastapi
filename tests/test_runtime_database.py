import asyncio

import pytest

from agent_runtime.runtime_database import RuntimeDatabase


class FakeSecretsManager:
    def __init__(self):
        self.secret_id = None

    def get_secret_value(self, *, SecretId):
        self.secret_id = SecretId
        return {"SecretString": "postgresql+psycopg://agent_svc:password@example.neon.tech/hanacard?sslmode=require"}


def test_runtime_database_reads_connection_string_from_exact_secret(monkeypatch):
    fake_client = FakeSecretsManager()
    captured = {}

    monkeypatch.setenv(
        "NEON_DATABASE_SECRET_ARN",
        "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:bigproject/local-dev/agentcore/neon-database-url-abcdef",
    )
    monkeypatch.setattr("agent_runtime.runtime_database.boto3.client", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr(
        "agent_runtime.runtime_database.create_async_engine",
        lambda url, **kwargs: captured.update(url=url, kwargs=kwargs) or object(),
    )
    monkeypatch.setattr(
        "agent_runtime.runtime_database.async_sessionmaker",
        lambda *args, **kwargs: object(),
    )

    database = RuntimeDatabase()
    asyncio.run(database.initialize())

    assert fake_client.secret_id.endswith("neon-database-url-abcdef")
    assert captured["url"].startswith("postgresql+psycopg://agent_svc:")
    assert captured["kwargs"]["pool_size"] == 1
    assert captured["kwargs"]["max_overflow"] == 1


def test_runtime_database_requires_secret_arn(monkeypatch):
    monkeypatch.delenv("NEON_DATABASE_SECRET_ARN", raising=False)

    with pytest.raises(RuntimeError, match="NEON_DATABASE_SECRET_ARN"):
        asyncio.run(RuntimeDatabase().initialize())
