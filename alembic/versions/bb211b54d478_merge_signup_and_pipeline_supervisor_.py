"""merge signup and pipeline-supervisor migrations

Revision ID: bb211b54d478
Revises: 401bffebdcd9, a1c4e77b93f0
Create Date: 2026-07-31 13:20:46.918296

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb211b54d478'
down_revision: Union[str, Sequence[str], None] = ('401bffebdcd9', 'a1c4e77b93f0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
