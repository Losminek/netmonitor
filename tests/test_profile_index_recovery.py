"""L49: poškozený profile-index.json – obnova, oprava metadat bez ztráty snapshotů a bez úniku obsahu do logu."""
import json
import logging
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from netmonitor.core import profiles
from netmonitor.core.config import AppConfig, PingTarget
from netmonitor.core.profiles import ProfileStore
from netmonitor.gui.sync_service import SyncService


def config(host="192.0.2.1"):
    cfg = AppConfig.from_dict({"network": {"external_api": False}})
    cfg.ping.targets = [PingTarget("Test", host)]
    return cfg


def write_index(store, data):
    store.index_path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("bad", [
    None, "soukromy-obsah", 5, {}, {"name": "soukromy-obsah"},
    {"id": 5, "name": "soukromy-obsah"},
    {"id": "../soukromy-obsah", "name": "Profil"},
], ids=["null", "string", "number", "empty", "missing-id", "numeric-id", "unsafe-id"])
def test_invalid_entries_are_skipped(tmp_path, caplog, bad):
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(config())
    good = store.record(store.active_id())
    store.bind_network(good["id"], "wifi:test", "Testovací síť")
    good = store.record(good["id"])
    write_index(store, {"profiles": [bad, good], "active": good["id"]})
    with caplog.at_level(logging.WARNING, logger="netmonitor.core.profiles"):
        store.initialize(config())
        assert store.list_profiles() == [good]
        assert store.active_id() == good["id"]
        assert store.load(good["id"]).ping.targets[0].host == "192.0.2.1"
        assert store.profile_for_network("wifi:test") == good["id"]
    assert caplog.records
    assert "soukromy-obsah" not in caplog.text
    assert all(not r.args and not r.exc_info for r in caplog.records)
    assert not list(store.root.glob("profile-index.json.bad-*"))
    assert ProfileStore(store.root).list_profiles() == [good]


@pytest.mark.parametrize("active", [3, None, [], {}, "neznamy-profil"])
def test_invalid_active_preserves_entries_and_recovers_current(tmp_path, caplog, active):
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(config())
    good = store.record(store.active_id())
    write_index(store, {"profiles": [good], "active": active})
    store.initialize(config())
    assert store.active_id() != good["id"]
    assert good in store.list_profiles()
    assert store.record(store.active_id())["name"] == "Obnovené aktuální nastavení"
    assert caplog.records


