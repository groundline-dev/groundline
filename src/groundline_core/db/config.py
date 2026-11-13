"""Database settings shared by setup and runtime connections."""

# SQLAlchemy 2.1: both URL forms use psycopg 3.
DRIVERS = frozenset({"postgresql", "postgresql+psycopg"})
