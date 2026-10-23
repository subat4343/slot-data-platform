from __future__ import annotations

import random

import pytest

from slot_data.adapters.base import AccessProhibitedError, FetchRequest, ensure_allowed
from slot_data.config import AccessPolicy, SourceConfig, load_app_config
from slot_data.ingestion.http import PoliteHttpClient, RateLimiter
from slot_data.storage.raw_store import LocalRawStore, RawIntegrityError, RawStoreError
from tests.conftest import ROOT


def test_raw_store_dedup_and_immutability(tmp_path):
    store = LocalRawStore(tmp_path)
    a = store.put(b"hello")
    b = store.put(b"hello")
    assert a.created and not b.created and a.uri == b.uri
    assert store.get(a.uri) == b"hello"
    assert store.verify(a.uri, a.sha256)
    assert list(store.iter_uris()) == [a.uri]
    # 改ざんされたファイルは再利用せず例外
    path = store.path_for(a.uri)
    path.chmod(0o666)
    path.write_bytes(b"corrupted")
    with pytest.raises(RawIntegrityError):
        store.put(b"hello")


def test_raw_store_rejects_path_traversal(tmp_path):
    with pytest.raises(RawStoreError):
        LocalRawStore(tmp_path).path_for("rawstore://../../etc/passwd")


def test_access_gate():
    with pytest.raises(AccessProhibitedError, match="利用条件で禁止"):
        ensure_allowed("x", AccessPolicy(automated_fetch="prohibited", evidence=["ToS 5(1)"]))
    with pytest.raises(AccessProhibitedError, match="未確認"):
        ensure_allowed("x", AccessPolicy())
    ensure_allowed("x", AccessPolicy(automated_fetch="permitted", permission_reference="contract-123"))


def test_shipped_source_configs_record_access_policy():
    config = load_app_config(ROOT / "config" / "app.example.yaml")
    daidata = config.source("daidata_online").access_policy
    assert daidata.automated_fetch == "prohibited" and daidata.evidence
    # P's CUBE は運営会社の許可（ユーザー申告）を根拠付きで記録したうえで permitted
    pscube = config.source("pscube")
    assert pscube.access_policy.automated_fetch == "permitted"
    assert "アンジェロ" in pscube.access_policy.permission_reference
    assert pscube.rate_limit.min_interval_seconds >= 30
    assert pscube.options["headless"] is False
    assert config.source_priority()["pscube"] < config.source_priority()["pscube_list"]


def test_rate_limiter_spaces_requests():
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    limiter = RateLimiter(5.0, clock=lambda: clock[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert sleeps == [5.0, 5.0]


def test_backoff_is_exponential_and_capped():
    client = PoliteHttpClient(SourceConfig(code="x", name="x"), rng=random.Random(0))
    delays = [client.backoff_delay(n) for n in range(1, 10)]
    assert delays[0] <= 2.0 and delays[2] > delays[0]
    assert max(delays) <= client.config.retry.max_delay_seconds
    client.close()


def test_fingerprint_is_stable():
    a = FetchRequest(kind="k", url="https://x/a", params=(("b", "2"), ("a", "1")))
    b = FetchRequest(kind="k", url="https://x/a", params=(("a", "1"), ("b", "2")))
    assert a.fingerprint() == b.fingerprint()
