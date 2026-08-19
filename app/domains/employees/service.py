import secrets
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import security
from app.common.time_utils import utcnow
from app.common.errors import bad_request, conflict, not_found
from app.domains.auth.model.session_model import LoginSession
from app.domains.auth.schema.auth_schema import SignupRequest
from app.domains.employees.model import (
    AdminAuditLog,
    ConsentLog,
    Department,
    Employee,
    EmployeePermission,
    EmployeeRole,
    EmployeeStatus,
    PermissionCode,
    RolePermission,
)
from app.domains.employees.schema import ApproveSignupRequest, CreateEmployeeRequest

# 회원가입 시점에 동의받는 약관 버전. Frontend 공개 문서와 반드시 일치시킨다.
TERMS_VERSION = "TERMS-2026-08"
PRIVACY_VERSION = "PRIVACY-2026-08"

async def _find_employee(db: AsyncSession, employee_code: str) -> Employee:
    result = await db.execute(
        select(Employee)
        .options(selectinload(Employee.permissions), selectinload(Employee.department))
        .where(Employee.employee_code == employee_code)
    )
    employee = result.scalar_one_or_none()
    if employee is None:
        raise not_found("EMPLOYEE_NOT_FOUND", "직원 계정을 찾을 수 없습니다.")
    return employee

async def _revoke_all_sessions(db: AsyncSession, employee_id: int, reason: str) -> None:
    now = utcnow()
    await db.execute(
        update(LoginSession)
        .where(LoginSession.employee_id == employee_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason)
    )

async def _record_audit_log(
    db: AsyncSession,
    *,
    actor_employee_code: str,
    action: str,
    target_employee_code: str | None = None,
    detail: str | None = None,
) -> None:
    """민감한 관리자 조작을 감사 로그 테이블에 남긴다.

    주의: 이 함수는 db.commit()을 호출하지 않는다. 호출하는 쪽에서 실제 변경사항과
    감사 로그가 같은 트랜잭션으로 묶여서 커밋되도록 한다 (조작은 됐는데 로그만
    안 남는 상황을 피하기 위함).
    """
    db.add(
        AdminAuditLog(
            actor_employee_code=actor_employee_code,
            action=action,
            target_employee_code=target_employee_code,
            detail=detail,
            created_at=utcnow(),
        )
    )

async def _count_other_active_permission_holders(
    db: AsyncSession, permission: PermissionCode, excluding_employee_code: str
) -> int:
    """특정 권한을 가진, ACTIVE 상태의, 대상을 제외한 다른 직원 수를 센다.

    "마지막 관리자 보호"에 사용: 이 권한을 가진 사람이 대상 직원 하나뿐이라면
    그 직원의 권한을 뺏거나 계정을 비활성화하는 조작 자체를 막아야 한다.
    """
    result = await db.execute(
        select(Employee.id)
        .join(EmployeePermission, EmployeePermission.employee_id == Employee.id)
        .where(
            EmployeePermission.permission_code == permission,
            Employee.status == EmployeeStatus.ACTIVE,
            Employee.employee_code != excluding_employee_code,
        )
    )
    return len(result.all())

async def _assert_email_available(db: AsyncSession, email: str) -> None:
    existing = await db.execute(select(Employee.id).where(Employee.email == email))
    if existing.scalar_one_or_none() is not None:
        raise conflict("EMAIL_ALREADY_REGISTERED", "이미 사용 중인 이메일입니다.")


async def _assert_department_active(db: AsyncSession, department_id: int) -> None:
    result = await db.execute(
        select(Department.id).where(Department.id == department_id, Department.is_active.is_(True))
    )
    if result.scalar_one_or_none() is None:
        raise bad_request("DEPARTMENT_NOT_FOUND", "존재하지 않거나 비활성화된 부서입니다.")


async def list_departments(db: AsyncSession) -> list[Department]:
    result = await db.execute(
        select(Department).where(Department.is_active.is_(True)).order_by(Department.name.asc())
    )
    return list(result.scalars().all())


