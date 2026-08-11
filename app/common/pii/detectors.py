"""탐지기 7종 — 정규식 + 체크섬.

**Presidio 는 배제했다.** spacy 모델이 500MB+ 라 컨테이너 이미지와 AWS 비용에
영향이 크다. 그 대신 이름·주소는 탐지하지 않는다(NER 없이는 불가능하다).

## 오탐을 줄이는 게 재현율만큼 중요하다

카드사 데이터 요청서는 숫자가 많다. 금액·기간·MCC 코드·건수가 매 문장에 나온다.
**경고가 잦으면 사람들이 무시하게 되고, 그러면 탐지기 전체가 무력해진다.**
`ACCOUNT` 에 문맥어 근접 조건을 붙인 이유가 이것이다.

## 숫자 경계에 `\\b` 를 쓰지 않는다

`\\b` 는 한글과 숫자 사이에서도 경계로 잡히지만, 우리가 막고 싶은 것은
"긴 숫자열의 일부만 잘라내 매치하는 것"이다. `(?<![0-9])` / `(?![0-9])` 가
그 의도를 정확히 표현한다.
"""

from __future__ import annotations

import re

from app.common.pii.checksums import (
    birthdate_valid,
    brn_checksum_valid,
    luhn_valid,
    rrn_checksum_valid,
)
from app.common.pii.types import Match, Severity

# 주민등록번호 — 6자리 생년월일 + (하이픈) + 성별 1~8 + 6자리
# 성별코드를 정규식에 넣어 `880101-9234568` 같은 값을 애초에 후보에서 뺀다.
RRN_RE = re.compile(r"(?<![0-9])(\d{6})-?([1-8]\d{6})(?![0-9])")

# 사업자등록번호 — 3-2-5. 하이픈은 각각 선택적이다.
BRN_RE = re.compile(r"(?<![0-9])(\d{3})-?(\d{2})-?(\d{5})(?![0-9])")

# 카드번호 — 13~19자리. 구분자는 하이픈/공백을 허용한다.
CARD_RE = re.compile(r"(?<![0-9])(?:\d[- ]?){12,18}\d(?![0-9])")

# 발급사 식별번호(IIN) 앞자리. 실재하는 카드 브랜드 대역만 인정한다.
#
#   2[2-7]  MasterCard 2-series      3[0-9]  Amex · Diners · JCB
#   4       Visa                     5[1-5]  MasterCard
#   6[0-6]  Discover · UnionPay
#
# Luhn 은 10자리 중 1개꼴로 우연히 통과한다. 길이만 맞으면 통과시키면
# `12880101123456812` 같은 일련번호가 BLOCK 된다(실제로 검증 벡터 RRN-07 이
# 이 경우다). **IIN 검사가 Luhn 만큼 중요하다.**
CARD_IIN_RE = re.compile(r"^(?:2[2-7]|3[0-9]|4|5[1-5]|6[0-6])")

# 휴대전화 — 01X + 3~4 + 4
PHONE_RE = re.compile(r"(?<![0-9])01[016789]-?\d{3,4}-?\d{4}(?![0-9])")

# 이메일 — RFC 약식
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")

# 계좌번호 — 10~14자리. 문맥어가 근접해 있을 때만 인정한다.
ACCOUNT_RE = re.compile(r"(?<![0-9])(?:\d[- ]?){9,13}\d(?![0-9])")
ACCOUNT_CONTEXT_WORDS = ("계좌", "입금", "송금", "예금주")
ACCOUNT_CONTEXT_WINDOW = 20

# 여권번호 — 영문 1자(M/S/R/O/G) + 숫자 8자리
PASSPORT_RE = re.compile(r"(?<![A-Za-z0-9])[MSROG]\d{8}(?![A-Za-z0-9])")


def _digits(text: str) -> str:
    return re.sub(r"[^0-9]", "", text)


def detect_rrn(text: str) -> list[Match]:
    """주민등록번호.

    🔑 체크섬 실패도 FLAG 다. PASS 가 아니다 — 2020-10 이후 발급분은 체크섬이
    성립하지 않는다. `checksums.py` 상단 주석 참조.
    """
    out: list[Match] = []
    for m in RRN_RE.finditer(text):
        digits = _digits(m.group(0))
        if not birthdate_valid(digits[:6]):
            continue  # 13월 같은 코드값. 주민번호가 아니다
        valid = rrn_checksum_valid(digits)
        out.append(
            Match(
                start=m.start(),
                end=m.end(),
                checksum_valid=valid,
                severity=Severity.BLOCK if valid else Severity.FLAG,
            )
        )
    return out


