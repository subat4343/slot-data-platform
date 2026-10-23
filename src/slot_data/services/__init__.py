"""ドメインサービス（API/UI 非依存）。CLI や将来の API はここを呼ぶ。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from slot_data.config import AppConfig, Settings, get_app_config, get_settings
from slot_data.storage.raw_store import LocalRawStore, RawStore


@dataclass
class AppContext:
    session_factory: sessionmaker[Session]
    raw_store: RawStore
    config: AppConfig = field(default_factory=AppConfig)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


def build_context(settings: Settings | None = None, config: AppConfig | None = None) -> AppContext:
    from slot_data.db import make_engine, make_session_factory

    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    return AppContext(
        session_factory=make_session_factory(engine),
        raw_store=LocalRawStore(settings.raw_store_root),
        config=config or get_app_config(),
    )
