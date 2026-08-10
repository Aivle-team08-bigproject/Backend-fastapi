# AgentCore Runtime + NeonDB 연동 진행 기록

## 목적

기존 로컬 Celery Worker가 수행하던 에이전트 실행을 AWS Bedrock AgentCore Runtime으로
위임하면서, Runtime이 NeonDB의 익명화 데이터에 안전하게 접근하도록 변경했다.

핵심 변경은 데이터베이스 종류가 아니라 에이전트 실행 위치를 Celery에서
AgentCore+Bedrock으로 이전한 것이다. NeonDB는 현재 검증용 데이터 소스이며, 이후 AWS
내부 RDS/Aurora로 전환해도 Secret 참조와 승인된 query 실행 경계는 그대로 유지한다.

## 실행 경계

```text
Local Backend / Celery Worker
  -> IAM InvokeAgentRuntime
  -> AgentCore Runtime
       -> Secrets Manager에서 Neon URL 조회
       -> NeonDB anonymized schema 조회
       -> Bedrock Claude Haiku 4.5 호출
```

Local Backend는 요청 orchestration, 상태 저장, SSE 발행만 담당한다. Runtime은 요구사항
분석·데이터 선별·데이터 처리 에이전트를 실행하며, `DATA_SELECTION`의 메타데이터 조회와
`DATA_PROCESSING`의 실제 데이터 조회·가공도 Runtime 내부에서 수행한다.

## 보안 구현

- `NEON_DATABASE_SECRET_ARN`만 Runtime 환경 변수로 주입한다.
- Neon connection string 원문은 Secrets Manager에서 Runtime 시작 시 읽는다.
- Runtime은 `app.core.config.Settings`와 JWT 설정에 의존하지 않는다.
- DB 세션은 `pool_size=1`, `max_overflow=1`의 작은 pool로 제한한다.
- `DatabaseQueryExecutor`가 등록된 데이터셋·컬럼·필터만 SQLAlchemy statement로 생성한다.
- LLM이 생성한 임의 SQL은 실행하지 않으며, 처리 결과에 원천 행을 포함하지 않는다.
- `Dockerfile.agentcore`는 `app/`, `agent_runtime/`만 복사한다. dotenv 및 `env_team`은
  이미지와 Docker build context에서 제외한다.

## 주요 파일

- `app/agentcore_runtime.py`: AgentCore HTTP Runtime 진입점 및 DB lifecycle
- `agent_runtime/runtime_database.py`: Secrets Manager 기반 Neon async session factory
- `app/domains/pipeline/agent_client.py`: 로컬/Runtime 공용 DB session factory와
  AgentCore invoke adapter
- `Dockerfile.agentcore`: AgentCore 전용 최소 이미지

## 배포·테스트 절차

1. `docker build -f Dockerfile.agentcore -t bigproject-agentcore:local .`
2. ECR에 새 tag를 push한다.
3. Infra의 `runtime_image_uri`, `neon_database_secret_arn`을 적용한다.
4. local invoker role MFA credentials를 획득한다.
5. 프로젝트 루트에서 Backend 컨테이너를 재생성한다.

```bash
docker compose up -d --build --force-recreate backend-api backend-worker
```

6. UI에서 새 데이터 요청을 생성한다. `DATA_SELECTION` 성공은 Runtime이 Secret을 읽고
NeonDB 메타데이터에 접근했음을 의미하며, 이후 `DATA_PROCESSING` 성공으로 승인된 데이터
조회와 결정론적 가공까지 확인한다.

## 검증 결과

- AgentCore Runtime v7에서 NeonDB 접근 성공
- local Backend와 Runtime 간 IAM InvokeAgentRuntime 호출 성공
- Backend 테스트 30개 통과
- `terraform validate` 통과
