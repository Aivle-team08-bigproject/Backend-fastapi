# AgentCore 로컬 호출 adapter

## 범위

이 브랜치는 기존 Celery supervisor와 상태 저장 경로를 유지하면서, 실행 backend를
환경변수로 선택할 수 있게 한다. 기본값은 `CELERY`이고, AWS 테스트에서는
`PIPELINE_EXECUTION_BACKEND=AGENTCORE`와 `AGENTCORE_RUNTIME_ARN`을 설정한다.

현재 단계에서 Celery는 파이프라인 orchestration과 DB 상태 기록을 담당하고, 각 stage의
agent 실행은 `InvokeAgentRuntime`으로 위임한다. `DATA_SELECTION`의 스키마 메타데이터
조회와 `DATA_PROCESSING`의 승인된 데이터 조회·결정론적 가공은 AgentCore Runtime 내부에서
실행한다. 따라서 Runtime 컨테이너에는 `Dockerfile.agentcore`를 사용하고, `/ping`과
`/invocations`를 제공해야 한다.

Runtime은 `NEON_DATABASE_SECRET_ARN`으로 지정된 Secrets Manager 값만 읽는다. 연결 문자열은
Terraform 변수·AgentCore 환경변수·이미지에 직접 저장하지 않으며, Runtime execution role에는
해당 Secret의 `secretsmanager:GetSecretValue`만 허용한다. 실제 조회는 여전히
`DatabaseQueryExecutor`의 등록된 데이터셋·컬럼·필터 allowlist를 거치므로 LLM 생성 SQL을 직접
실행하지 않는다.

## 환경변수

```env
PIPELINE_EXECUTION_BACKEND=AGENTCORE
AGENTCORE_REGION=ap-northeast-2
AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:ap-northeast-2:<account>:runtime/<runtime-id>
AGENTCORE_RUNTIME_QUALIFIER=
AGENTCORE_SESSION_PREFIX=bigproject
AGENTCORE_CONNECT_TIMEOUT_SECONDS=10
AGENTCORE_READ_TIMEOUT_SECONDS=900
```

로컬 FastAPI 프로세스의 AWS credential은 환경변수에 직접 저장하지 않고 AWS SDK의 표준
credential provider chain을 사용한다. IAM principal에는 `bedrock-agentcore:InvokeAgentRuntime`
권한을 단일 runtime ARN에만 부여해야 하며, AgentCore Runtime 쪽은 응답을
`{"output": <stage-result>}` JSON으로 반환한다.

## 실행

```bash
PIPELINE_EXECUTION_BACKEND=AGENTCORE \
AGENTCORE_RUNTIME_ARN=... \
uvicorn app.main:app --reload
```

AWS 호출에 실패하면 원문 응답이나 payload를 로그에 남기지 않고 stage를 실패 처리한다.
`DATA_PROCESSING` 결과에는 원천 행을 포함하지 않는 기존 payload 경계를 유지해야 한다.
