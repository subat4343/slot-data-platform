"""PostgreSQL と原本ストアの一貫バックアップ／リストア。

順序: (1) pg_dump（スナップショット）→ (2) 原本ファイルのコピー。
原本は DB 登録より先に書かれ、以後変更されないため、(1) の時点で参照されている原本は (2) で必ず揃う。
(2) の間に増えた原本は参照されない孤立ファイルになるだけで、監査（RAW_ORPHAN_FILE）で検出できる。
manifest.json に各原本の SHA-256 を記録し、リストア後に検証する。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from slot_data.storage.raw_store import LocalRawStore

DUMP_NAME = "database.dump"
MANIFEST_NAME = "manifest.json"
RAW_DIR = "rawstore"


class BackupError(RuntimeError):
    pass


def libpq_args(database_url: str) -> tuple[list[str], dict[str, str]]:
    """SQLAlchemy URL を pg_dump / pg_restore の引数と環境変数（パスワード）に変換する。"""
    url = make_url(database_url)
    args = []
    if url.host:
        args += ["-h", url.host]
    if url.port:
        args += ["-p", str(url.port)]
    if url.username:
        args += ["-U", url.username]
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = str(url.password)  # コマンドライン・ログに出さない
    return args, env


def _tool(name: str, bin_dir: Path | None) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    if bin_dir and (bin_dir / exe).exists():
        return str(bin_dir / exe)
    found = shutil.which(name)
    if not found:
        raise BackupError(f"{name} not found; set SLOT_DATA_PG_BIN_DIR")
    return found


@dataclass
class BackupResult:
    directory: Path
    raw_files: int
    dump_bytes: int


def backup(
    database_url: str, raw_store: LocalRawStore, out_dir: Path, bin_dir: Path | None = None
) -> BackupResult:
    out_dir.mkdir(parents=True, exist_ok=False)
    args, env = libpq_args(database_url)
    dbname = make_url(database_url).database
    dump_path = out_dir / DUMP_NAME
    proc = subprocess.run(
        [_tool("pg_dump", bin_dir), *args, "-Fc", "--no-owner", "-f", str(dump_path), dbname],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BackupError(f"pg_dump failed: {proc.stderr.strip()}")

    files: list[dict[str, Any]] = []
    for uri in raw_store.iter_uris():
        src = raw_store.path_for(uri)
        relative = src.relative_to(raw_store.root)
        dest = out_dir / RAW_DIR / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        files.append({"uri": uri, "sha256": src.stem, "size": dest.stat().st_size})
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "database": dbname,
        "dump": DUMP_NAME,
        "dump_bytes": dump_path.stat().st_size,
        "raw_files": files,
        "order": "pg_dump then raw copy",
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return BackupResult(out_dir, len(files), manifest["dump_bytes"])


def restore(
    backup_dir: Path, database_url: str, raw_store: LocalRawStore, bin_dir: Path | None = None
) -> dict[str, Any]:
    """空の DB に復元する（既存データの上書きはしない）。原本はストアに無いものだけコピーし、ハッシュを検証する。"""
    manifest = json.loads((backup_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    args, env = libpq_args(database_url)
    dbname = make_url(database_url).database
    proc = subprocess.run(
        [
            _tool("pg_restore", bin_dir),
            *args,
            "--no-owner",
            "--exit-on-error",
            "-d",
            dbname,
            str(backup_dir / manifest["dump"]),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BackupError(f"pg_restore failed: {proc.stderr.strip()}")
    restored, verified = 0, 0
    for item in manifest["raw_files"]:
        dest = raw_store.path_for(item["uri"])
        if not dest.exists():
            src = backup_dir / RAW_DIR / dest.relative_to(raw_store.root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            restored += 1
        if not raw_store.verify(item["uri"], item["sha256"]):
            raise BackupError(f"restored raw object failed verification: {item['uri']}")
        verified += 1
    return {"raw_restored": restored, "raw_verified": verified, "manifest_created_at": manifest["created_at"]}
