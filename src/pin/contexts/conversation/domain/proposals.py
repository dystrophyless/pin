"""Framework-independent vocabulary for provider proposal DTOs."""

from enum import StrEnum


class SourceLanguage(StrEnum):
    RU = "ru"
    EN = "en"


class InputKind(StrEnum):
    TEXT = "text"
    TRANSCRIPT = "transcript"


class ProposalOperation(StrEnum):
    CREATE_PLAN = "propose_create_plan"
    CHANGE_OCCURRENCE = "propose_change_occurrence"
    CHANGE_SERIES = "propose_change_series"
    COMPLETE = "propose_complete"
    CANCEL = "propose_cancel"


class FailureCategory(StrEnum):
    INVALID_INPUT = "invalid_input"
    ACCESS_DENIED = "access_denied"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TIMEOUT = "timeout"
    TRANSPORT = "transport_error"
    PROVIDER = "provider_error"
    INVALID_OUTPUT = "invalid_output"
    USAGE_UNKNOWN = "usage_unknown"
    STALE_JOB = "stale_job"