async def _get_role_permission_codes(db: AsyncSession, role: EmployeeRole) -> set[PermissionCode]:
    """role_permissions 테이블에서 역할에 대응하는 권한 템플릿을 읽어온다.

    예전엔 이 매핑이 코드(ROLE_PERMISSIONS dict)에 하드코딩돼 있었는데, 역할별 권한을
    조정할 때마다 배포가 필요했다. 이제는 DB가 정본이라 운영 중에도 값만 바꾸면 된다.
    """
    result = await db.execute(
        select(RolePermission.permission_code).where(RolePermission.role_code == role.value)
    )
    return {PermissionCode(code) for code in result.scalars().all()}


async def _record_consent(
    db: AsyncSession,
    *,
    employee_id: int,
    consent_type: str,
    version: str,
    agreed_at,
    ip_address: str | None,
) -> None:
    db.add(
        ConsentLog(
            employee_id=employee_id,
            consent_type=consent_type,
            version=version,
            agreed_at=agreed_at,
            ip_address=ip_address,
        )
    )


async def _generate_unique_employee_code(db: AsyncSession) -> str:
    """자기 가입자는 사번을 직접 정하지 않으므로 서버가 발급한다.

    PENDING_APPROVAL 상태에서는 로그인에 employee_code를 쓰지 않으니 형식보다는
    유일성이 중요하다. 충돌 시 재시도한다 (실제로 충돌할 확률은 매우 낮다).
    """
    for _ in range(5):
        candidate = f"SU-{secrets.token_hex(4).upper()}"
        existing = await db.execute(select(Employee.id).where(Employee.employee_code == candidate))
        if existing.scalar_one_or_none() is None:
            return candidate
    raise conflict("EMPLOYEE_CODE_GENERATION_FAILED", "사번 발급에 실패했습니다. 다시 시도해주세요.")


