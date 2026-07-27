from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """모든 도메인 모델의 공통 베이스. 여기 자체는 테이블을 만들지 않는다."""
    metadata = MetaData(schema="service")