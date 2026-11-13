"""Choose persistence explicitly; never fall back after an Azure SQL failure."""

from ..config import Settings
from ..db import create_database_engine, session_factory
from .repository import InMemoryAuditRepository
from .sql_repository import SqlAuditRepository


def build_repository(settings: Settings):
    if not settings.database_url:
        return InMemoryAuditRepository()
    return SqlAuditRepository(
        session_factory(create_database_engine(settings.database_url)),
        lease_seconds=settings.worker_lease_seconds,
        max_attempts=settings.worker_max_attempts,
        retry_seconds=settings.worker_retry_seconds,
    )
