"""체크섬 3종.

전부 **숫자만 남긴 문자열**을 받는다. 하이픈 제거는 호출부 책임이다.

🔑 체크섬은 BLOCK 조건으로만 쓴다. PASS 조건으로 쓰면 안 된다.

   2020년 10월부터 주민등록번호 뒷 7자리 중 성별 1자리를 뺀 6자리가 임의번호로
   바뀌었다. 검증번호(13번째)도 그 6자리에 포함되므로 **그 이후 발급분은 체크섬이
   성립하지 않는다.** 체크섬을 통과 판정에 쓰면 탐지기가 해마다 조용히 약해진다.

   RRN 만 체크섬 실패 시에도 FLAG 인 이유가 이것이다. BRN·CARD 는 이런 개정이
   없어 체크섬 실패를 PASS 로 봐도 된다.
"""

from __future__ import annotations

RRN_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
BRN_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)


def rrn_checksum_valid(digits: str) -> bool:
    """주민등록번호 검증번호. 앞 12자리 × 가중치 합 → (11 - 합%11) % 10."""
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:12], RRN_WEIGHTS))
    return (11 - total % 11) % 10 == int(digits[12])


def brn_checksum_valid(digits: str) -> bool:
    """사업자등록번호 검증번호.

    9번째 자리에 5를 곱한 값의 **십의 자리**를 따로 더하는 게 이 알고리즘의 특징이다.
    그 항을 빠뜨리면 통과율이 절반으로 떨어진다.
    """
    if len(digits) != 10 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:9], BRN_WEIGHTS))
    total += (int(digits[8]) * 5) // 10
    return (10 - total % 10) % 10 == int(digits[9])


def luhn_valid(digits: str) -> bool:
    """Luhn. 카드번호 표준 검증식."""
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def birthdate_valid(digits6: str) -> bool:
    """주민번호 앞 6자리(YYMMDD)의 월·일이 실재하는 범위인가.

    연도는 세기를 모르므로 검사하지 않는다(성별코드가 세기를 나타내지만, 그것까지
    엮으면 규칙이 복잡해지는 데 비해 걸러내는 양이 적다).

    이 검사가 `881301-1234568` 같은 코드값을 걸러낸다 — 13월은 존재하지 않는다.
    윤년까지 따지지 않는 이유는 `0229` 를 통과시켜도 손해가 없기 때문이다.
    여기서 하려는 일은 날짜 검증이 아니라 **명백한 비-주민번호 제거**다.
    """
    if len(digits6) != 6 or not digits6.isdigit():
        return False
    month = int(digits6[2:4])
    day = int(digits6[4:6])
    if not 1 <= month <= 12:
        return False
    max_day = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
    return 1 <= day <= max_day
