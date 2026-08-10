# 공지사항 기능 PR 문서

## 목적

관리자 전용 공지사항 CRUD와 실무자용 공지 조회 기능을 추가합니다. Neon 분리 브랜치에서 검증한 `service.notices` 스키마를 기준으로 합니다.

## 변경 범위

- Backend: `feature/notices-backend`
  - 공개 공지 목록/상세/최신 공지 API
  - 관리자 공지 목록/상세/작성/수정 및 논리 삭제 API
  - 관리자 권한 검사
  - DB 담당 스키마 계약에 맞춘 모델·상태 전이·응답 계약
- Frontend: `feature/notices-frontend`
  - Dashboard 최신 공지 한 줄 배너
  - 공지사항 목록/상세 페이지
  - 관리자 공지 관리/작성/수정 페이지
  - 관리자 메뉴 및 라우팅 권한 제어

## 검증

- Docker Compose로 Backend/Frontend/Redis 기동 확인
- DB 담당의 Neon 분리 브랜치 적용 후 더미 공지 조회 확인
- 관리자 로그인 후 작성·수정·삭제 흐름 확인
- 비관리자 공지 조회 및 관리자 화면 접근 차단 확인
- 화면 캡처: `/Users/joupark/bigproject2/docs/NOTICE_FEATURE_SCREENSHOTS.md`

## 리뷰 포인트

1. 관리자 권한 검사가 모든 `/api/v1/admin/notices*` 엔드포인트에 적용되었는지
2. 공개 조회에서 `PUBLISHED` 공지만 반환되는지
3. `published_at` 정렬 및 Dashboard 최신 공지 표시가 일관적인지
4. DB 담당 스키마의 FK, 상태 체크 제약조건, 인덱스, `app_svc` 권한과 Backend 모델 계약이 일치하는지

## PR 생성 안내

이 문서는 `feature/notices-backend-reapply`에서 `develop`으로 보내는 복구 PR의 검토 문서입니다. 기존 공지사항 PR의 Revert 커밋을 다시 revert하여 기능을 복구합니다.
