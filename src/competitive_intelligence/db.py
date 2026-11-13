"""Lazy Azure SQL engine and transaction factory; never deploy or initialize schema."""

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker

from .models import Base


def create_database_engine(database_url: str) -> Engine:
    try:
        url = make_url(database_url)
    except Exception:
        raise ValueError("DATABASE_URL must be a valid SQLAlchemy Azure SQL URL") from None
    if url.drivername != "mssql+mssqlpython":
        raise ValueError("DATABASE_URL must use the confirmed mssql+mssqlpython dialect")
    query = {key.lower(): str(value).lower() for key, value in url.query.items()}
    if query.get("encrypt", "yes") not in ("yes", "true", "mandatory", "strict"):
        raise ValueError("Azure SQL encryption must be enabled")
    if query.get("trustservercertificate", "no") not in ("no", "false"):
        raise ValueError("Azure SQL certificate validation must remain enabled")
    # Explicit defaults keep transport behavior independent of driver defaults.
    tls_query = {key: value for key, value in url.query.items()
                 if key.lower() not in ("encrypt", "trustservercertificate")}
    tls_query.update(Encrypt=query.get("encrypt", "yes"),
                     TrustServerCertificate=query.get("trustservercertificate", "no"))
    url = url.set(query=tls_query)
    return create_engine(
        url, pool_pre_ping=True, pool_size=2, max_overflow=1, pool_timeout=10,
        hide_parameters=True, connect_args={"timeout": 10},
    )


def session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)


def check_schema(engine: Engine) -> None:
    """Read-only column compatibility check for audit and source-intake mappings."""
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        actual = {column["name"] for column in inspector.get_columns(table.name, schema="ci")}
        if set(table.columns.keys()) - actual:
            raise ValueError(f"Existing ci.{table.name} schema is incompatible")
