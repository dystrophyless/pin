"""Published contracts for preference readers and onboarding consumers."""

from pin.contexts.identity.application.contracts import (
    HomeZoneChangeHandoff,
    IdentityStore,
    OnboardingDraft,
    PreferencesSnapshot,
    TrialActivator,
)
from pin.contexts.identity.application.onboarding import (
    OnboardingResult,
    answer_onboarding,
    begin_onboarding,
    begin_preferences_edit,
    cancel_onboarding,
    confirm_onboarding,
    get_preferences,
)
from pin.contexts.identity.domain.preferences import PreferenceVersionConflict

__all__ = [
    "HomeZoneChangeHandoff",
    "IdentityStore",
    "OnboardingDraft",
    "OnboardingResult",
    "PreferenceVersionConflict",
    "PreferencesSnapshot",
    "TrialActivator",
    "answer_onboarding",
    "begin_onboarding",
    "begin_preferences_edit",
    "cancel_onboarding",
    "confirm_onboarding",
    "get_preferences",
]
