"""設定: 秘密情報・接続先は環境変数、運用パラメータは YAML（config/app.yaml）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RateLimitConfig(BaseModel):
    min_interval_seconds: float = 5.0
    max_concurrency: int = 1


class RetryConfig(BaseModel):
    max_attempts: int = 3
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0
    timeout_seconds: float = 30.0


class AccessPolicy(BaseModel):
    """自動取得の可否。利用規約・robots を確認した結果を記録し、prohibited の間はアダプターが起動しない。"""

    automated_fetch: Literal["prohibited", "permitted", "unverified"] = "unverified"
    reviewed_at: str | None = None
    evidence: list[str] = Field(default_factory=list)
    # 運営者から書面許可を得た場合の参照（契約番号・メール等）。permitted にする場合は必須
    permission_reference: str | None = None
    respect_robots_txt: bool = True


class SourceConfig(BaseModel):
    code: str
    name: str
    priority: int = 100  # 小さいほど優先
    enabled: bool = True
    access_policy: AccessPolicy = Field(default_factory=AccessPolicy)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    user_agent: str = "slot-data-collector/0.1 (+contact: set-in-config)"
    options: dict[str, Any] = Field(default_factory=dict)


class CollectTarget(BaseModel):
    source: str
    hall_code: str
    source_hall_id: str
    machines: list[str] | None = None


class SelectionConfig(BaseModel):
    # final_candidate の値が同一取得元で settle_hours 以上変化しなければ final に昇格する
    settle_hours: float = 24.0


class QualityConfig(BaseModel):
    # metric 名 → 警告とする絶対値上限（domain.metrics の既定値を上書き）
    warn_abs_limits: dict[str, int] = Field(default_factory=dict)


class AppConfig(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)
    targets: list[CollectTarget] = Field(default_factory=list)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    schedule_interval_minutes: int = 60

    def source(self, code: str) -> SourceConfig | None:
        return next((s for s in self.sources if s.code == code), None)

    def source_priority(self) -> dict[str, int]:
        return {s.code: s.priority for s in self.sources}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SLOT_DATA_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres@localhost:55432/slot_data"
    raw_store_root: Path = Path("var/rawstore")
    config_path: Path = Path("config/app.yaml")
    log_level: str = "INFO"
    log_json: bool = True
    pg_bin_dir: Path | None = None  # pg_dump / pg_restore の場所（PATH に無い場合）


def load_app_config(path: Path) -> AppConfig:
    if not path.exists():
        example = path.with_name("app.example.yaml")
        if example.exists():
            path = example
        else:
            return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources_dir = path.parent / "sources"
    sources = list(data.get("sources", []))
    if sources_dir.is_dir():
        known = {s.get("code") for s in sources}
        for file in sorted(sources_dir.glob("*.yaml")):
            item = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
            if item.get("code") not in known:
                sources.append(item)
    data["sources"] = sources
    return AppConfig.model_validate(data)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_app_config() -> AppConfig:
    return load_app_config(get_settings().config_path)
