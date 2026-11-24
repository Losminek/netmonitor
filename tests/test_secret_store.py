import errno
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from netmonitor.core import secret_store
from netmonitor.core.secret_store import SecretStore


def test_roundtrip_and_clear(tmp_path):
    store = SecretStore(tmp_path / "s.json")
    assert store.load() is None
    store.save("https://sync.zelina.eu", "a@example.org", "tajny-token-ěšč")
    assert store.load() == ("https://sync.zelina.eu", "a@example.org", "tajny-token-ěšč")
    store.clear()
    store.clear()
    assert store.load() is None


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    assert SecretStore(path).load() is None
    path.write_text(json.dumps({"scheme": "file", "token": 5, "server": "x", "email": "y"}), encoding="utf-8")
    assert SecretStore(path).load() is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX práva souboru")
def test_file_is_private_on_posix(tmp_path):
    store = SecretStore(tmp_path / "s.json")
    store.save("https://sync.zelina.eu", "a@example.org", "token")
    assert os.stat(store.path).st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX práva souboru")
def test_temporary_file_is_private_during_write(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "sync-session.json")
    # Starý pevný soubor s širšími právy se odstraní, nový vzniká soukromý.
    stale = store.path.with_suffix(".tmp")
    stale.write_text("pozůstatek", encoding="utf-8")
    stale.chmod(0o644)
    real_dump = json.dump
    modes = []

    def checked_dump(data, stream, **kwargs):
        modes.append(os.fstat(stream.fileno()).st_mode & 0o777)
        assert modes[-1] == 0o600
        return real_dump(data, stream, **kwargs)

    monkeypatch.setattr(json, "dump", checked_dump)
    store.save("https://example.com", "a@example.com", "test-token")

    assert modes == [0o600]
    assert not stale.exists()
    assert set(tmp_path.iterdir()) == {store.path}
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("has_session", [True, False])
def test_clear_removes_legacy_temporary_file(tmp_path, has_session):
    store = SecretStore(tmp_path / "sync-session.json")
    if has_session:
        store.save("https://example.com", "a@example.com", "test-token")
    stale = store.path.with_suffix(".tmp")
    stale.write_text("test-old-token", encoding="utf-8")

    store.clear()
    store.clear()

    assert not store.path.exists()
    assert not stale.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["permission", "directory"])
def test_save_continues_when_legacy_temporary_cleanup_fails(tmp_path, monkeypatch, caplog, failure):
    store = SecretStore(tmp_path / "sync-session.json")
    store.save("https://example.com", "a@example.com", "test-old-token")
    stale = store.path.with_suffix(".tmp")
    if failure == "directory":
        stale.mkdir()
    else:
        stale.write_text("test-old-token", encoding="utf-8")
        real_unlink = Path.unlink

        def denied_unlink(path, *args, **kwargs):
            if path == stale:
                raise PermissionError(errno.EACCES, "test-old-token", str(path))
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", denied_unlink)

    with caplog.at_level(logging.WARNING, logger=secret_store.__name__):
        store.save("https://example.com", "a@example.com", "test-new-token")

    assert store.load() == ("https://example.com", "a@example.com", "test-new-token")
    assert set(tmp_path.iterdir()) == {store.path, stale}
    assert len(caplog.records) == 1
    warning = caplog.records[0]
    assert warning.levelno == logging.WARNING
    assert warning.getMessage() == "Nepodařilo se odstranit pozůstatek přihlašovací relace."
    assert warning.exc_info is None
    assert warning.stack_info is None
    assert str(tmp_path) not in caplog.text
    assert "test-old-token" not in caplog.text
    assert "test-new-token" not in caplog.text


@pytest.mark.parametrize("failure", ["dump", "fsync", "replace"])
def test_failed_save_preserves_original_and_cleans_temporary_file(tmp_path, monkeypatch, failure):
    store = SecretStore(tmp_path / "sync-session.json")
    store.save("https://example.com", "a@example.com", "test-old-token")
    original = store.path.read_bytes()
    stale = store.path.with_suffix(".tmp")
    stale.write_text("test-old-token", encoding="utf-8")

    def fail(*args, **kwargs):
        if failure == "dump":
            args[1].write('{"scheme":')   # výjimka až po částečném zápisu
        raise OSError("Simulované selhání zápisu")

    if failure == "dump":
        monkeypatch.setattr(json, "dump", fail)
    else:
        monkeypatch.setattr(os, failure, fail)

    with pytest.raises(OSError, match="Simulované selhání zápisu"):
        store.save("https://example.com", "a@example.com", "test-new-token")

    assert store.path.read_bytes() == original
    # Kontrolujeme celý adresář, nejen příponu: pomocník používá .tmp-*.json.
    assert list(tmp_path.iterdir()) == [store.path]


def test_save_fsyncs_before_replace(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "sync-session.json")
    real_fsync, real_replace = os.fsync, os.replace
    calls = []

    def checked_fsync(fd):
        # Flush už musel dostat JSON do souboru před fsync.
        temporary = next(p for p in tmp_path.iterdir() if p != store.path)
        with temporary.open(encoding="utf-8") as stream:
            assert isinstance(json.load(stream), dict)
        calls.append("fsync")
        return real_fsync(fd)

    def checked_replace(src, dst):
        assert calls == ["fsync"]
        calls.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", checked_fsync)
    monkeypatch.setattr(os, "replace", checked_replace)
    store.save("https://example.com", "a@example.com", "test-token")
    assert calls == ["fsync", "replace"]


def test_load_reads_old_file_format(tmp_path):
    path = tmp_path / "sync-session.json"
    path.write_text('{"scheme": "file", "token": "test-old-token", '
                    '"server": "https://example.com", "email": "a@example.com"}', encoding="utf-8")
    assert SecretStore(path).load() == ("https://example.com", "a@example.com", "test-old-token")


def test_windows_save_and_load_use_dpapi(tmp_path, monkeypatch):
    calls = []

    def fake_dpapi(data, protect):
        calls.append(protect)
        return data[::-1]

    monkeypatch.setattr(secret_store, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(secret_store, "_dpapi", fake_dpapi)
    store = SecretStore(tmp_path / "sync-session.json")
    store.save("https://example.com", "a@example.com", "test-token")
    assert json.loads(store.path.read_text(encoding="utf-8"))["scheme"] == "dpapi"
    assert store.load() == ("https://example.com", "a@example.com", "test-token")
    assert calls == [True, False]


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI je jen ve Windows")
def test_token_is_encrypted_on_windows(tmp_path):
    store = SecretStore(tmp_path / "s.json")
    store.save("https://sync.zelina.eu", "a@example.org", "velmi-tajny-token")
    raw = store.path.read_text(encoding="utf-8")
    assert "velmi-tajny-token" not in raw and json.loads(raw)["scheme"] == "dpapi"
    assert store.load()[2] == "velmi-tajny-token"