async def create_employee(
    db: AsyncSession, payload: CreateEmployeeRequest, created_by: str
) -> tuple[Employee, str]:
    existing = await db.execute(
        select(Employee.id).where(Employee.employee_code == payload.employee_code)
    )
    if existing.scalar_one_or_none() is not None:
        raise conflict("EMPLOYEE_CODE_DUPLICATED", "이미 사용 중인 직원 ID입니다.")
    await _assert_email_available(db, payload.email)
    await _assert_department_active(db, payload.department_id)

    temporary_password = security.generate_temporary_password()
    now = utcnow()

    employee = Employee(
        employee_code=payload.employee_code,
        name=payload.name,
        email=payload.email,
        department_id=payload.department_id,
        password_hash=security.hash_password(temporary_password),
        status=EmployeeStatus.ACTIVE,
        must_change_password=True,
        failed_login_count=0,
        auth_version=1,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    employee.permissions = [
        EmployeePermission(permission_code=code) for code in payload.permissions
    ]

    db.add(employee)
    await _record_audit_log(
        db,
        actor_employee_code=created_by,
        action="EMPLOYEE_CREATED",
        target_employee_code=payload.employee_code,
        detail=f"permissions={sorted(p.value for p in payload.permissions)}",
    )
    await db.commit()
    await db.refresh(employee, attribute_names=["permissions", "department"])

    return employee, temporary_password


async def signup(db: AsyncSession, payload: SignupRequest, ip_address: str | None = None) -> Employee:
    """직원 자율 회원가입. role/permissions는 절대 요청에서 받지 않고, 승인 전까지는
    권한 없이 PENDING_APPROVAL 상태로만 만든다 (관리자 승인 → 권한 부여 흐름).

    거절(REJECTED)된 이메일은 재신청을 허용한다 — 기존 행을 새 신청 내용으로 덮어써서
    PENDING_APPROVAL로 되돌린다 (employee_code는 유지되어 감사 로그 이력이 끊기지 않는다).
    PENDING_APPROVAL/ACTIVE/LOCKED/DISABLED 상태인 이메일은 그대로 중복으로 막는다.
    """
    await _assert_department_active(db, payload.department_id)

    existing = await db.execute(select(Employee).where(Employee.email == payload.email))
    employee = existing.scalar_one_or_none()

    if employee is not None and employee.status != EmployeeStatus.REJECTED:
        raise conflict("EMAIL_ALREADY_REGISTERED", "이미 사용 중인 이메일입니다.")

    now = utcnow()
    password_hash = security.hash_password(payload.password)

    if employee is None:
        employee = Employee(
            employee_code=await _generate_unique_employee_code(db),
            created_by="SELF_SIGNUP",
            failed_login_count=0,
            auth_version=1,
            created_at=now,
        )
        db.add(employee)

    employee.name = payload.name
    employee.email = payload.email
    employee.phone = payload.phone
    employee.department_id = payload.department_id
    employee.position = payload.position
    employee.password_hash = password_hash
    employee.status = EmployeeStatus.PENDING_APPROVAL
    employee.must_change_password = False
    employee.terms_agreed_at = now
    employee.terms_version = TERMS_VERSION
    employee.privacy_agreed_at = now
    employee.privacy_version = PRIVACY_VERSION
    employee.approved_by = None
    employee.approved_at = None
    employee.rejected_reason = None
    employee.updated_at = now

    try:
        await db.flush()  # 신규 행이면 employee.id 확정 (consent_logs FK에 필요)

        await _record_consent(
            db,
            employee_id=employee.id,
            consent_type="TERMS",
            version=TERMS_VERSION,
            agreed_at=now,
            ip_address=ip_address,
        )
        await _record_consent(
            db,
            employee_id=employee.id,
            consent_type="PRIVACY",
            version=PRIVACY_VERSION,
            agreed_at=now,
            ip_address=ip_address,
        )

        await db.commit()
    except IntegrityError:
        # 동시에 같은 이메일로 회원가입 요청이 들어오면 둘 다 위의 조회에서 "없음"으로 보고
        # INSERT를 시도할 수 있다. DB의 email UNIQUE 제약이 뒤늦게 하나를 막아주는데,
        # 여기서 잡지 않으면 500으로 새어나간다 — 처리되지 않은 예외이므로 409로 변환한다.
        await db.rollback()
        raise conflict("EMAIL_ALREADY_REGISTERED", "이미 사용 중인 이메일입니다.")

    await db.refresh(employee, attribute_names=["permissions", "department"])

    return employee


async def list_pending_signups(db: AsyncSession) -> list[Employee]:
    result = await db.execute(
        select(Employee)
        .options(selectinload(Employee.permissions), selectinload(Employee.department))
        .where(Employee.status == EmployeeStatus.PENDING_APPROVAL)
        .order_by(Employee.created_at.asc())
    )
    return list(result.scalars().all())


async def approve_signup(
    db: AsyncSession, employee_code: str, payload: ApproveSignupRequest, operator_code: str
) -> Employee:
    employee = await _find_employee(db, employee_code)
    if employee.status != EmployeeStatus.PENDING_APPROVAL:
        raise bad_request("SIGNUP_NOT_PENDING", "승인 대기 중인 가입 신청이 아닙니다.")

    now = utcnow()
    if payload.department_id is not None:
        await _assert_department_active(db, payload.department_id)
        employee.department_id = payload.department_id
    if payload.position is not None:
        employee.position = payload.position

    employee.status = EmployeeStatus.ACTIVE
    employee.approved_by = operator_code
    employee.approved_at = now
    employee.role_code = payload.role.value
    employee.permissions = [
        EmployeePermission(permission_code=code)
        for code in await _get_role_permission_codes(db, payload.role)
    ]
    employee.auth_version += 1
    employee.updated_at = now

    await _record_audit_log(
        db,
        actor_employee_code=operator_code,
        action="EMPLOYEE_SIGNUP_APPROVED",
        target_employee_code=employee_code,
        detail=f"role={payload.role.value}",
    )
    await db.commit()
    await db.refresh(employee, attribute_names=["permissions", "department"])

    return employee


async def reject_signup(
    db: AsyncSession, employee_code: str, reason: str, operator_code: str
) -> Employee:
    employee = await _find_employee(db, employee_code)
    if employee.status != EmployeeStatus.PENDING_APPROVAL:
        raise bad_request("SIGNUP_NOT_PENDING", "승인 대기 중인 가입 신청이 아닙니다.")

    employee.status = EmployeeStatus.REJECTED
    employee.rejected_reason = reason
    employee.auth_version += 1
    employee.updated_at = utcnow()

    await _record_audit_log(
        db,
        actor_employee_code=operator_code,
        action="EMPLOYEE_SIGNUP_REJECTED",
        target_employee_code=employee_code,
        detail=reason,
    )
    await db.commit()
    await db.refresh(employee, attribute_names=["permissions", "department"])

    return employee

async def find_all(db: AsyncSession) -> list[Employee]:
    result = await db.execute(
        select(Employee)
        .options(selectinload(Employee.permissions), selectinload(Employee.department))
        .order_by(Employee.created_at.desc())
    )
    return list(result.scalars().all())

async def find_one(db: AsyncSession, employee_code: str) -> Employee:
    return await _find_employee(db, employee_code)

async def replace_permissions(
    db: AsyncSession,
    employee_code: str,
    permissions: set[PermissionCode],
    operator_code: str,
    role_code: str | None = None,
) -> Employee:
    employee = await _find_employee(db, employee_code)

    if employee_code == operator_code and PermissionCode.EMPLOYEE_PERMISSION_MANAGE not in permissions:
        raise bad_request(
            "SELF_PERMISSION_REMOVAL_BLOCKED", "본인의 권한 관리 권한은 직접 제거할 수 없습니다."
        )

    # 마지막 관리자 보호: 대상 직원이 EMPLOYEE_PERMISSION_MANAGE를 잃게 되는데,
    # 그 권한을 가진 다른 활성 직원이 아무도 없다면 막는다.
    currently_has_manage = PermissionCode.EMPLOYEE_PERMISSION_MANAGE in {
        p.permission_code for p in employee.permissions
    }
    losing_manage = currently_has_manage and PermissionCode.EMPLOYEE_PERMISSION_MANAGE not in permissions
    if losing_manage:
        other_holders = await _count_other_active_permission_holders(
            db, PermissionCode.EMPLOYEE_PERMISSION_MANAGE, employee_code
        )
        if other_holders == 0:
            raise bad_request(
                "LAST_PERMISSION_MANAGER_BLOCKED",
                "이 직원의 권한 관리 권한을 제거하면 권한을 관리할 수 있는 관리자가 아무도 남지 않습니다.",
            )

    previous = sorted(p.permission_code.value for p in employee.permissions)
    employee.permissions = [EmployeePermission(permission_code=code) for code in permissions]
    if role_code is not None:
        employee.role_code = role_code
    employee.auth_version += 1
    employee.updated_at = utcnow()

    await _revoke_all_sessions(db, employee.id, "관리자에 의한 권한 변경")
    await _record_audit_log(
        db,
        actor_employee_code=operator_code,
        action="PERMISSIONS_CHANGED",
        target_employee_code=employee_code,
        detail=f"before={previous} after={sorted(p.value for p in permissions)}",
    )
    await db.commit()
    await db.refresh(employee, attribute_names=["permissions", "department"])

    return employee


async def replace_role(
    db: AsyncSession, employee_code: str, role: EmployeeRole, operator_code: str
) -> Employee:
    permissions = await _get_role_permission_codes(db, role)
    return await replace_permissions(db, employee_code, permissions, operator_code, role_code=role.value)

async def change_status(
    db: AsyncSession, employee_code: str, status: EmployeeStatus, operator_code: str
) -> Employee:
    employee = await _find_employee(db, employee_code)

    if employee_code == operator_code and status != EmployeeStatus.ACTIVE:
        raise bad_request("SELF_DISABLE_BLOCKED", "본인 계정은 직접 잠그거나 비활성화할 수 없습니다.")

    # 마지막 관리자 보호: ACTIVE가 아닌 상태로 바뀌면 이 직원은 사실상 아무 권한도
    # 행사할 수 없게 된다. EMPLOYEE_PERMISSION_MANAGE를 가진 마지막 활성 직원이라면 막는다.
    has_manage = PermissionCode.EMPLOYEE_PERMISSION_MANAGE in {p.permission_code for p in employee.permissions}
    if status != EmployeeStatus.ACTIVE and has_manage:
        other_holders = await _count_other_active_permission_holders(
            db, PermissionCode.EMPLOYEE_PERMISSION_MANAGE, employee_code
        )
        if other_holders == 0:
            raise bad_request(
                "LAST_PERMISSION_MANAGER_BLOCKED",
                "이 직원을 비활성화하면 권한을 관리할 수 있는 관리자가 아무도 남지 않습니다.",
            )

    previous_status = employee.status.value
    employee.status = status
    # 비활성/잠금 계정을 다시 활성화하면 로그인 차단 상태도 함께 해제해야 한다.
    # status만 ACTIVE로 바꾸면 locked_until이 남아 auth_service.login에서
    # ACCOUNT_LOCKED로 계속 거부되는 문제가 발생한다.
    if status == EmployeeStatus.ACTIVE:
        employee.locked_until = None
        employee.failed_login_count = 0
    employee.auth_version += 1
    employee.updated_at = utcnow()

    await _revoke_all_sessions(db, employee.id, f"직원 상태 변경: {status}")
    await _record_audit_log(
        db,
        actor_employee_code=operator_code,
        action="STATUS_CHANGED",
        target_employee_code=employee_code,
        detail=f"before={previous_status} after={status.value}",
    )
    await db.commit()
    await db.refresh(employee, attribute_names=["permissions", "department"])

    return employee

async def reset_password(db: AsyncSession, employee_code: str, operator_code: str) -> tuple[Employee, str]:
    employee = await _find_employee(db, employee_code)
    temporary_password = security.generate_temporary_password()

    employee.password_hash = security.hash_password(temporary_password)
    employee.must_change_password = True
    employee.auth_version += 1
    employee.updated_at = utcnow()

    await _revoke_all_sessions(db, employee.id, "관리자에 의한 비밀번호 초기화")
    await _record_audit_log(
        db,
        actor_employee_code=operator_code,
        action="PASSWORD_RESET",
        target_employee_code=employee_code,
    )
    await db.commit()
    await db.refresh(employee, attribute_names=["permissions", "department"])

    return employee, temporary_password

async def list_audit_logs(db: AsyncSession, limit: int = 200) -> list[AdminAuditLog]:
    result = await db.execute(
        select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def find_sessions_by_employee(db: AsyncSession, employee_code: str) -> list[LoginSession]:
    result = await db.execute(
        select(LoginSession)
        .join(Employee, Employee.id == LoginSession.employee_id)
        .where(Employee.employee_code == employee_code)
        .order_by(LoginSession.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_session(db: AsyncSession, session_id: uuid.UUID, operator_code: str) -> None:
    session = await db.get(LoginSession, session_id)
    if session is None:
        raise not_found("SESSION_NOT_FOUND", "로그인 세션을 찾을 수 없습니다.")

    employee = await db.get(Employee, session.employee_id)
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoke_reason = f"관리자 강제 로그아웃: {operator_code}"
        db.add(
            AdminAuditLog(
                actor_employee_code=operator_code,
                action="SESSION_REVOKED",
                target_employee_code=employee.employee_code if employee else None,
                detail=f"session_id={session_id}",
                created_at=utcnow(),
            )
        )
    await db.commit()
