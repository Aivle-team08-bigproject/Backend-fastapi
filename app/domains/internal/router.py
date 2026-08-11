from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import bad_request
from app.db.session import get_db
from app.domains.internal.security import verify_internal_service_key
from app.domains.pipeline.schema import InternalDeliveryLookupResponse
from app.domains.pipeline.service import lookup_customer_delivery

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    dependencies=[Depends(verify_internal_service_key)],
)


@router.get(
    "/deliveries/{contract_no}/artifact",
    response_model=InternalDeliveryLookupResponse,
)
async def get_delivery_artifact(
    contract_no: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> InternalDeliveryLookupResponse:
    """Spring 전용. 고객이 보낸 X-API-Key를 대신 검증하고 S3 storage_key만 돌려준다.

    사내 민감 DB(employees/clients 등)는 이 서버 안에서만 조회되며, Spring에는
    이 응답 필드 3개(storage_key/mime_type/artifact_filename) 외에는 아무것도
    노출하지 않는다.
    """
    if not x_api_key:
        raise bad_request("MISSING_API_KEY", "X-API-Key 헤더가 필요합니다.")
    result = await lookup_customer_delivery(db, contract_no, x_api_key)
    return InternalDeliveryLookupResponse(**result)
