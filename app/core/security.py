"""
보안 관련 저수준 유틸.

주의: passlib은 bcrypt 4.1+ 와 호환성 문제가 있어(passlib이 더 이상 유지보수되지 않음,
bcrypt 패키지에서 __about__ 속성이 제거되며 깨짐) 여기서는 passlib 없이 bcrypt 패키지를
직접 사용한다.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

import bcrypt
import jwt

from app.common.time_utils import utcnow

from app.core.config import settings

BCRYPT_ROUNDS = 12


# ---------- 비밀번호 해시 (BCrypt) ----------

def hash_password(raw_password: str) -> str:
    encoded = raw_password.encode("utf-8")
    if len(encoded) > 72:
        # bcrypt는 72바이트를 넘는 입력을 허용하지 않는다.
        raise ValueError("비밀번호는 72바이트를 초과할 수 없습니다.")
    hashed = bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def verify_password(raw_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


# ---------- Access Token (JWT) ----------

def create_access_token(
    *,
    employee_code: str,
    session_id: uuid.UUID,
    auth_version: int,
    name: str,
    department: str | None,
) -> tuple[str, datetime]:
    now = utcnow()
    expires_at = now + timedelta(minutes=settings.access_token_ttl_minutes)

    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": employee_code,
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
        "sid": str(session_id),
        "ver": auth_version,
        "name": name,
        "department": department,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, expires_at


def decode_access_token(token: str) -> dict:
    """서명·만료·issuer·audience를 전부 검증한다. 실패 시 jwt.PyJWTError 하위 예외 발생."""
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )


# ---------- Refresh Token ----------

def generate_raw_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------- 임시 비밀번호 생성 ----------

_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # 혼동되는 I, O 제외
_LOWER = "abcdefghijkmnopqrstuvwxyz"  # 혼동되는 l 제외
_DIGITS = "23456789"  # 혼동되는 0, 1 제외
_SPECIAL = "!@#$%^&*"
_ALL = _UPPER + _LOWER + _DIGITS + _SPECIAL


def generate_temporary_password(length: int = 16) -> str:
    """대문자·소문자·숫자·특수문자를 각 1개 이상 포함하는 임시 비밀번호를 생성한다."""
    chars = [
        secrets.choice(_UPPER),
        secrets.choice(_LOWER),
        secrets.choice(_DIGITS),
        secrets.choice(_SPECIAL),
    ]
    chars += [secrets.choice(_ALL) for _ in range(length - len(chars))]

    rng = secrets.SystemRandom()
    rng.shuffle(chars)
    return "".join(chars)
