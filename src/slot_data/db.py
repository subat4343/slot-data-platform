from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from slot_data.config import get_settings


def make_engine(url: str | None = None) -> Engine:
    # セッションのタイムゾーンを UTC に固定し、取得値が常に UTC の aware datetime になるようにする
    return create_engine(
        url or get_settings().database_url,
        pool_pre_ping=True,
        future=True,
        connect_args={"options": "-c timezone=UTC"},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
