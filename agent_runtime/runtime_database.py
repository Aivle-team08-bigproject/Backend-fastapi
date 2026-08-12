"""AgentCore Runtime 전용 PostgreSQL 읽기 세션.

FastAPI backend의 Settings에는 JWT 등 Runtime에 불필요한 값이 포함돼 있다.
이 모듈은 AgentCore execution role로 Secrets Manager에서 DB URL만 읽으므로
Runtime이 backend 인증 설정이나 로컬 dotenv 파일에 의존하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import boto3
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class RuntimeDatabase:
    """Secret ARN 하나로 초기화되는 작은 PostgreSQL 커넥션 풀."""

    @staticmethod
    def _normalize_database_url(value: str) -> str:
        """Use the asyncpg dialect bundled in the AgentCore image."""
        value = value.strip()
        if value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        if value.startswith("postgres://"):
            value = "postgresql+asyncpg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql+asyncpg://"):
            parsed = urlsplit(value)
            # sslmode/channel_binding are libpq options and asyncpg rejects
            # them as unexpected keyword arguments. TLS remains enabled by
            # the Neon endpoint and asyncpg's default SSL negotiation.
            query = urlencode(
                [pair for pair in parse_qsl(parsed.query, keep_blank_values=True)
                 if pair[0] not in {"sslmode", "channel_binding"}]
            )
            return urlunsplit(parsed._replace(query=query))
        return value

    def __init__(self) -> None:
        self._engine = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def initialize(self) -> None:
        if self._session_factory is not None:
            return

        secret_arn = os.environ.get("AGENT_DATABASE_SECRET_ARN", "").strip()
        if not secret_arn:
            raise RuntimeError("AGENT_DATABASE_SECRET_ARN is required")
        region = os.environ.get("AWS_REGION") or os.environ.get("AGENT_RUNTIME_REGION")

        def read_secret() -> str:
            client = boto3.client("secretsmanager", region_name=region)
            response = client.get_secret_value(SecretId=secret_arn)
            value = response.get("SecretString")
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError("database secret must contain a connection string or PostgreSQL JSON")
            value = value.strip()
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return value
            if not isinstance(parsed, dict):
                raise RuntimeError("database secret JSON must be an object")
            connection_string = parsed.get("connection_string") or parsed.get("url")
            if isinstance(connection_string, str) and connection_string.strip():
                return self._normalize_database_url(connection_string)
            # Some existing Secrets Manager entries use the secret name as the
            # JSON key and store the connection string as its only value.
            # Accept that shape while keeping structured RDS/Aurora JSON below.
            if len(parsed) == 1:
                only_value = next(iter(parsed.values()))
                if isinstance(only_value, str) and only_value.strip():
                    return self._normalize_database_url(only_value)
            required = {"host", "username", "password"}
            if not required.issubset(parsed):
                raise RuntimeError("database secret JSON requires host, username, and password")
            database = parsed.get("dbname") or parsed.get("database")
            if not isinstance(database, str) or not database.strip():
                raise RuntimeError("database secret JSON requires dbname or database")
            host = str(parsed["host"]).strip()
            username = quote(str(parsed["username"]), safe="")
            password = quote(str(parsed["password"]), safe="")
            port = parsed.get("port", 5432)
            query = "?sslmode=require" if parsed.get("sslmode", True) else ""
            return f"postgresql+asyncpg://{username}:{password}@{host}:{port}/{quote(database, safe='')}{query}"

        database_url = await asyncio.to_thread(read_secret)
        self._engine = create_async_engine(
            database_url,
            echo=False,
            future=True,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=1,
            pool_timeout=10,
            pool_recycle=300,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("RuntimeDatabase has not been initialized")
        return self._session_factory

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None
