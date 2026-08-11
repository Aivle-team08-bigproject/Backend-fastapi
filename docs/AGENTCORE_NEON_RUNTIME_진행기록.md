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
       -> 검증된 최종 CSV를 S3에 직접 저장
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
- Runtime execution role은 `envs/dev`의 artifact write policy만 받아 S3/KMS write를
  수행하며, access key는 이미지·환경변수에 넣지 않는다.
- Runtime은 유효성 검증을 통과한 CSV만 저장하고 base64 본문을 호출 응답에서 제거한다.
  FastAPI는 S3 객체를 재작성하지 않고 `storage_key`를 Artifact·Spring 전달 경계에 기록한다.

## 주요 파일

- `app/agentcore_runtime.py`: AgentCore HTTP Runtime 진입점, DB lifecycle, S3 direct-write
- `agent_runtime/runtime_database.py`: Secrets Manager 기반 Neon async session factory
- `agent_runtime/runtime_artifact_storage.py`: Runtime execution role 기반 S3 CSV 저장
- `app/domains/pipeline/agent_client.py`: 로컬/Runtime 공용 DB session factory와
  AgentCore invoke adapter
- `Dockerfile.agentcore`: AgentCore 전용 최소 이미지

## 배포·테스트 절차

1. `docker build -f Dockerfile.agentcore -t bigproject-agentcore:local .`
2. ECR에 새 tag를 push한다.
3. Infra `envs/dev`를 먼저 적용하고 `artifacts_bucket_name`, `artifact_write_policy_arn`을
   `envs/agentcore-local-dev`의 `artifact_s3_bucket_name`, `artifact_write_policy_arn`에 넣는다.
4. Infra의 `runtime_image_uri`, `neon_database_secret_arn`을 적용한다.
5. local invoker role MFA credentials를 획득한다.
6. 프로젝트 루트에서 Backend 컨테이너를 재생성한다.

```bash
docker compose up -d --build --force-recreate backend-api backend-worker
```

7. UI에서 새 데이터 요청을 생성한다. `DATA_SELECTION` 성공은 Runtime이 Secret을 읽고
NeonDB 메타데이터에 접근했음을 의미하며, 이후 `DATA_PROCESSING` 성공으로 승인된 데이터
조회·결정론적 가공·S3 직접 저장까지 확인한다.

## 검증 결과

- AgentCore Runtime v7에서 NeonDB 접근 성공
- local Backend와 Runtime 간 IAM InvokeAgentRuntime 호출 성공
- Backend 테스트 30개 통과
- `terraform validate` 통과

## ECR 삭제 후 Runtime 이미지 복구

ECR repository가 삭제된 경우 Infra 브랜치에서 Terraform apply로 repository를 먼저
재생성한 뒤, 이 브랜치에서 새 이미지를 build·push한다. 삭제 전 tag를 재사용하지 않고
새 immutable tag를 사용한다.

```bash
cd /Users/joupark/bigproject2/Backend-fastapi

ECR_REPO="$(cd ../bigproject-infra/envs/agentcore-local-dev && terraform output -raw ecr_repository_url)"
IMAGE_TAG="agentcore-restore-$(date +%Y%m%d%H%M%S)"

docker build -f Dockerfile.agentcore -t bigproject-agentcore:local .
aws ecr get-login-password --region ap-northeast-2 \
  | docker login --username AWS --password-stdin "$ECR_REPO"
docker tag bigproject-agentcore:local "$ECR_REPO:$IMAGE_TAG"
docker push "$ECR_REPO:$IMAGE_TAG"

printf 'runtime_image_uri = "%s:%s"\n' "$ECR_REPO" "$IMAGE_TAG"
```

마지막 출력값으로 Infra의 `terraform.tfvars`에 있는 `runtime_image_uri`를 바꾼 뒤 두 번째
Terraform apply를 실행한다. 적용 후에는 local invoker role의 MFA credentials로 Backend를
재빌드·재시작하고 새 데이터 요청의 데이터 선별·데이터 처리 성공을 확인한다.
