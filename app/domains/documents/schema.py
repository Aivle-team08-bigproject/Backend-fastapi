from pydantic import BaseModel, Field


class ExtractedTextResponse(BaseModel):
    """업로드한 문서에서 뽑아낸 텍스트. 이 도메인은 아무것도 DB에 저장하지 않는다 —
    호출부(예: automation 요구사항 분석 화면)가 이 결과를 자기 입력창에 채워 넣고,
    사용자가 검토·수정한 뒤 각자의 제출 API를 별도로 호출한다.
    """

    extracted_text: str = Field(description="파일에서 추출한 텍스트 원문(형식만 정리됨).")
    filename: str = Field(description="업로드한 파일의 원본 파일명.")
    truncated: bool = Field(
        description="추출된 텍스트가 길이 제한(MAX_TEXT_LENGTH)을 초과해 잘렸는지 여부."
    )