def detect_brn(text: str) -> list[Match]:
    """사업자등록번호. 체크섬 실패는 탐지하지 않는다.

    주민번호와 달리 형식 개정이 없어서, 체크섬 실패를 "사업자번호가 아니다"로
    단정해도 된다. 계약번호·요청번호가 같은 형태로 자주 나오므로 이 구분이 중요하다.
    """
    out: list[Match] = []
    for m in BRN_RE.finditer(text):
        if not brn_checksum_valid(_digits(m.group(0))):
            continue
        out.append(
            Match(start=m.start(), end=m.end(), checksum_valid=True, severity=Severity.BLOCK)
        )
    return out


def detect_card(text: str) -> list[Match]:
    """카드번호. Luhn 실패는 탐지하지 않는다.

    두 관문을 다 통과해야 한다.

        IIN    앞자리가 실재하는 카드 브랜드 대역인가
        Luhn   검증식이 맞는가

    **Luhn 만으로는 부족하다.** 10자리 중 1개꼴로 우연히 통과하므로, 긴 일련번호가
    BLOCK 되는 사고가 난다. IIN 이 그 대부분을 앞에서 자른다.

    ⚠️ 구분자로 공백을 허용하므로, 숫자 두 덩이가 공백으로 이어진 문자열도 후보가
    된다. 공백 4자리 그룹 표기("4111 1111 1111 1111")가 실제로 흔해서 감수했다.
    """
    out: list[Match] = []
    for m in CARD_RE.finditer(text):
        digits = _digits(m.group(0))
        if not 13 <= len(digits) <= 19:
            continue
        if not CARD_IIN_RE.match(digits) or not luhn_valid(digits):
            continue
        out.append(
            Match(start=m.start(), end=m.end(), checksum_valid=True, severity=Severity.BLOCK)
        )
    return out


def detect_phone(text: str) -> list[Match]:
    """휴대전화. 체크섬이 없으므로 전부 FLAG.

    `"결과는 담당자 010-… 로 보내주세요"` 는 업무상 정당한 요청이다. 막을 이유가
    없고, 확인만 받으면 된다.
    """
    return [
        Match(start=m.start(), end=m.end(), checksum_valid=None, severity=Severity.FLAG)
        for m in PHONE_RE.finditer(text)
    ]


def detect_email(text: str) -> list[Match]:
    """이메일. 전부 FLAG."""
    return [
        Match(start=m.start(), end=m.end(), checksum_valid=None, severity=Severity.FLAG)
        for m in EMAIL_RE.finditer(text)
    ]


def detect_account(text: str) -> list[Match]:
    """계좌번호. **문맥어가 앞뒤 20자 안에 있을 때만** 인정한다.

    은행별 자릿수·구분자가 제각각이라 숫자만으로는 오탐이 폭발한다. 우리 도메인의
    `"식별번호 110234567890 을 기준으로 집계"` 같은 문장이 전부 걸리면 탐지기를
    아무도 신뢰하지 않게 된다.
    """
    out: list[Match] = []
    for m in ACCOUNT_RE.finditer(text):
        window = text[max(0, m.start() - ACCOUNT_CONTEXT_WINDOW) : m.end() + ACCOUNT_CONTEXT_WINDOW]
        if not any(word in window for word in ACCOUNT_CONTEXT_WORDS):
            continue
        out.append(
            Match(start=m.start(), end=m.end(), checksum_valid=None, severity=Severity.FLAG)
        )
    return out


def detect_passport(text: str) -> list[Match]:
    """여권번호. 전부 FLAG."""
    return [
        Match(start=m.start(), end=m.end(), checksum_valid=None, severity=Severity.FLAG)
        for m in PASSPORT_RE.finditer(text)
    ]


# 겹침이 생겼을 때 남길 순서. 앞쪽이 이긴다.
#
# 주민번호 13자리가 Luhn 을 우연히 통과하면 RRN 과 CARD 가 같은 구간을 잡는다.
# 둘 다 남기면 "2건 탐지"가 되어 사실과 다르다. 더 구체적인 형식(생년월일·성별코드를
# 검사하는 RRN)을 우선한다.
DETECTOR_PRIORITY: tuple[tuple[str, object], ...] = (
    ("RRN", detect_rrn),
    ("CARD", detect_card),
    ("BRN", detect_brn),
    ("ACCOUNT", detect_account),
    ("PHONE", detect_phone),
    ("PASSPORT", detect_passport),
    ("EMAIL", detect_email),
)
