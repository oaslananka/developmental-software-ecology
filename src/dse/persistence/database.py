from sqlalchemy import Engine, create_engine

from dse.persistence.schema import metadata


def create_database_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def create_schema(engine: Engine) -> None:
    metadata.create_all(engine)


def drop_schema(engine: Engine) -> None:
    metadata.drop_all(engine)
