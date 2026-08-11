# PR: 이메일 발송·S3 고객 API 연동

- Source: `feature/email-delivery-s3-customer-api`
- Target: `develop`
- Latest commit: `727f777`

## 변경 목적

최종 산출물 이메일 발송과 고객용 API 연동을 FastAPI 기준으로 연결한다. FastAPI가
발송 상태와 고객 API 인증을 소유하고, Spring에는 필요한 메일 큐 메시지만 전달한다.

## 주요 변경

- 샘플/최종 산출물 이메일 요청 유형 지원
- 최종 산출물 이메일 요청에 API URL·API Key 필수 검증
- API URL·Key를 SQS/Redis 이메일 메시지에 포함
- SQS 결과 반영 후 Redis SSE 상태 발행
- S3 artifact 저장 모드와 다운로드 URL 연동
- 고객 API Key 발급 및 내부 산출물 조회 API 유지
- DLQ 수동 종결 내부 API 추가
- 종료된 이메일 발송 이력의 수신자 개인정보 파기 루프 추가

## DB 주의사항

Alembic 파일은 이 브랜치에서 변경하지 않는다. 이메일 발송 테이블 migration은 DB
담당자가 `develop` 최신 head 기준으로 작성해야 하며, 상세 요구사항은
`docs/DB_EMAIL_DELIVERY_MIGRATION_HANDOFF.md`를 참고한다.

## 검증

- `.venv/bin/python -m compileall -q app`
- `git diff --check`
- Docker 이미지 빌드 및 FastAPI 컨테이너 기동
- 고객 API에서 Presigned URL 발급 후 S3 CSV 다운로드 확인

## 확인 요청

- DB migration 적용 순서와 실제 `service.email_deliveries` 스키마 확인
- SQS/DLQ 운영 파라미터와 내부 DLQ 종결 권한 확인
- `INTERNAL_SERVICE_KEY`, S3 bucket, SQS URL의 배포환경 주입 확인
