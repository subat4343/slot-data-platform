"""原本ストレージ。

内容アドレス方式（SHA-256）で gzip 圧縮して保存する。同一内容は 1 ファイルにまとまり（重複排除）、
一度書いたファイルは上書きしない（不変）。DB には ``rawstore://sha256/ab/cd/<sha>.gz`` 形式の URI を記録し、
ストアのルートは設定で差し替えられる（ローカル FS 以外のオブジェクトストレージは RawStore を実装して追加）。
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

URI_SCHEME = "rawstore://"


class RawStoreError(Exception):
    pass


class RawIntegrityError(RawStoreError):
    pass


@dataclass(frozen=True)
class StoredObject:
    uri: str
    sha256: str
    byte_size: int
    created: bool  # 新規に書き込んだか（False は既存の同一内容を再利用）


class RawStore(Protocol):
    def put(self, data: bytes) -> StoredObject: ...

    def get(self, uri: str) -> bytes: ...

    def exists(self, uri: str) -> bool: ...

    def verify(self, uri: str, sha256: str) -> bool: ...

    def iter_uris(self) -> Iterator[str]: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalRawStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def uri_for(self, digest: str) -> str:
        return f"{URI_SCHEME}sha256/{digest[:2]}/{digest[2:4]}/{digest}.gz"

    def path_for(self, uri: str) -> Path:
        if not uri.startswith(URI_SCHEME):
            raise RawStoreError(f"unsupported uri: {uri}")
        relative = uri[len(URI_SCHEME) :]
        if ".." in Path(relative).parts:
            raise RawStoreError(f"invalid uri: {uri}")
        return self.root / relative

    def put(self, data: bytes) -> StoredObject:
        digest = sha256_hex(data)
        uri = self.uri_for(digest)
        path = self.path_for(uri)
        if path.exists():
            # 既存ファイルは上書きしない。破損していれば例外にして呼び出し側で正規化を止める
            if not self.verify(uri, digest):
                raise RawIntegrityError(f"existing raw object is corrupted: {uri}")
            return StoredObject(uri, digest, len(data), created=False)

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".gz")
        try:
            with os.fdopen(fd, "wb") as raw_file, gzip.GzipFile(fileobj=raw_file, mode="wb", mtime=0) as gz:
                gz.write(data)
            with open(tmp_name, "rb") as check:
                if sha256_hex(gzip.decompress(check.read())) != digest:
                    raise RawIntegrityError("written raw object failed verification")
            try:
                if path.exists():  # 並行書き込みで先に同一内容が置かれた
                    os.remove(tmp_name)
                else:
                    os.replace(tmp_name, path)
            except PermissionError:
                # Windows では読み取り専用の既存ファイルへ置換できない。先行した書き込みを採用する
                if not path.exists():
                    raise
                os.remove(tmp_name)
            if not self.verify(uri, digest):
                raise RawIntegrityError(f"raw object failed verification after write: {uri}")
        except BaseException:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise
        with contextlib.suppress(OSError):
            os.chmod(path, 0o444)
        return StoredObject(uri, digest, len(data), created=True)

    def get(self, uri: str) -> bytes:
        path = self.path_for(uri)
        if not path.exists():
            raise RawStoreError(f"raw object not found: {uri}")
        with gzip.open(path, "rb") as gz:
            return gz.read()

    def exists(self, uri: str) -> bool:
        return self.path_for(uri).exists()

    def verify(self, uri: str, sha256: str) -> bool:
        try:
            return sha256_hex(self.get(uri)) == sha256
        except (RawStoreError, OSError, EOFError, gzip.BadGzipFile):
            return False

    def iter_uris(self) -> Iterator[str]:
        base = self.root / "sha256"
        if not base.exists():
            return
        for path in sorted(base.rglob("*.gz")):
            if path.name.startswith(".tmp-"):
                continue
            yield URI_SCHEME + path.relative_to(self.root).as_posix()
