"""Connecting to the database, and logging when that fails."""

import logging

from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger("groundline.db")


async def check_connection(raw: str, *, timeout: int = 5) -> bool:
    """Try connecting; log and return False if it fails.

    The log gives where and why, never the password."""
    try:
        url = make_url(raw)
    except (SQLAlchemyError, ValueError):
        # The parser's message quotes the whole URL, password included.
        logger.error("can't connect to the database: DATABASE_URL isn't a valid URL")
        return False
    where = f"{url.host or 'localhost'}:{url.port or 5432}/{url.database}"
    try:
        engine = create_async_engine(url, connect_args={"connect_timeout": timeout})
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()
    except ImportError as error:
        # A URL such as postgresql+asyncpg:// names a driver that isn't installed.
        logger.error(
            "can't connect to the database at %s: the %s driver isn't installed; "
            "use postgresql+psycopg:// in DATABASE_URL",
            where,
            error.name or url.get_driver_name(),
        )
        return False
    except (SQLAlchemyError, OSError) as error:
        password = url.password if isinstance(url.password, str) else None
        logger.error(
            "can't connect to the database at %s: %s", where, _reason(error, password)
        )
        return False
    logger.info("connected to the database at %s", where)
    return True


def _reason(error: Exception, password: str | None) -> str:
    """The driver's own explanation, on one line, with the password removed."""
    inner = error.orig if isinstance(error, DBAPIError) else error
    reason = " ".join(str(inner).split())
    return reason.replace(password, "***") if password else reason
