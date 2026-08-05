from fastapi import APIRouter, Depends, File, UploadFile

from app.common.security_deps import CurrentAuth, get_current_auth
from app.domains.documents import service
from app.domains.documents.schema import ExtractedTextResponse

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/extract-text", response_model=ExtractedTextResponse)
async def extract_text_from_document(
    file: UploadFile = File(...),
    auth: CurrentAuth = Depends(get_current_auth),
) -> ExtractedTextResponse:
    """업로드한 문서(.txt/.docx/.pdf)에서 텍스트를 추출해서 반환한다.

    이 엔드포인트는 어떤 에이전트도 실행하지 않고 DB에도 아무것도 쓰지 않는다 — 순수하게
    파일을 텍스트로 바꿔주기만 한다. 반환된 텍스트를 어떻게 쓸지(요구사항 분석 화면의
    입력창에 채워 넣는 등)는 호출부(프론트엔드)의 몫이다.
    """
    return await service.extract_text(file)
