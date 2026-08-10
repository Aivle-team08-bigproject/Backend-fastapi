"""AgentCore Runtime 전용 Neon 읽기 세션.

FastAPI backend의 Settings에는 JWT 등 Runtime에 불필요한 값이 포함돼 있다.
이 모듈은 AgentCore execution role로 Secrets Manager에서 DB URL만 읽으므로
Runtime이 backend 인증 설정이나 로컬 dotenv 파일에 의존하지 않는다.
"""

from __future__ import annotations

import asyncio
import os

import boto3
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class RuntimeDatabase:
    """Secret ARN 하나로 초기화되는 작은 Neon 커넥션 풀."""

    def __init__(self) -> None:
        self._engine = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def initialize(self) -> None:
        if self._session_factory is not None:
            return

        secret_arn = os.environ.get("NEON_DATABASE_SECRET_ARN", "").strip()
        if not secret_arn:
            raise RuntimeError("NEON_DATABASE_SECRET_ARN is required")
        region = os.environ.get("AWS_REGION") or os.environ.get("AGENT_RUNTIME_REGION")

        def read_secret() -> str:
            client = boto3.client("secretsmanager", region_name=region)
            response = client.get_secret_value(SecretId=secret_arn)
            value = response.get("SecretString")
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError("Neon database secret must contain a plaintext connection string")
            return value.strip()

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
