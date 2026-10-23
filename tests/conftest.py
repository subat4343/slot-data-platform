from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import time
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TEST_DB_URL = os.environ.get(
    "SLOT_DATA_TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:55432/slot_data_test"
)


def _db_available(url: str) -> bool:
    try:
        engine = create_engine(url)
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    if not _db_available(TEST_DB_URL):
        pytest.skip(f"PostgreSQL not available at {TEST_DB_URL}")
    cfg = alembic_config(TEST_DB_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    from slot_data.db import make_engine

    eng = make_engine(TEST_DB_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalar_one()
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def raw_store(tmp_path: Path):
    from slot_data.storage.raw_store import LocalRawStore

    return LocalRawStore(tmp_path / "rawstore")


@pytest.fixture
def ctx(session_factory, raw_store):
    """サービス層の実行コンテキスト（DB セッションファクトリ + 原本ストア + 設定）。"""
    from slot_data.config import AppConfig, SourceConfig
    from slot_data.services import AppContext

    config = AppConfig(
        sources=[
            SourceConfig(code="pscube", name="P's CUBE", priority=10),
            SourceConfig(code="daidata_online", name="台データオンライン", priority=20),
            SourceConfig(code="manual", name="手動入力", priority=50),
            SourceConfig(code="fixture", name="テスト用", priority=90),
        ]
    )
    return AppContext(session_factory=session_factory, raw_store=raw_store, config=config)


@pytest.fixture
def hall(ctx):
    from slot_data.services import masters

    with ctx.session() as s:
        h = masters.create_hall(
            s, code="H001", name="テスト店", business_day_cutoff=time(5, 0), closing_time=time(22, 45)
        )
        for code in ("pscube", "daidata_online", "manual", "fixture"):
            masters.ensure_source(s, code, code)
        s.flush()
        return h
