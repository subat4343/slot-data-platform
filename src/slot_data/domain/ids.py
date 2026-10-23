"""UUIDv7 生成（RFC 9562）。

Python 3.14 未満の標準ライブラリと PostgreSQL 16 には uuidv7 生成関数が無いため、
アプリ側で生成する（ADR-0003）。生成値は標準 UUID 型なので DB 側は UUID 列のまま扱える。
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def uuid7() -> uuid.UUID:
    """時刻順に単調増加する UUIDv7 を返す。

    同一ミリ秒内では 12bit の rand_a をカウンタとして使い、プロセス内の単調性を保証する
    （RFC 9562 6.2 Method 1）。カウンタ溢れ時は論理時刻を 1ms 進める。
    """
    global _last_ms, _counter
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms > _last_ms:
            _last_ms = ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x07FF  # 上位ビットを空けて溢れにくくする
        else:
            _counter += 1
            if _counter > 0x0FFF:
                _last_ms += 1
                _counter = 0
            ms = _last_ms
        counter = _counter

    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= counter << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    return value.int >> 80
