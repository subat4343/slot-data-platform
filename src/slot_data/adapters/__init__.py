from __future__ import annotations

from slot_data.adapters.base import SourceAdapter
from slot_data.config import SourceConfig

from .daidata_online import DaidataOnlineAdapter
from .pscube import PsCubeAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    PsCubeAdapter.source_code: PsCubeAdapter,
    DaidataOnlineAdapter.source_code: DaidataOnlineAdapter,
}


def register_adapter(adapter_cls: type[SourceAdapter]) -> None:
    ADAPTERS[adapter_cls.source_code] = adapter_cls


def get_adapter(config: SourceConfig) -> SourceAdapter:
    try:
        return ADAPTERS[config.code](config)
    except KeyError as exc:
        raise KeyError(f"no adapter registered for source {config.code}") from exc
