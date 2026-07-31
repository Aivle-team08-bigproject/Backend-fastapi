def mask_phone(phone: str | None) -> str | None:
    """연락처를 010-****-5678 형식으로 마스킹한다. DB에는 마스킹 없이 숫자만 저장하고,
    API 응답에 내보낼 때만 이 함수를 거친다 (개인정보 표시 제한 보호조치)."""
    if not phone:
        return phone
    if len(phone) < 7:
        return "*" * len(phone)
    return f"{phone[:3]}-****-{phone[-4:]}"
