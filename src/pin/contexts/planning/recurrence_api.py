"""Published typed consumer contract for confirmed recurrence operations.

Preview producers call the pure preview functions, display their exact projected
dates and offsets, then issue `RecurrenceConfirmationProof` against the returned
projection digest. Command handlers inject only an approved verifier; no boolean
or permissive confirmation adapter is provided here.
"""

from pin.contexts.planning.application.recurrence_contracts import (
    RecurrenceClock,
    RecurrenceCommandStore,
    RecurrenceConfirmationGrant,
    RecurrenceConfirmationProof,
    RecurrenceConfirmationVerifier,
    RecurrenceGeneratorStore,
    SeriesOccurrenceSnapshot,
    SeriesSnapshot,
)
from pin.contexts.planning.application.recurrence_controls import (
    StopRecurrenceSeries,
    StopSeriesPreview,
    preview_stop_series,
    stop_confirmed_series,
)
from pin.contexts.planning.application.recurrence_exceptions import (
    ConfirmedRecurrenceException,
    RecurrenceExceptionPreview,
    apply_confirmed_recurrence_exception,
    preview_recurrence_exception,
)
from pin.contexts.planning.application.recurrence_generation import (
    GenerateRecurrenceSeries,
    generate_recurrence_horizon,
)
from pin.contexts.planning.application.recurrence_preview import (
    CreateConfirmedSeries,
    RecurrenceAccessDenied,
    RecurrenceConfirmationRejected,
    RecurrenceIdempotencyConflict,
    RecurrencePreview,
    RecurrenceStalePreview,
    create_confirmed_series,
    preview_recurring_series,
)
from pin.contexts.planning.domain.recurrence import (
    RecurrenceExceptionDraft,
    RecurrenceExceptionScope,
    RecurrenceFrequency,
    RecurrenceOccurrence,
    RecurrenceProjection,
    RecurrenceRule,
    RecurringPlanDraft,
    project_recurrence,
    recurrence_date,
    resolve_recurring_wall_time,
)
from pin.contexts.planning.infrastructure.series_store import (
    SeriesNotFound,
    SeriesStopped,
    SeriesVersionConflict,
    SqlAlchemySeriesStore,
)

__all__ = [
    "ConfirmedRecurrenceException",
    "CreateConfirmedSeries",
    "GenerateRecurrenceSeries",
    "RecurrenceAccessDenied",
    "RecurrenceClock",
    "RecurrenceCommandStore",
    "RecurrenceConfirmationGrant",
    "RecurrenceConfirmationProof",
    "RecurrenceConfirmationRejected",
    "RecurrenceConfirmationVerifier",
    "RecurrenceExceptionDraft",
    "RecurrenceExceptionPreview",
    "RecurrenceExceptionScope",
    "RecurrenceFrequency",
    "RecurrenceGeneratorStore",
    "RecurrenceIdempotencyConflict",
    "RecurrenceOccurrence",
    "RecurrencePreview",
    "RecurrenceProjection",
    "RecurrenceRule",
    "RecurrenceStalePreview",
    "RecurringPlanDraft",
    "SeriesNotFound",
    "SeriesOccurrenceSnapshot",
    "SeriesSnapshot",
    "SeriesStopped",
    "SeriesVersionConflict",
    "SqlAlchemySeriesStore",
    "StopRecurrenceSeries",
    "StopSeriesPreview",
    "apply_confirmed_recurrence_exception",
    "create_confirmed_series",
    "generate_recurrence_horizon",
    "preview_recurrence_exception",
    "preview_recurring_series",
    "preview_stop_series",
    "project_recurrence",
    "recurrence_date",
    "resolve_recurring_wall_time",
    "stop_confirmed_series",
]
