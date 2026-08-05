"""파일 바이트에서 텍스트를 뽑아내는 순수 함수 모음.

이 모듈은 판단(요약/재작성/키워드 선택)을 하지 않는다 — 원본 파일 안의 텍스트를
순서대로 그대로 이어붙이기만 한다. 유일하게 하는 "정리"는 파싱 과정에서 섞여드는
형식적 부산물(제어문자, 과도한 빈 줄) 제거뿐이고, 문장·단어·내용은 절대 바꾸지 않는다.

여기서 던지는 예외는 전부 이 모듈이 처리하지 않는다 — 호출부(service.py)가
잡아서 사용자에게 보여줄 에러로 변환한다.
"""

import io
import re

import docx
import pypdf

# 지원하는 확장자. service.py의 업로드 검증과 이 모듈의 디스패치가 이 집합을 함께 참조한다.
SUPPORTED_EXTENSIONS = {".txt", ".docx", ".pdf"}

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


def extract_txt(raw: bytes) -> str:
    """UTF-8(BOM 포함)로 우선 시도하고, 실패하면 CP949(한글 레거시 인코딩)로 폴백한다."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


def extract_docx(raw: bytes) -> str:
    """모든 문단 텍스트를 순서대로 이어붙인다. 표/이미지 안의 텍스트는 다루지 않는다."""
    document = docx.Document(io.BytesIO(raw))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def extract_pdf(raw: bytes) -> str:
    """모든 페이지의 텍스트를 순서대로 이어붙인다. 스캔 이미지 PDF는 빈 문자열이 나올 수 있다."""
    reader = pypdf.PdfReader(io.BytesIO(raw))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


_EXTRACTORS = {
    ".txt": extract_txt,
    ".docx": extract_docx,
    ".pdf": extract_pdf,
}


def extract_by_extension(extension: str, raw: bytes) -> str:
    """확장자에 맞는 추출기를 호출한다.

    extension은 소문자 점 포함 형태(예: ".pdf")여야 한다. 지원하지 않는 확장자를 넘기면
    KeyError — 호출부가 SUPPORTED_EXTENSIONS로 미리 검증했다는 전제 하에 동작한다.
    """
    return _EXTRACTORS[extension](raw)


def clean_text(text: str) -> str:
    """추출된 텍스트의 형식만 정리한다 — 문장·단어·내용은 절대 건드리지 않는다.

    - 제어문자(널문자 등 파싱 부산물) 제거
    - 3줄 이상 연속된 빈 줄을 2줄(빈 줄 하나)로 축소
    - 앞뒤 공백 제거
    """
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _EXCESS_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
