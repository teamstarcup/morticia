"""create known repos table

Revision ID: 2a24e2410cbd
Revises: 
Create Date: 2025-09-03 01:00:37.346752

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a24e2410cbd'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('known_repos',sa.Column('repo_id', sa.String(50), primary_key=True))
    pass


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('known_repos')
    pass
