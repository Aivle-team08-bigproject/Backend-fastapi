from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # 스키마를 지정하지 않으면 search_path의 첫 스키마(public)로 간다.
    # 테이블 정의는 sqlfiles/migrations/V009가 소유하며, 여기서는 매핑만 맞춘다.
    metadata = MetaData(schema="automation")