"""unix times to datetimes

Revision ID: 922b636258bd
Revises: 75a758ee8e5a
Create Date: 2025-09-04 03:23:19.946928

"""
from typing import Sequence, Union

import sqlalchemy
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '922b636258bd'
down_revision: Union[str, Sequence[str], None] = '75a758ee8e5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("known_pull_requests", "created_at", type_=sqlalchemy.types.TIMESTAMP, postgresql_using='to_timestamp(created_at)')
    op.alter_column("known_pull_requests", "updated_at", type_=sqlalchemy.types.TIMESTAMP, postgresql_using='to_timestamp(updated_at)')
    op.alter_column("known_pull_requests", "closed_at", type_=sqlalchemy.types.TIMESTAMP, postgresql_using='to_timestamp(closed_at)')
    op.alter_column("known_pull_requests", "merged_at", type_=sqlalchemy.types.TIMESTAMP, postgresql_using='to_timestamp(merged_at)')
    pass


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("known_pull_requests", "created_at", type_=sqlalchemy.types.INTEGER, postgresql_using="date_part('epoch', created_at)")
    op.alter_column("known_pull_requests", "updated_at", type_=sqlalchemy.types.INTEGER, postgresql_using="date_part('epoch', updated_at)")
    op.alter_column("known_pull_requests", "closed_at", type_=sqlalchemy.types.INTEGER, postgresql_using="date_part('epoch', closed_at)")
    op.alter_column("known_pull_requests", "merged_at", type_=sqlalchemy.types.INTEGER, postgresql_using="date_part('epoch', merged_at)")
    pass
