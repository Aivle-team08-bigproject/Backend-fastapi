from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security_deps import CurrentAuth, get_current_auth
from app.common.time_utils import as_utc, utcnow
from app.core.config import settings
from app.db.session import get_db
from app.domains.auth.schema.auth_schema import (
    ChangePasswordRequest,
    EmployeeSummary,
    LoginRequest,
    LoginResponse,
    SignupRequest,
    SignupResponse,
    TokenResponse,
)
from app.domains.auth.service import auth_service
from app.domains.employees import service as employee_service
from app.domains.employees.model import Employee

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _to_summary(
    employee: Employee,
    permissions: set | None = None,
) -> EmployeeSummary:
    return EmployeeSummary(
        employee_code=employee.employee_code,
        name=employee.name,
        email=employee.email,
        department_id=employee.department_id,
        department_name=employee.department.name if employee.department else None,
        role=employee.role_code,
        status=employee.status.value,
        must_change_password=employee.must_change_password,
        permissions=sorted(
            permission.value
            for permission in (
                permissions if permissions is not None else {p.permission_code for p in employee.permissions}
            )
        ),
    )


def _expires_in_seconds(expires_at: datetime) -> int:
    return max(0, int((as_utc(expires_at) - utcnow()).total_seconds()))


def _set_refresh_cookie(response: Response, raw_token: str, session_expires_at: datetime) -> None:
    max_age = max(0, int((as_utc(session_expires_at) - utcnow()).total_seconds()))
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path="/api/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path="/api/auth",
    )


@router.post("/signup", response_model=SignupResponse)
async def signup(
    payload: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SignupResponse:
    ip_address = request.client.host if request.client else None
    employee = await employee_service.signup(db, payload, ip_address)
    return SignupResponse(
        employee_code=employee.employee_code,
        email=employee.email,
        status=employee.status.value,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    result = await auth_service.login(db, payload, ip_address, user_agent)
    _set_refresh_cookie(response, result.raw_refresh_token, result.session.expires_at)

    return LoginResponse(
        access_token=result.access_token,
        expires_in_seconds=_expires_in_seconds(result.access_token_expires_at),
        expires_at=result.access_token_expires_at,
        employee=_to_summary(result.employee),
    )


@router.post("/review-auto-login", response_model=LoginResponse)
async def review_auto_login(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """심사 기간에만 runtime Secret 설정으로 열리는 자동 로그인 경로."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    result = await auth_service.review_auto_login(db, ip_address, user_agent)
    _set_refresh_cookie(response, result.raw_refresh_token, result.session.expires_at)
    return LoginResponse(
        access_token=result.access_token,
        expires_in_seconds=_expires_in_seconds(result.access_token_expires_at),
        expires_at=result.access_token_expires_at,
        employee=_to_summary(result.employee),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    result = await auth_service.refresh(db, raw_token)
    _set_refresh_cookie(response, result.raw_refresh_token, result.session.expires_at)

    return TokenResponse(
        access_token=result.access_token,
        expires_in_seconds=_expires_in_seconds(result.access_token_expires_at),
        expires_at=result.access_token_expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    await auth_service.logout(db, auth.session.id, auth.employee.employee_code)
    _clear_refresh_cookie(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    response: Response,
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    await auth_service.logout_all(db, auth.employee.employee_code)
    _clear_refresh_cookie(response)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    await auth_service.change_password(db, auth.employee.employee_code, payload)
    _clear_refresh_cookie(response)


@router.get("/me", response_model=EmployeeSummary)
async def me(auth: CurrentAuth = Depends(get_current_auth)) -> EmployeeSummary:
    return _to_summary(auth.employee, auth.permissions)
