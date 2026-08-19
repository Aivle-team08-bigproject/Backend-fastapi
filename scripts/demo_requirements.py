"""시연용 더미 작업에 공통으로 사용하는 자연어 요구사항 생성기."""


def _focus_for(title: str) -> str:
    text = title.lower()
    if any(word in text for word in ("이상", "부정", "인증", "신용")):
        return "승인·거절·인증 방식·거래 금액과 같은 결제 흐름의 이상 패턴을 집계해 비교"
    if any(word in text for word in ("지역", "상권", "동선", "밀집", "배송")):
        return "가맹점 지역과 시간대별 거래 건수·금액을 집계해 지역 및 상권별 차이를 비교"
    if any(word in text for word in ("재구매", "구매", "소비", "매출", "전환", "유통", "렌탈")):
        return "업종·가맹점·고객군·시간대별 거래 건수와 금액을 집계해 구매 및 매출 흐름을 비교"
    if any(word in text for word in ("연령", "인구", "이용자")):
        return "연령대와 지역·업종별 거래 분포를 개인을 식별할 수 없는 집단 단위로 비교"
    return "제목의 업무 목적에 맞는 거래·가맹점·고객 속성을 집단 단위로 집계해 차이를 비교"


def build_demo_requirement(title: str, business_purpose: str | None = None) -> str:
    """날짜를 고정하지 않고 실제 메타데이터에서 분석 기간을 선택하도록 만든다."""
    purpose = business_purpose or f"'{title}' 업무 목적"
    focus = _focus_for(title)
    return (
        f"{purpose}를 위해 '{title}'을(를) 분석할 수 있는 비식별 결제 데이터 가공을 요청합니다. "
        f"{focus}할 수 있도록 필요한 원천 컬럼과 파생 컬럼을 설계해주세요. "
        "개인 식별자, 카드번호, 이메일, 전화번호, 원문 주소와 같은 직접 식별값은 제외하고 "
        "필요한 경우 연령대·지역·업종·시간대처럼 집단화한 값만 사용해주세요. "
        "특정 날짜나 연도를 고정하지 말고, 실제 메타데이터와 보유 데이터에서 분석 가능한 전체 기간을 확인한 뒤 "
        "LLM이 유효한 기간을 선택해주세요. 결과는 재식별 위험이 없는 집계 데이터로 만들고, "
        "선택한 컬럼·필터·집계 기준·제외한 컬럼과 사유를 함께 기록해 CSV 또는 XLSX로 제공합니다."
    )
