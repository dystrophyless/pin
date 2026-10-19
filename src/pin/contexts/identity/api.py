"""Published contracts for preference readers and onboarding consumers."""

from pin.contexts.identity.application.contracts import (
    HomeZoneChangeHandoff,
    IdentityStore,
    OnboardingDraft,
    PreferencesSnapshot,
    ProcessingAuthorization,
    ProcessingConsentSnapshot,
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
from pin.contexts.identity.application.processing_consent import (
    PROCESSING_CONSENT_POLICY_VERSION,
    ProcessingConsentAcceptance,
    ProcessingConsentConflict,
    accept_processing_consent,
    authorize_processing_consent,
    current_processing_authorization,
    get_processing_consent,
    invalidate_restored_processing_consent,
    list_granted_processing_consent_owners,
    revoke_processing_consent,
)
from pin.contexts.identity.application.review_roster import (
    PreferencesPage,
    ReviewProfilesReadStore,
)
from pin.contexts.identity.domain.preferences import Preferences, PreferenceVersionConflict
from pin.contexts.identity.infrastructure.review_roster import list_review_profiles
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore

__all__ = [
    "HomeZoneChangeHandoff",
    "IdentityStore",
    "SqlAlchemyIdentityStore",
    "OnboardingDraft",
    "OnboardingResult",
    "PreferenceVersionConflict",
    "Preferences",
    "PreferencesSnapshot",
    "ProcessingAuthorization",
    "ProcessingConsentSnapshot",
    "ProcessingConsentAcceptance",
    "ProcessingConsentConflict",
    "PROCESSING_CONSENT_POLICY_VERSION",
    "accept_processing_consent",
    "authorize_processing_consent",
    "current_processing_authorization",
    "get_processing_consent",
    "invalidate_restored_processing_consent",
    "list_granted_processing_consent_owners",
    "revoke_processing_consent",
    "PreferencesPage",
    "ReviewProfilesReadStore",
    "list_review_profiles",
    "TrialActivator",
    "answer_onboarding",
    "begin_onboarding",
    "begin_preferences_edit",
    "cancel_onboarding",
    "confirm_onboarding",
    "get_preferences",
]
