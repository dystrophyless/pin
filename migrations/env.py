from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from pin.adapters.telegram.ingress import TelegramIngressBotRecord, TelegramOwnerRecord
from pin.adapters.telegram.presentation.tokens import TelegramCallbackTokenRecord
from pin.bootstrap.config import ProcessRole, load_settings
from pin.contexts.billing.infrastructure import models as billing_models  # noqa: F401
from pin.contexts.conversation.infrastructure import capture_models
from pin.contexts.conversation.infrastructure.models import (
    ConversationTurnResultRecord,
    ProviderCallRecord,
)
from pin.contexts.identity.infrastructure import models as identity_models  # noqa: F401
from pin.contexts.notifications.infrastructure import models as notifications_models
from pin.contexts.planning.infrastructure import models as planning_models  # noqa: F401
from pin.contexts.planning.infrastructure.agenda import AgendaSnapshotRecord
from pin.contexts.planning.infrastructure.series_models import (
    SeriesExceptionRecord,
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.persistence.models import Base
from pin.platform import models as platform_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
assert TelegramCallbackTokenRecord.metadata is target_metadata
assert TelegramIngressBotRecord.metadata is target_metadata
assert TelegramOwnerRecord.metadata is target_metadata
assert ConversationTurnResultRecord.metadata is target_metadata
assert ProviderCallRecord.metadata is target_metadata
assert capture_models.CaptureSequenceRecord.metadata is target_metadata
assert capture_models.CaptureDraftRecord.metadata is target_metadata
assert capture_models.CaptureInputRecord.metadata is target_metadata
assert capture_models.CaptureConfirmationRecord.metadata is target_metadata
assert identity_models.PreferencesRecord.metadata is target_metadata
assert identity_models.OnboardingRecord.metadata is target_metadata
assert planning_models.PlanItemRecord.metadata is target_metadata
assert planning_models.OccurrenceRecord.metadata is target_metadata
assert planning_models.ReminderRuleRecord.metadata is target_metadata
assert AgendaSnapshotRecord.metadata is target_metadata
assert SeriesRecord.metadata is target_metadata
assert SeriesOccurrenceRecord.metadata is target_metadata
assert SeriesExceptionRecord.metadata is target_metadata
assert notifications_models.ReminderIntentRecord.metadata is target_metadata
assert notifications_models.NotificationReconciliationReceiptRecord.metadata is target_metadata
assert notifications_models.DeliveryAttemptRecord.metadata is target_metadata


def _database_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return load_settings(ProcessRole.SPEECH_WORKER).database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    engine = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
