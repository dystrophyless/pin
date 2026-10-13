"""One short database transaction for a single application command."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SqlAlchemyUnitOfWork:
    """Own one fresh session; commit on success and roll back on failure."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        if self._session is not None:
            raise RuntimeError("unit of work is already active")
        session = self._session_factory()
        try:
            await session.begin()
        except BaseException:
            await session.close()
            raise
        self._session = session
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        session = self._session
        if session is None:
            raise RuntimeError("unit of work is not active")
        try:
            if exception_type is None:
                await session.commit()
            else:
                await session.rollback()
        except BaseException:
            await session.rollback()
            raise
        finally:
            self._session = None
            await session.close()
        return False
