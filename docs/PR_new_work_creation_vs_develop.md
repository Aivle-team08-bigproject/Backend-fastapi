# PR 문서: `feat/new-work-creation` → `develop`

## 1. 변경 목적

새 작업 생성 화면에서 입력한 고객사 정보, 계약 일정, 구조화된 요구사항과 데이터 준비·민감정보 선택값을 백엔드가 저장할 수 있도록 작업 생성 API와 데이터 모델을 확장한다.

## 2. `develop` 대비 주요 변경

### 작업 생성 요청 확장

- `client`: 고객사 정보 입력을 지원한다.
  - `company_name` 필수
  - 사업자등록번호, 담당자명, 담당자 이메일, 담당자 연락처 선택
- `contract`: 계약 정보를 입력할 수 있다.
  - 계약번호는 선택 입력이며 미입력 시 서버가 자동 발급한다.
  - 계약 시작일, 계약 종료일, 최종 납기일을 저장한다.
- `structured_requirement`: 자유 서술 요구사항과 함께 공용 구조화 선택값을 JSON 형태로 저장한다.
- `source_data_status`: `READY`, `PREPARING`, `UNKNOWN` 중 하나를 저장한다.
- `data_sensitivity`: `NONE`, `POSSIBLE`, `UNKNOWN` 중 하나를 저장한다.

### 응답 확장

작업 생성 응답에 생성된 `contract_no`를 포함한다. 계약 정보를 보내지 않은 경우에는 `null`을 반환한다.

### 고객사 처리

동일한 회사명이 있으면 기존 고객사 정보를 갱신하고, 없으면 새 고객사 레코드를 생성한다. 작업 생성 요청의 고객사 정보가 없을 때는 기존 호환성을 위해 `requester_name`을 회사명으로 사용한다.

## 3. 데이터베이스 변경

마이그레이션 파일 `alembic/versions/3f8e1c2a7b90_add_work_intake_fields.py`를 추가한다.

- `service.data_requests.structured_requirement` (`JSONB`, 기본값 `{}`)
- `service.data_requests.source_data_status` (`VARCHAR(20)`, 기본값 `UNKNOWN`)
- `service.data_requests.data_sensitivity` (`VARCHAR(20)`, 기본값 `UNKNOWN`)
- `service.contracts.delivery_due_at` (`TIMESTAMP WITH TIME ZONE`, nullable)

마이그레이션의 선행 revision은 `c8e3a1f75b20`이다. 배포 전 대상 DB의 Alembic revision 상태와 마이그레이션 체인을 확인해야 한다.

## 4. 입력 검증

- `raw_requirement`: 1~8000자
- `title`: 최대 200자
- 고객사명: 1~200자
- 계약번호: 최대 60자
- 계약 종료일은 계약 시작일보다 빠를 수 없다.
- 최종 납기일은 계약 종료일 이후일 수 없다.
- 열거형 선택값은 정의된 `Literal` 값만 허용한다.

## 5. 영향 범위

- `app/domains/pipeline/model.py`
- `app/domains/pipeline/schema.py`
- `app/domains/pipeline/service.py`
- `alembic/versions/3f8e1c2a7b90_add_work_intake_fields.py`

기존 고객사 정보 없이 요청을 생성하는 호출 방식은 유지한다. 기존 작업 실행·검토 파이프라인의 단계 구성과 실행 방식은 변경하지 않는다.

## 6. 검증 체크리스트

- [ ] Alembic migration이 최신 DB에서 정상 적용된다.
- [ ] 고객사·계약·구조화 요구사항을 포함한 작업 생성 요청이 성공한다.
- [ ] 계약번호 미입력 시 `CTR-YYYYMMDD-XXXXXX` 형식으로 자동 발급된다.
- [ ] 계약 시작일·종료일·최종 납기일 역전 입력이 거부된다.
- [ ] `source_data_status`, `data_sensitivity`의 잘못된 값이 거부된다.
- [ ] 기존 호환 요청(`client`, `contract` 없이 생성)이 성공한다.
- [ ] 고객사 정보가 기존 레코드 갱신 또는 신규 레코드 생성으로 올바르게 처리된다.
- [ ] 작업 생성 응답에 `contract_no`가 올바르게 포함된다.
- [ ] 관련 API 테스트와 린트·타입 검사를 통과한다.

## 7. 배포 시 유의사항

애플리케이션 배포 전에 `3f8e1c2a7b90` 마이그레이션을 적용해야 한다. 마이그레이션이 적용되지 않은 상태에서 새 필드를 사용하는 API를 호출하면 `UndefinedColumn` 오류가 발생할 수 있으므로, 애플리케이션과 DB 스키마의 배포 순서를 함께 확인한다.
