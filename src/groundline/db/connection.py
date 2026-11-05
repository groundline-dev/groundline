"""Connecting to the database, and logging when that fails."""

import logging

from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from groundline.core.errors import SetupError
from groundline.db.config import DRIVERS

logger = logging.getLogger("groundline.db")


def make_engine(raw: str, *, timeout: int = 5) -> AsyncEngine:
    """A caller-owned engine for operational data, with safe error messages.

    Authentication must read the primary, with a fresh READ COMMITTED snapshot.
    Pooling reuses connections, not query results.
    """
    try:
        url = make_url(raw)
        if url.drivername not in DRIVERS:
            message = "DATABASE_URL must use postgresql+psycopg://"
            raise SetupError(message, message)
        return create_async_engine(
            url,
            hide_parameters=True,
            # Test each pooled connection before handing it out, so connections
            # broken by a Postgres restart are replaced instead of failing a request.
            pool_pre_ping=True,
            isolation_level="READ COMMITTED",
            connect_args={
                "connect_timeout": timeout,
                "target_session_attrs": "read-write",
            },
        )
    except (SQLAlchemyError, ValueError, ImportError):
        message = "DATABASE_URL is not a usable PostgreSQL URL"
        raise SetupError(message, message) from None


async def check_connection(
    raw: str,
    *,
    timeout: int = 5,
    engine: AsyncEngine | None = None,
    was_connected: bool | None = None,
) -> bool:
    """Run SELECT 1 against the database, log the result, and return whether it worked.

    Log lines name the host, port and database and the driver's reason for
    failing, but never the password.

    To avoid repeating the same line on every poll of /ready, pass the previous
    result as `was_connected`. The function then logs only when the database
    goes down or comes back.

    If you pass `engine`, the function uses it and leaves it open, and `timeout`
    has no effect. Otherwise it creates its own engine with `timeout` and closes
    it before returning.
    """
    try:
        url = make_url(raw)
    except (SQLAlchemyError, ValueError):
        # The parser's message quotes the whole URL, password included.
        if was_connected is not False:
            logger.error(
                "can't connect to the database: DATABASE_URL isn't a valid URL"
            )
        return False
    where = f"{url.host or 'localhost'}:{url.port or 5432}/{url.database}"
    owns_engine = engine is None
    try:
        if engine is None:
            engine = create_async_engine(url, connect_args={"connect_timeout": timeout})
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            if owns_engine:
                await engine.dispose()
    except ImportError as error:
        # A URL such as postgresql+asyncpg:// names a driver that isn't installed.
        if was_connected is False:
            return False
        logger.error(
            "can't connect to the database at %s: the %s driver isn't installed; "
            "use postgresql+psycopg:// in DATABASE_URL",
            where,
            error.name or url.get_driver_name(),
        )
        return False
    except (SQLAlchemyError, OSError) as error:
        if was_connected is not False:
            password = url.password if isinstance(url.password, str) else None
            logger.error(
                "can't connect to the database at %s: %s",
                where,
                _reason(error, password),
            )
        return False
    if was_connected is False:
        logger.info("the database at %s answers again", where)
    elif was_connected is None:
        logger.info("connected to the database at %s", where)
    return True


def scrub(message: str, *passwords: str | None) -> str:
    """`message` on one line, with each password in it replaced by ***.

    Passwords are replaced before the lines are joined, so one containing
    whitespace still matches."""
    for password in passwords:
        if password:
            message = message.replace(password, "***")
    return " ".join(message.split())


def _reason(error: Exception, password: str | None) -> str:
    """The driver's own explanation, on one line, with the password removed."""
    inner = error.orig if isinstance(error, DBAPIError) else error
    return scrub(str(inner), password)
