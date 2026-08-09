# 공지사항 기능 PR 문서

## 목적

관리자 전용 공지사항 CRUD와 실무자용 공지 조회 기능을 추가합니다. Neon 분리 브랜치에서 검증한 `service.notices` 스키마와 Alembic migration을 기준으로 합니다.

## 변경 범위

- Backend: `feature/notices-backend`
  - 공개 공지 목록/상세/최신 공지 API
- 관리자 공지 목록/상세/작성/수정 및 논리 삭제 API
  - 관리자 권한 검사
  - `e4a1b2c3d4e5_add_notices.py` migration
- Frontend: `feature/notices-frontend`
  - Dashboard 최신 공지 한 줄 배너
  - 공지사항 목록/상세 페이지
  - 관리자 공지 관리/작성/수정 페이지
  - 관리자 메뉴 및 라우팅 권한 제어

## 검증

- Docker Compose로 Backend/Frontend/Redis 기동 확인
- Neon 분리 브랜치에 migration 적용 및 더미 공지 조회 확인
- 관리자 로그인 후 작성·수정·삭제 흐름 확인
- 비관리자 공지 조회 및 관리자 화면 접근 차단 확인
- 화면 캡처: `/Users/joupark/bigproject2/docs/NOTICE_FEATURE_SCREENSHOTS.md`

## 리뷰 포인트

1. 관리자 권한 검사가 모든 `/api/v1/admin/notices*` 엔드포인트에 적용되었는지
2. 공개 조회에서 `PUBLISHED` 공지만 반환되는지
3. `published_at` 정렬 및 Dashboard 최신 공지 표시가 일관적인지
4. migration의 FK, 상태 체크 제약조건, 인덱스가 운영 DB 기준에 맞는지

## PR 생성 안내

이 문서는 검토용이며 PR은 자동 생성하지 않습니다. 두 공지사항 브랜치는 `feature/pipeline-sse-progress`에서 분기되었으므로, 각 저장소의 대상 브랜치를 `feature/pipeline-sse-progress`로 설정해 별도 PR을 생성합니다.
