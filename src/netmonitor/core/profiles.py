"""Lokální pojmenované profily NetMonitoru.

Každý profil je úplný snapshot AppConfig. Aktivní konfigurace zůstává také
v původním config.json kvůli kompatibilitě se staršími verzemi aplikace.
Při přepnutí se ze snapshotu použijí jen sdílené části, aktuální místní volby zůstávají.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import AppConfig, atomic_write_json
from ..utils.paths import config_dir

INDEX = "profile-index.json"
log = logging.getLogger(__name__)


def _valid_uuid(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _valid_networks(networks) -> bool:
    return isinstance(networks, list) and all(
        isinstance(n, dict) and isinstance(n.get("key"), str) and bool(n["key"])
        and isinstance(n.get("label", ""), str) for n in networks
    )


def _valid_metadata(item) -> bool:
    if not _valid_networks(item.get("networks", [])):
        return False
    if "remote_id" in item and not _valid_uuid(item["remote_id"]):
        return False
    if "remote_revision" in item and (type(item["remote_revision"]) is not int or item["remote_revision"] < 1):
        return False
    if any(key in item and not isinstance(item[key], str)
           for key in ("remote_account", "remote_server", "synced_hash")):
        return False
    return all(key not in item or isinstance(item[key], bool) for key in ("local_only", "recovered"))


def _unique_name(name: str, taken: set[str]) -> str:
    name = name.strip()[:100] or "Profil"
    candidate, i = name, 2
    while candidate.casefold() in taken:
        suffix = f" ({i})"
        candidate = name[:100 - len(suffix)] + suffix
        i += 1
    return candidate


def _valid_delete(item) -> bool:
    return (isinstance(item, dict) and _valid_uuid(item.get("remote_id"))
            and (item.get("revision") is None or (type(item["revision"]) is int and item["revision"] > 0))
            and isinstance(item.get("account"), str) and isinstance(item.get("server"), str))


class ProfileStore:
    def __init__(self, root: Path | None = None):
        self.root = root or config_dir() / "profiles"
        self.index_path = self.root / INDEX

    def _read(self, current: AppConfig | None = None) -> dict:
        with self.index_path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Neplatný index profilů")
        entries = data.get("profiles", [])
        if not isinstance(entries, list) or not entries:
            raise ValueError("Neplatný seznam profilů")
        valid, ids, names = [], set(), set()
        for item in entries:
            if (not isinstance(item, dict) or not _valid_uuid(item.get("id"))
                    or uuid.UUID(item["id"]) in ids):
                log.warning("Přeskakuji neplatnou položku v indexu profilů")
                continue
            item = dict(item)
            name = item.get("name")
            bad_name = (not isinstance(name, str) or not name.strip() or len(name) > 100
                        or name.casefold() in names)
            if bad_name:
                log.warning("Opravuji neplatný název profilu v indexu")
                # Stejně jako při přejmenování zůstává platná vazba na server.
                item["name"] = _unique_name(name if isinstance(name, str) else "Profil", names)
            if not _valid_metadata(item):
                log.warning("Opravuji neplatná metadata profilu v indexu")
                if not _valid_networks(item.get("networks", [])):
                    item.pop("networks", None)
                # Neúplná vazba ani opravená metadata se nesmí sama nahrát na server.
                for key in list(item):
                    if key.startswith("remote_") or key == "synced_hash":
                        del item[key]
                item["local_only"] = True
                item["recovered"] = True
            valid.append(item)
            ids.add(uuid.UUID(item["id"]))
            names.add(item["name"].casefold())
        if not valid:
            raise ValueError("Index neobsahuje platné profily")
        index = dict(data, profiles=valid)
        invalid_active = not isinstance(data.get("active"), str) or data["active"] not in {p["id"] for p in valid}
        if invalid_active:
            # Bez běžící konfigurace nelze určit, který snapshot je bezpečné přepisovat.
            if current is None:
                raise ValueError("Neplatný aktivní profil; je nutná obnova s aktuálním nastavením")
            log.warning("Neplatný aktivní profil – ukládám aktuální nastavení do nového profilu")
            self._recover_current(index, current)
        if "pending_deletes" in data:
            pending = data["pending_deletes"]
            if not isinstance(pending, list):
                log.warning("Přeskakuji neplatný seznam čekajících smazání profilů")
                pending = []
            index["pending_deletes"] = []
            for item in pending:
                if _valid_delete(item):
                    index["pending_deletes"].append(item)
                else:
                    log.warning("Přeskakuji neplatnou položku čekajícího smazání profilu")
        if index != data:
            try:
                atomic_write_json(self.index_path, index)
            except OSError:
                if invalid_active:
                    raise  # nová aktivní vazba musí být trvale uložená před autosavem
                log.warning("Opravený index profilů nelze uložit; používám opravu v paměti")
        return index

    def _file(self, profile_id: str) -> Path:
        return self.root / f"{uuid.UUID(profile_id)}.json"

    def _recover_current(self, index: dict, current: AppConfig) -> None:
        """Běžící config.json nesmí při obnově přepsat žádný existující snapshot."""
        profile_id = str(uuid.uuid4())
        name = _unique_name("Obnovené aktuální nastavení", {p["name"].casefold() for p in index["profiles"]})
        atomic_write_json(self._file(profile_id), current.to_dict())
        index["profiles"].append({"id": profile_id, "name": name, "local_only": True, "recovered": True})
        index["active"] = profile_id

    def initialize(self, current: AppConfig) -> None:
        """Převede původní nastavení; poškozený index zálohuje a obnoví ze snapshotů."""
        if self.index_path.exists():
            try:
                self._read(current)
                return
            except ValueError:
                # Obsah ani text výjimky nelogovat: index obsahuje soukromá metadata.
                log.warning("Index profilů je poškozený – zálohuji a obnovuji profily")
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                self.index_path.replace(self.root / f"{INDEX}.bad-{stamp}")
            recovered = []
            ids = set()
            for path in sorted(self.root.glob("*.json")):
                if (not _valid_uuid(path.stem) or path != self._file(path.stem)
                        or uuid.UUID(path.stem) in ids):
                    continue
                try:
                    with path.open(encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        raise ValueError("Neplatný snapshot profilu")
                    AppConfig.from_dict(data)
                except (OSError, ValueError, TypeError, AttributeError):
                    log.warning("Přeskakuji nečitelný snapshot profilu při obnově indexu")
                    continue
                profile_id = str(uuid.UUID(path.stem))
                # Bez původní vazby na účet se obnovený profil nesmí sám nahrát jinam.
                recovered.append({"id": profile_id, "name": f"Obnovený profil {len(recovered) + 1}",
                                  "local_only": True, "recovered": True})
                ids.add(uuid.UUID(profile_id))
            index = {"schema_version": 1, "profiles": recovered}
            self._recover_current(index, current)
            atomic_write_json(self.index_path, index)
            return
        profile_id = str(uuid.uuid4())
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._file(profile_id), current.to_dict())
        atomic_write_json(self.index_path, {
            "schema_version": 1, "active": profile_id,
            "profiles": [{"id": profile_id, "name": "Soukromý"}],
        })

    def list_profiles(self) -> list[dict]:
        return [dict(item) for item in self._read()["profiles"]]

    def active_id(self) -> str:
        return self._read()["active"]

    def record(self, profile_id: str) -> dict:
        for item in self._read()["profiles"]:
            if item["id"] == profile_id:
                return dict(item)
        raise ValueError("Neznámý profil")

    def bind_remote(self, profile_id: str, remote_id: str, revision: int,
                    account: str, server: str, synced_hash: str | None = None) -> None:
        """Spáruje lokální profil se vzdáleným po potvrzeném úspěšném zápisu.

        synced_hash je otisk obsahu, který je v tu chvíli shodný se serverem; podle něj se
        pozná, že se profil od poslední synchronizace místně změnil.
        """
        uuid.UUID(remote_id)
        if not isinstance(revision, int) or revision < 1:
            raise ValueError("Neplatná revize")
        index = self._read()
        for item in index["profiles"]:
            if item["id"] == profile_id:
                item["remote_id"] = remote_id
                item["remote_revision"] = revision
                item["remote_account"] = account.strip().casefold()
                item["remote_server"] = server.rstrip("/")
                item["synced_hash"] = synced_hash or ""
                item.pop("local_only", None)
                item.pop("recovered", None)
                atomic_write_json(self.index_path, index)
                return
        raise ValueError("Neznámý profil")

    def load(self, profile_id: str) -> AppConfig:
        if profile_id not in {p["id"] for p in self.list_profiles()}:
            raise ValueError("Neznámý profil")
        with self._file(profile_id).open(encoding="utf-8") as f:
            return AppConfig.from_dict(json.load(f))

    def save(self, profile_id: str, cfg: AppConfig) -> None:
        if profile_id not in {p["id"] for p in self.list_profiles()}:
            raise ValueError("Neznámý profil")
        atomic_write_json(self._file(profile_id), cfg.to_dict())

    def save_active(self, cfg: AppConfig) -> None:
        self.save(self.active_id(), cfg)

    def create(self, name: str, cfg: AppConfig) -> str:
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("Název musí mít 1 až 100 znaků")
        index = self._read()
        if any(p["name"].casefold() == name.casefold() for p in index["profiles"]):
            raise ValueError("Profil s tímto názvem už existuje")
        profile_id = str(uuid.uuid4())
        atomic_write_json(self._file(profile_id), cfg.to_dict())
        index["profiles"].append({"id": profile_id, "name": name})
        atomic_write_json(self.index_path, index)
        return profile_id

    def rename(self, profile_id: str, name: str) -> None:
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("Název musí mít 1 až 100 znaků")
        index = self._read()
        if any(p["name"].casefold() == name.casefold() and p["id"] != profile_id
               for p in index["profiles"]):
            raise ValueError("Profil s tímto názvem už existuje")
        for item in index["profiles"]:
            if item["id"] == profile_id:
                item["name"] = name      # vazba na server zůstává; nový název se nahraje při synchronizaci
                atomic_write_json(self.index_path, index)
                return
        raise ValueError("Neznámý profil")

    def activate(self, profile_id: str) -> AppConfig:
        cfg = self.load(profile_id)  # nejprve ověřit, že snapshot je čitelný
        index = self._read()
        index["active"] = profile_id
        atomic_write_json(self.index_path, index)
        return cfg

    def delete(self, profile_id: str) -> None:
        index = self._read()
        if profile_id == index["active"]:
            raise ValueError("Aktivní profil nelze smazat")
        if profile_id not in {p["id"] for p in index["profiles"]}:
            raise ValueError("Neznámý profil")
        gone = next(p for p in index["profiles"] if p["id"] == profile_id)
        if gone.get("remote_id"):
            # Smazání se na serveru dokončí při nejbližší synchronizaci (i když je teď počítač offline).
            index.setdefault("pending_deletes", []).append({
                "remote_id": gone["remote_id"], "revision": int(gone.get("remote_revision", 0)) or None,
                "account": gone.get("remote_account", ""), "server": gone.get("remote_server", "")})
        index["profiles"] = [p for p in index["profiles"] if p["id"] != profile_id]
        atomic_write_json(self.index_path, index)
        self._file(profile_id).unlink(missing_ok=True)

    def pending_deletes(self, account: str, server: str) -> list[tuple[str, int | None]]:
        return [(d["remote_id"], d.get("revision")) for d in self._read().get("pending_deletes", [])
                if d.get("account") == account and d.get("server") == server]

    def clear_pending_delete(self, remote_id: str) -> None:
        index = self._read()
        index["pending_deletes"] = [d for d in index.get("pending_deletes", []) if d.get("remote_id") != remote_id]
        atomic_write_json(self.index_path, index)

    def unbind(self, profile_id: str, local_only: bool = False) -> None:
        """Zruší vazbu na server. local_only=True: profil se už sám nenahrává."""
        index = self._read()
        for item in index["profiles"]:
            if item["id"] == profile_id:
                for key in [k for k in item if k.startswith("remote_") or k == "synced_hash"]:
                    del item[key]
                if local_only:
                    item["local_only"] = True
                else:
                    item.pop("local_only", None)
                item.pop("recovered", None)
                atomic_write_json(self.index_path, index)
                return
        raise ValueError("Neznámý profil")

    # -- spojení profilu se sítí (jen pro tento počítač, nesynchronizuje se) --
    def bind_network(self, profile_id: str, key: str, label: str) -> None:
        """Jedna síť patří nejvýš jednomu profilu."""
        if not key:
            raise ValueError("Síť se nepodařilo rozpoznat")
        index = self._read()
        if profile_id not in {p["id"] for p in index["profiles"]}:
            raise ValueError("Neznámý profil")
        for item in index["profiles"]:
            nets = [n for n in item.get("networks", []) if n.get("key") != key]
            if item["id"] == profile_id:
                nets.append({"key": key, "label": label[:80]})
            if nets:
                item["networks"] = nets
            else:
                item.pop("networks", None)
        atomic_write_json(self.index_path, index)

    def unbind_networks(self, profile_id: str) -> None:
        index = self._read()
        for item in index["profiles"]:
            if item["id"] == profile_id:
                item.pop("networks", None)
        atomic_write_json(self.index_path, index)

    def profile_for_network(self, key: str) -> str | None:
        for item in self._read()["profiles"]:
            if any(n.get("key") == key for n in item.get("networks", [])):
                return item["id"]
        return None

    def unique_name(self, name: str, skip_id: str | None = None) -> str:
        taken = {p["name"].casefold() for p in self.list_profiles() if p["id"] != skip_id}
        return _unique_name(name, taken)

    def unbind_remote(self, remote_id: str, account: str, server: str) -> None:
        index = self._read()
        for item in index["profiles"]:
            if (item.get("remote_id") == remote_id
                    and item.get("remote_account") == account
                    and item.get("remote_server") == server):
                for key in list(item):
                    if key.startswith("remote_") or key == "synced_hash":
                        del item[key]
        atomic_write_json(self.index_path, index)
