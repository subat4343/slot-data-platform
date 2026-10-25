"""allow negative bonus payout

P's CUBE の特賞履歴「出枚pt」はマイナスになり得る（2026-10-26 の収集で REG -7 を確認）ため、
bonus_events.payout_medals の 0 以上の制約を外す。

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26 07:44:13.765709

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f("ck_bonus_events_payout_non_negative"), "bonus_events", type_="check")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_check_constraint(
        op.f("ck_bonus_events_payout_non_negative"),
        "bonus_events",
        "payout_medals IS NULL OR payout_medals >= 0",
    )
