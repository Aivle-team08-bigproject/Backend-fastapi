import asyncio

import pytest

from agent_runtime.runtime_database import RuntimeDatabase


class FakeSecretsManager:
    def __init__(self):
        self.secret_id = None

    def get_secret_value(self, *, SecretId):
        self.secret_id = SecretId
        return {"SecretString": "postgresql+psycopg://agent_svc:password@example.rds.amazonaws.com/hanacard?sslmode=require"}


def test_runtime_database_reads_connection_string_from_exact_secret(monkeypatch):
    fake_client = FakeSecretsManager()
    captured = {}

    monkeypatch.setenv(
        "AGENT_DATABASE_SECRET_ARN",
        "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:bigproject/dev/agent-database-abcdef",
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

    assert fake_client.secret_id.endswith("agent-database-abcdef")
    assert captured["url"].startswith("postgresql+psycopg://agent_svc:")
    assert captured["kwargs"]["pool_size"] == 1
    assert captured["kwargs"]["max_overflow"] == 1


def test_runtime_database_reads_rds_postgres_json(monkeypatch):
    fake_client = FakeSecretsManager()
    fake_client.get_secret_value = lambda **kwargs: {
        "SecretString": '{"host":"db.rds.amazonaws.com","port":5432,"dbname":"hanacard","username":"app_svc","password":"p@ss"}'
    }
    captured = {}
    monkeypatch.setenv("AGENT_DATABASE_SECRET_ARN", "arn:test:rds")
    monkeypatch.setattr("agent_runtime.runtime_database.boto3.client", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr("agent_runtime.runtime_database.create_async_engine", lambda url, **kwargs: captured.update(url=url) or object())
    monkeypatch.setattr("agent_runtime.runtime_database.async_sessionmaker", lambda *args, **kwargs: object())

    asyncio.run(RuntimeDatabase().initialize())
    assert captured["url"] == "postgresql+psycopg://app_svc:p%40ss@db.rds.amazonaws.com:5432/hanacard?sslmode=require"


def test_runtime_database_reads_single_key_connection_string_json(monkeypatch):
    fake_client = FakeSecretsManager()
    fake_client.get_secret_value = lambda **kwargs: {
        "SecretString": '{"bigproject/local-dev/agentcore/neon-database-url":"postgresql+psycopg://agent_svc:password@example.neon.tech/hanacard?sslmode=require"}'
    }
    captured = {}
    monkeypatch.setenv("AGENT_DATABASE_SECRET_ARN", "arn:test:neon")
    monkeypatch.setattr("agent_runtime.runtime_database.boto3.client", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr("agent_runtime.runtime_database.create_async_engine", lambda url, **kwargs: captured.update(url=url) or object())
    monkeypatch.setattr("agent_runtime.runtime_database.async_sessionmaker", lambda *args, **kwargs: object())

    asyncio.run(RuntimeDatabase().initialize())
    assert captured["url"].startswith("postgresql+psycopg://agent_svc:password@example.neon.tech/")


def test_runtime_database_requires_secret_arn(monkeypatch):
    monkeypatch.delenv("AGENT_DATABASE_SECRET_ARN", raising=False)

    with pytest.raises(RuntimeError, match="AGENT_DATABASE_SECRET_ARN"):
        asyncio.run(RuntimeDatabase().initialize())
