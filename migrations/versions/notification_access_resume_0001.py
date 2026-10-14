"""Persist generation on external Accountability attempts."""
import sqlalchemy as sa
from alembic import op

revision = "notification_access_resume_0001"
down_revision = "accountability_resume_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("accountability_delivery_attempts", sa.Column(
        "dispatch_version", sa.Integer(), nullable=False, server_default="1"),
        schema="notifications")
    op.create_check_constraint("ck_notifications_accountability_generation",
        "accountability_delivery_attempts", "dispatch_version >= 1", schema="notifications")
    op.alter_column("accountability_delivery_attempts", "dispatch_version",
        server_default=None, schema="notifications")


def downgrade():
    op.drop_constraint("ck_notifications_accountability_generation",
        "accountability_delivery_attempts", schema="notifications", type_="check")
    op.drop_column("accountability_delivery_attempts", "dispatch_version", schema="notifications")