@pytest.mark.parametrize("raw", [
    b'{"profiles": "x"}', b'{"profiles": {}}', b'{"profiles": []}',
    b'{"profiles": [{"name": 5}]}', b'[]', b'null', b'5',
    b'{"soukromy-obsah":', b'\xff',
])
def test_unusable_index_is_backed_up_and_snapshots_recovered(tmp_path, caplog, raw):
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(config())
    first = store.active_id()
    second = store.create("Druhý", config("198.51.100.1"))
    snapshots = {pid: store._file(pid).read_bytes() for pid in (first, second)}
    store.index_path.write_bytes(raw)
    store.initialize(config("192.0.2.2"))
    backups = list(store.root.glob("profile-index.json.bad-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == raw
    records = store.list_profiles()
    assert {p["id"] for p in records} == {first, second, store.active_id()}
    assert store.active_id() not in {first, second}
    assert store.load(store.active_id()).ping.targets[0].host == "192.0.2.2"
    assert len({p["name"] for p in records}) == 3
    # Ztracená vazba na účet se nesmí změnit v automatické nahrání do jiného účtu.
    assert all(p.get("local_only") for p in records)
    for pid, content in snapshots.items():
        assert store._file(pid).read_bytes() == content
        assert store.load(pid).ping.targets
    assert caplog.records and "soukromy-obsah" not in caplog.text
    assert all(not r.exc_info for r in caplog.records)
    store.initialize(config())
    assert store.list_profiles() == records
    assert len(list(store.root.glob("profile-index.json.bad-*"))) == 1


def test_recovery_skips_unreadable_snapshots_and_preserves_them(tmp_path, caplog):
    store = ProfileStore(tmp_path / "profiles")
    store.root.mkdir()
    damaged = {str(uuid4()): b'{', str(uuid4()): b'[]', str(uuid4()): b'\xff'}
    for pid, raw in damaged.items():
        store._file(pid).write_bytes(raw)
    (store.root / "other.json").write_text("{}", encoding="utf-8")
    store.index_path.write_text('{"profiles": "x"}', encoding="utf-8")
    store.initialize(config())
    assert store.list_profiles() == [{"id": store.active_id(), "name": "Obnovené aktuální nastavení",
                                      "local_only": True, "recovered": True}]
    assert store.load(store.active_id()).ping.targets[0].host == "192.0.2.1"
    for pid, raw in damaged.items():
        assert store._file(pid).read_bytes() == raw
    assert caplog.records


def test_duplicate_entry_is_skipped_and_remote_metadata_preserved(tmp_path, caplog):
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(config())
    pid, remote = store.active_id(), str(uuid4())
    store.bind_remote(pid, remote, 2, "test@example.com", "https://example.com", "hash")
    good = store.record(pid)
    pending = [{"remote_id": str(uuid4()), "revision": 1,
                "account": "test@example.com", "server": "https://example.com"}]
    write_index(store, {"profiles": [good, good], "active": pid, "pending_deletes": pending})
    store.initialize(config())
    assert store.list_profiles() == [good]
    assert store.pending_deletes("test@example.com", "https://example.com") == [(pending[0]["remote_id"], 1)]
    assert caplog.records


@pytest.mark.parametrize("bad", [None, {}, "soukromy-obsah", [None, {}, {"remote_id": 3}]])
def test_invalid_pending_deletes_are_skipped(tmp_path, caplog, bad):
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(config())
    good = store.record(store.active_id())
    write_index(store, {"profiles": [good], "active": good["id"], "pending_deletes": bad})
    store.initialize(config())
    assert store.pending_deletes("test@example.com", "https://example.com") == []
    assert store.list_profiles() == [good]
    assert caplog.records and "soukromy-obsah" not in caplog.text


def test_failed_backup_does_not_overwrite_index(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profiles")
    store.root.mkdir()
    raw = b'{"profiles": "x"}'
    store.index_path.write_bytes(raw)

    def fail(*args):
        raise OSError("Nelze vytvořit zálohu")

    monkeypatch.setattr(type(store.index_path), "replace", fail)
    with pytest.raises(OSError):
        store.initialize(config())
    assert store.index_path.read_bytes() == raw
    assert list(store.root.iterdir()) == [store.index_path]


@pytest.mark.parametrize("damage", ["unreadable", "invalid-active", "dropped-active"])
def test_recovery_autosave_never_overwrites_existing_snapshot(tmp_path, damage):
    store = ProfileStore(tmp_path)
    store.initialize(config())
    first = store.active_id()
    second = store.create("Druhý", config("198.51.100.1"))
    snapshots = {pid: store._file(pid).read_bytes() for pid in (first, second)}
    data = json.loads(store.index_path.read_text(encoding="utf-8"))
    if damage == "unreadable":
        store.index_path.write_text("{", encoding="utf-8")
    else:
        data["active"] = 3 if damage == "invalid-active" else str(uuid4())
        if damage == "dropped-active":
            data["profiles"].append({"id": data["active"] + "/bad", "name": "Vadný"})
            data["active"] += "/bad"
        write_index(store, data)
    current = config("192.0.2.99")
    store.initialize(current)
    store.save_active(current)  # stejná cesta jako Controller._save_now
    for pid, raw in snapshots.items():
        assert store._file(pid).read_bytes() == raw
    active = store.active_id()
    assert active not in snapshots
    assert store.record(active)["name"] == "Obnovené aktuální nastavení"
    assert store.record(active)["local_only"] is True
    assert store.load(active).ping.targets[0].host == "192.0.2.99"
    records = store.list_profiles()
    store.initialize(current)
    assert store.list_profiles() == records


def test_read_without_current_refuses_invalid_active(tmp_path):
    store = ProfileStore(tmp_path)
    store.initialize(config())
    data = json.loads(store.index_path.read_text(encoding="utf-8"))
    data["active"] = 3
    write_index(store, data)
    raw = store.index_path.read_bytes()
    snapshot = store._file(data["profiles"][0]["id"]).read_bytes()
    with pytest.raises(ValueError):
        store.save_active(config("192.0.2.99"))
    assert store.index_path.read_bytes() == raw
    assert store._file(data["profiles"][0]["id"]).read_bytes() == snapshot


@pytest.mark.parametrize("field,bad", [
    ("networks", {}), ("networks", [None]), ("networks", [{"key": 5, "label": "Síť"}]),
    ("networks", [{"key": "wifi:test", "label": 5}]),
    ("remote_id", 5), ("remote_revision", "soukromy-obsah"),
    ("remote_revision", True), ("remote_account", []), ("remote_server", {}),
    ("synced_hash", []), ("local_only", "ano"),
])
def test_bad_secondary_metadata_preserves_profile(tmp_path, caplog, field, bad):
    store = ProfileStore(tmp_path)
    store.initialize(config())
    pid = store.active_id()
    store.bind_remote(pid, str(uuid4()), 2, "test@example.com", "https://example.com", "hash")
    rec = store.record(pid)
    rec[field] = bad
    write_index(store, {"profiles": [rec], "active": pid})
    raw = store._file(pid).read_bytes()
    with caplog.at_level(logging.WARNING):
        store.initialize(config("192.0.2.99"))
    repaired = store.record(pid)
    assert store.active_id() == pid
    assert repaired["local_only"] is True
    assert repaired["recovered"] is True
    assert not any(k.startswith("remote_") or k == "synced_hash" for k in repaired)
    if field == "networks":
        assert "networks" not in repaired
    assert store._file(pid).read_bytes() == raw
    assert caplog.records and "soukromy-obsah" not in caplog.text
    assert all(not r.args and not r.exc_info for r in caplog.records)


@pytest.mark.parametrize("name", [None, 5, "", " ", "x" * 101, "soukromý"])
def test_invalid_or_duplicate_name_is_replaced(tmp_path, name):
    store = ProfileStore(tmp_path)
    store.initialize(config())
    active = store.active_id()
    other = store.create("Druhý", config("198.51.100.1"))
    data = json.loads(store.index_path.read_text(encoding="utf-8"))
    data["profiles"][1]["name"] = name
    write_index(store, data)
    raw = store._file(other).read_bytes()
    store.initialize(config())
    records = store.list_profiles()
    assert {p["id"] for p in records} == {active, other}
    names = [p["name"].casefold() for p in records]
    assert len(set(names)) == len(names)
    assert all(isinstance(p["name"], str) and 0 < len(p["name"]) <= 100 for p in records)
    assert store._file(other).read_bytes() == raw


def test_read_only_index_still_lists_repaired_profiles(tmp_path, monkeypatch, caplog):
    store = ProfileStore(tmp_path)
    store.initialize(config())
    good = store.record(store.active_id())
    write_index(store, {"profiles": [good, {"name": "soukromy-obsah"}], "active": good["id"]})

    def denied(*args):
        raise OSError("soukromy-obsah")

    monkeypatch.setattr(profiles, "atomic_write_json", denied)
    with caplog.at_level(logging.WARNING):
        assert store.list_profiles() == [good]
    assert caplog.records and "soukromy-obsah" not in caplog.text
    assert all(not r.args and not r.exc_info for r in caplog.records)


@pytest.mark.parametrize("recovered", [True, False])
def test_local_only_status_distinguishes_recovery(recovered):
    service = SimpleNamespace(logged_in=True)
    record = {"local_only": True, "recovered": recovered}
    text = SyncService.profile_state(cast(SyncService, service), record)
    assert text == ("jen místní (obnovený profil)" if recovered else "jen místní (na serveru smazán)")


def test_reupload_clears_recovery_reason_before_later_server_deletion(tmp_path):
    store = ProfileStore(tmp_path)
    store.initialize(config())
    store.index_path.write_text("{", encoding="utf-8")
    store.initialize(config())
    pid = store.active_id()
    assert store.record(pid).get("recovered") is True
    store.bind_remote(pid, str(uuid4()), 1, "test@example.com", "https://example.com")
    assert "recovered" not in store.record(pid)
    store.unbind(pid, local_only=True)
    assert SyncService.profile_state(cast(SyncService, SimpleNamespace(logged_in=True)), store.record(pid)) == "jen místní (na serveru smazán)"


@pytest.mark.parametrize("name", [None, 5, "", " ", "x" * 101, "Straße", "STRASSE"])
def test_name_only_repair_preserves_server_binding(tmp_path, name):
    store = ProfileStore(tmp_path)
    store.initialize(config())
    store.rename(store.active_id(), "Straße")
    store.create("Profil", config())
    pid = store.create("Druhý", config("198.51.100.1"))
    store.bind_remote(pid, str(uuid4()), 3, "test@example.com", "https://example.com", "hash")
    store.bind_network(pid, "wifi:test", "Testovací síť")
    original = store.record(pid)
    expected_name = store.unique_name(name if isinstance(name, str) else "Profil", skip_id=pid)
    data = json.loads(store.index_path.read_text(encoding="utf-8"))
    next(p for p in data["profiles"] if p["id"] == pid)["name"] = name
    write_index(store, data)
    snapshot = store._file(pid).read_bytes()

    store.initialize(config())

    expected = dict(original, name=expected_name)
    assert store.record(pid) == expected
    assert ProfileStore(tmp_path).record(pid) == expected
    assert store._file(pid).read_bytes() == snapshot


@pytest.mark.parametrize("local_only", [True, False])
def test_unbind_clears_recovered_flag(tmp_path, local_only):
    store = ProfileStore(tmp_path)
    store.initialize(config())
    pid = store.active_id()
    store.bind_remote(pid, str(uuid4()), 3, "test@example.com", "https://example.com", "hash")
    data = json.loads(store.index_path.read_text(encoding="utf-8"))
    data["profiles"][0].update(local_only=True, recovered=True)
    write_index(store, data)

    store.unbind(pid, local_only=local_only)

    expected: dict[str, str | bool] = {"id": pid, "name": "Soukromý"}
    if local_only:
        expected["local_only"] = True
    record = ProfileStore(tmp_path).record(pid)
    assert record == expected
