"""업로드된 문서 파일을 검증하고 텍스트를 추출하는 오케스트레이션.

이 도메인은 상태를 갖지 않는다 — 파일을 디스크에 저장하지 않고 DB에도 아무것도 쓰지 않는다
(A안). 업로드된 바이트는 이 요청 처리가 끝나면 메모리에서 그대로 버려진다.

이 결과를 이후 어느 도메인이 소비할지(예: automation의 요구사항 분석 화면)는 이 서비스가
알 필요가 없다 — 그래서 특정 도메인에 종속된 제약을 그대로 import하지 않고 이 모듈 자체
상수로 둔다.
"""

from pathlib import Path

from fastapi import UploadFile

from app.common.errors import bad_request
from app.core.config import settings
from app.domains.documents import extractors
from app.domains.documents.schema import ExtractedTextResponse

# automation의 AnalyzeRequirementRequest.raw_request max_length(8000)과 의도적으로 값을
# 맞춰뒀다 — 이 서비스가 그 스키마를 직접 참조하진 않지만, 결과적으로 그 입력창에 들어갈
# 값이라 같은 기준으로 미리 잘라준다. 두 값이 갈라지면 안 되니 바꿀 땐 두 곳 다 확인할 것.
MAX_TEXT_LENGTH = 8000

_CHUNK_SIZE = 1024 * 1024  # 1MB


async def extract_text(upload: UploadFile) -> ExtractedTextResponse:
    """업로드 파일을 검증하고 텍스트를 추출해서 반환한다.

    실패 시 app.common.errors.DomainException(400)을 던진다 — 라우터는 별도 처리 없이
    그대로 전파시키면 된다.
    """
    extension = _validate_extension(upload.filename)
    raw = await _read_with_size_limit(upload)

    try:
        text = extractors.extract_by_extension(extension, raw)
    except Exception as exc:  # noqa: BLE001 — 파싱 라이브러리별 예외를 다 알 필요 없이 손상 파일로 통일
        raise bad_request(
            "INVALID_FILE",
            "파일을 열 수 없습니다. 손상되었거나 지원하지 않는 형식일 수 있습니다.",
        ) from exc

    text = extractors.clean_text(text)
    if not text:
        raise bad_request(
            "EMPTY_EXTRACTED_TEXT",
            "파일에서 텍스트를 추출할 수 없습니다. 스캔한 이미지 PDF가 아닌지 확인해주세요.",
        )

    truncated = len(text) > MAX_TEXT_LENGTH
    if truncated:
        text = text[:MAX_TEXT_LENGTH]

    return ExtractedTextResponse(
        extracted_text=text,
        filename=Path(upload.filename).name,
        truncated=truncated,
    )


def _validate_extension(filename: str | None) -> str:
    if not filename:
        raise bad_request("UNSUPPORTED_FILE_TYPE", "파일 이름을 확인할 수 없습니다.")
    extension = Path(filename).suffix.lower()
    if extension not in extractors.SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(extractors.SUPPORTED_EXTENSIONS))
        raise bad_request(
            "UNSUPPORTED_FILE_TYPE",
            f"지원하지 않는 파일 형식입니다. {allowed} 파일만 업로드할 수 있습니다.",
        )
    return extension


async def _read_with_size_limit(upload: UploadFile) -> bytes:
    """청크 단위로 읽어 메모리에 통째로 올리지 않고, 제한 초과 시 즉시 중단한다."""
    limit = settings.document_upload_max_bytes
    chunks: list[bytes] = []
    size = 0
    try:
        while chunk := await upload.read(_CHUNK_SIZE):
            size += len(chunk)
            if size > limit:
                raise bad_request(
                    "FILE_TOO_LARGE",
                    f"파일은 {limit // (1024 * 1024)}MB를 초과할 수 없습니다.",
                )
            chunks.append(chunk)
    finally:
        await upload.close()

    if size == 0:
        raise bad_request("EMPTY_EXTRACTED_TEXT", "빈 파일은 업로드할 수 없습니다.")

    return b"".join(chunks)
