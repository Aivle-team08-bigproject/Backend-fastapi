# AWS 배포 체크리스트 (`develop_aws`)

## 이미지

GitHub Actions가 기존 ECR repository에 다음 이미지를 push한다.

- API: `ECR_API_REPOSITORY:<commit-sha>`
- AgentCore: `ECR_AGENTCORE_REPOSITORY:<commit-sha>` (`linux/arm64`)

현재 ECR 주소:

- API: `747938282742.dkr.ecr.ap-northeast-2.amazonaws.com/backend-fastapi`
- AgentCore: `747938282742.dkr.ecr.ap-northeast-2.amazonaws.com/backend-fastapi-agentcore`

운영 배포에서는 `latest`보다 commit SHA 태그를 사용한다.

## GitHub Actions 설정

Repository Settings → Secrets and variables → Actions에 등록한다.

### Secret

- `AWS_GITHUB_ACTIONS_ROLE_ARN`: GitHub OIDC를 신뢰하는 IAM Role ARN

### Variables

- `AWS_REGION`: 기본값 `ap-northeast-2`
- `ECR_API_REPOSITORY`: 기존 API ECR 전체 URI
- `ECR_AGENTCORE_REPOSITORY`: 기존 AgentCore ECR 전체 URI

Role에는 해당 ECR repository에 대한 로그인·이미지 push 권한이 필요하다.

## API 런타임 환경

- `ENVIRONMENT=production`
- `HANACARD_APP_DATABASE_URL`: RDS PostgreSQL 연결 문자열
- `WORKER_STATUS_REDIS_URL`: ElastiCache Redis 연결 문자열
- `ARTIFACT_STORAGE_BACKEND=s3`
- `S3_ARTIFACTS_BUCKET`: 결과 파일 S3 bucket
- `AWS_REGION`
- `INTERNAL_SERVICE_KEY`, `JWT_SECRET`: Secrets Manager 등에서 주입

## AgentCore 런타임 환경

- `AGENT_DATABASE_SECRET_ARN`: RDS 연결 정보를 담은 Secrets Manager ARN
- `AWS_REGION`
- 에이전트 모델 설정 및 API 키: Secrets Manager 또는 런타임 환경변수로 주입

AgentCore 역할에는 Secrets Manager 읽기, S3 결과 저장, 필요한 Bedrock 권한만 부여한다.

## 배포 후 확인

1. API `/health/live`가 `200`인지 확인
2. API `/health/ready`가 DB·Redis 모두 `ok`인지 확인
3. AgentCore `/ping`이 응답하는지 확인
4. 샘플 invocation으로 `execution_id`가 유지되는지 확인
5. S3 결과 파일 생성 및 presigned URL 다운로드 확인
