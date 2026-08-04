"""에이전트가 만든 필터 값을 SQLAlchemy 컬럼 타입에 맞게 정규화한다."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.sql.sqltypes import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    Integer,
    Numeric,
    SmallInteger,
    String,
)


class FilterValueError(ValueError):
    """필터 값을 컬럼의 SQL 타입으로 변환할 수 없을 때 발생한다."""


def normalize_filter_value(column_type: Any, value: Any) -> Any:
    """SQLAlchemy 컬럼 타입에 맞는 Python 바인드 값으로 변환한다.

    에이전트가 반환한 JSON은 문자열·숫자·불리언만 신뢰하고, SQL 실행 직전에
    컬럼 타입을 기준으로 다시 변환한다. timezone이 있는 DateTime에 timezone 없는
    값이 오면 시스템 기준 UTC로 해석해 aware datetime으로 만든다.
    """
    if isinstance(value, (list, tuple)):
        return type(value)(normalize_filter_value(column_type, item) for item in value)
    if value is None:
        raise FilterValueError("null filter values are not supported")

    if isinstance(column_type, DateTime):
        return _normalize_datetime(value, timezone_aware=bool(column_type.timezone))
    if isinstance(column_type, Date):
        return _normalize_date(value)
    if isinstance(column_type, Boolean):
        return _normalize_boolean(value)
    if isinstance(column_type, (Integer, SmallInteger, BigInteger)):
        return _normalize_integer(value)
    if isinstance(column_type, Numeric):
        return _normalize_numeric(value)
    if isinstance(column_type, Enum):
        return _normalize_enum(column_type, value)
    if isinstance(column_type, Float):
        return _normalize_float(value)
    if isinstance(column_type, String):
        return str(value)
    return value


def _normalize_datetime(value: Any, *, timezone_aware: bool) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            result = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise FilterValueError(f"invalid datetime value: {value!r}") from exc
    else:
        raise FilterValueError(f"invalid datetime value: {value!r}")

    if timezone_aware:
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result
    if result.tzinfo is not None:
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def _normalize_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise FilterValueError(f"invalid date value: {value!r}") from exc
    raise FilterValueError(f"invalid date value: {value!r}")


def _normalize_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise FilterValueError(f"invalid boolean value: {value!r}")


def _normalize_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise FilterValueError(f"invalid integer value: {value!r}")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FilterValueError(f"invalid integer value: {value!r}") from exc
    if not decimal.is_finite() or decimal != decimal.to_integral_value():
        raise FilterValueError(f"invalid integer value: {value!r}")
    return int(decimal)


def _normalize_numeric(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise FilterValueError(f"invalid numeric value: {value!r}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FilterValueError(f"invalid numeric value: {value!r}") from exc
    if not result.is_finite():
        raise FilterValueError(f"invalid numeric value: {value!r}")
    return result


def _normalize_float(value: Any) -> float:
    if isinstance(value, bool):
        raise FilterValueError(f"invalid float value: {value!r}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise FilterValueError(f"invalid float value: {value!r}") from exc
    if result != result or result in {float("inf"), float("-inf")}:
        raise FilterValueError(f"invalid float value: {value!r}")
    return result


def _normalize_enum(column_type: Enum, value: Any) -> Any:
    normalized = str(value)
    if column_type.enums and normalized not in column_type.enums:
        raise FilterValueError(
            f"invalid enum value {normalized!r}; expected one of {column_type.enums}"
        )
    return normalized
