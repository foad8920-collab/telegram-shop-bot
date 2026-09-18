"""add user language preference

Revision ID: 24a5b76d4ac0
Revises: cf79fcf985f3
Create Date: 2026-09-18 19:17:00.133530

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '24a5b76d4ac0'
down_revision: Union[str, None] = 'cf79fcf985f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'language_code',
            sa.String(length=10),
            server_default='ar',
            nullable=False
        )
    )


def downgrade() -> None:
    op.drop_column(
        'users',
        'language_code'
    )