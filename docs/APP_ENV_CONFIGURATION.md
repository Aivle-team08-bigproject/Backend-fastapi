# APP_ENV 설정 가이드

## 목적

`APP_ENV`는 FastAPI가 실행되는 환경을 구분하는 설정값이다. 비밀번호, AWS 자격증명,
고객 API Key를 저장하는 값이 아니며 다음 값 중 하나만 사용할 수 있다.

| 값 | 용도 |
|---|---|
| `local` | 개발자 PC와 로컬 Docker Compose |
| `test` | pytest 등 자동화 테스트 |
| `dev` | 공유 개발 환경 |
| `staging` | 운영 전 검증 환경 |
| `production` | 운영 환경 |

## 필수 설정인 이유

앱 설정은 `app.core.config.Settings`가 생성될 때 환경변수에서 읽는다. `APP_ENV`가 없으면
기본값으로 대체하지 않고 설정 검증 단계에서 실패하므로, 잘못된 운영 환경이 `local`로
조용히 실행되는 것을 막는다.

```dotenv
APP_ENV=production
```

`APP_ENV`가 누락된 상태로 `gunicorn app.main:app` 또는 Celery worker를 실행하면
`Settings()` 생성 시 `Field required` 오류가 발생하고 프로세스가 시작되지 않는다.

## 실행 경로별 주입

| 실행 경로 | 주입 위치 | 권장값 |
|---|---|---|
| 로컬 Docker Compose API | 루트 `docker-compose.yml` | `${APP_ENV:-local}` |
| 로컬 Docker Compose worker | 루트 `docker-compose.yml` | `${APP_ENV:-local}` |
| pytest | `tests/conftest.py` | `test` |
| 운영 배포 | Secret/Parameter 또는 배포 환경변수 | `production` |

Dockerfile은 `APP_ENV`를 하드코딩하지 않는다. 이미지는 여러 환경에서 재사용하고,
실행 환경에 따라 달라지는 값은 컨테이너 실행 시 주입한다.

## 내부 인증키와의 구분

`APP_ENV`는 환경 이름이고, `INTERNAL_SERVICE_KEY`는 Spring과 FastAPI 사이의 서버 간
인증 비밀값이다. 운영·스테이징에서는 `INTERNAL_SERVICE_KEY`를 Secret Manager 등에서
주입해야 하며, 두 값은 서로 대체할 수 없다.

```dotenv
APP_ENV=production
INTERNAL_SERVICE_KEY=<secret-manager-value>
```

`APP_ENV=local`인 로컬 Compose에서는 개발용 키 fallback을 사용할 수 있지만, 운영이나
스테이징에는 동일한 fallback 값을 사용하면 안 된다.

## 배포 전 확인

- API 컨테이너와 Celery worker 양쪽에 `APP_ENV`가 주입되는가
- 운영·스테이징 값이 각각 `production`·`staging`으로 설정되는가
- `INTERNAL_SERVICE_KEY`가 Secret Manager 등 외부 비밀 저장소에서 주입되는가
- CI 테스트 실행 전에 `APP_ENV=test`가 설정되는가
- `env_team` 등 비밀 파일을 저장소에 커밋하지 않았는가

AWS 리소스와 운영 환경변수 주입 예시는
`bigproject-infra/docs/EMAIL_ARTIFACT_S3_TERRAFORM_RUNBOOK.md`를 함께 참조한다.
