# ADR-0001 技術構成とディレクトリ構成

## 状況
仕様 §2・§11 で Python 3.12+、PostgreSQL 16+、SQLAlchemy 2、Alembic、Pydantic 2、pytest が指定されている。

## 決定
- 指定どおりの構成とする。開発機は Python 3.13.7、PostgreSQL 16.12 で検証した。
- CLI は Typer、HTTP は httpx、Excel は openpyxl、Parquet は pyarrow、祝日は jpholiday。
- ディレクトリは仕様 §11 に沿う。リポジトリのルートを `slot-data/` 相当とする。仕様に無い `services/`（実行コンテキスト・マスター操作）と `quality/`（課題・監査）を追加した。
- ログは標準 logging に JSON フォーマッタを付けて構造化する（依存を増やさない）。

## 影響
API や GUI を追加するときは `services/` と各ドメインモジュールを呼ぶ。CLI にロジックを置かない。
