"""Application ports for context-owned retention work."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

type OwnerPurger[SessionT] = Callable[[SessionT, UUID, datetime], Awaitable[None]]
type ExpiredTextPurger[SessionT] = Callable[[SessionT, datetime], Awaitable[int]]
