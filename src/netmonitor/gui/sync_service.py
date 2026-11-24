"""Účet a automatická synchronizace profilů – propojení klienta, úložiště a GUI.

Síť běží vždy na pozadí (Controller._run_bg); místní soubory se čtou a zapisují jen ve vlákně GUI.
Bez přihlášení se nic neodesílá a aplikace funguje čistě lokálně.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import sync_engine
from ..core.config import atomic_write_json
from ..core.secret_store import SecretStore
from ..core.sync_client import DEFAULT_SERVER, SyncClient, SyncError
from ..utils.paths import config_dir

log = logging.getLogger(__name__)
SETTINGS_FILE = "sync.json"


@dataclass
class _Result:
    value: object


def _safe(fn: Callable[[], object]) -> Callable[[], _Result]:
    """Obal doručí i chybu přes Controller, který samotným výjimkám nevolá done."""
    def call():
        try:
            return _Result(fn())
        except SyncError as exc:
            return _Result(exc)
        except Exception as exc:  # noqa: BLE001 – dokončení musí proběhnout i při neočekávané chybě
            # Text výjimky a zdrojové řádky mohou obsahovat heslo, token nebo tělo odpovědi.
            log.error("Neočekávaná chyba synchronizace (%s)", type(exc).__name__)
            frames = traceback.extract_tb(exc.__traceback__)
            log.debug("Místa chyby synchronizace:\n%s",
                      "".join(f"  {os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}\n"
                              for frame in frames))
            return _Result(SyncError("Při komunikaci se serverem došlo k neočekávané chybě. "
                                     "Zkuste to prosím znovu."))
    return call


class SyncService(QObject):
    changed = Signal()                  # stav přihlášení / text stavu
    conflicts_found = Signal(object)    # list[sync_engine.Action]

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.secrets = SecretStore()
        self._settings_path = config_dir() / SETTINGS_FILE
        self.settings = self._load_settings()
        self._server_override = os.environ.get("NETMONITOR_SYNC_URL", "")
        if self._server_override:
            try:
                self._server_override = SyncClient(self._server_override).base_url
            except ValueError:
                self._server_override = ""      # neplatná vývojová URL nezmění uložený server
        self._tokens_lock = threading.RLock()  # jen změny relace a soubor tokenu, nikdy síť
        self.client = self._make_client(self._server_override or self.settings["server"])
        self.status = "Nepřihlášeno – profily jsou uložené jen v tomto počítači."
        self.last_sync: float | None = None
        self.busy = False
        self.remember = True
        self.errors: list[str] = []
        self.pending_conflicts: list = []
        self._resume_pending = False       # obnova uložené relace odložená kvůli síti/výpadku serveru
        self._resume_gen = 0               # odhlášení zneplatní výsledek rozběhnuté obnovy
        self._again = False
        self._resolutions: dict[str, str] = {}
        self._asked: set = set()            # konflikty, na které už byl uživatel dotázán
        self._debounce = QTimer(self, singleShot=True, interval=4000, timeout=self.sync_now)
        self._periodic = QTimer(self, interval=5 * 60 * 1000, timeout=self.sync_now)
        ctrl.config_changed.connect(self._local_change)
        ctrl.profiles_changed.connect(self._local_change)

    # ------------------------------------------------------------------ nastavení
    def _load_settings(self) -> dict:
        data = {"server": DEFAULT_SERVER, "email": ""}
        try:
            with self._settings_path.open(encoding="utf-8") as f:
                stored = json.load(f)
            for key in data:
                if isinstance(stored.get(key), str) and stored[key]:
                    data[key] = stored[key]
        except (OSError, ValueError, AttributeError):
            pass
        return data

    def _save_settings(self) -> None:
        try:
            atomic_write_json(self._settings_path, self.settings)
        except OSError as exc:
            log.warning("Nastavení synchronizace nelze uložit: %s", exc)

    def _make_client(self, server: str) -> SyncClient:
        try:
            client = SyncClient(server)
        except ValueError:
            client = SyncClient(DEFAULT_SERVER)
            self.settings["server"] = DEFAULT_SERVER

        def tokens_changed(refresh):
            with self._tokens_lock:
                if client is self.client:
                    self._tokens_changed(refresh)

        client.on_tokens = tokens_changed
        return client

    def set_server(self, server: str) -> None:
        """Změna serveru odhlásí. Vývojové přepsání má přednost a nikdy se neukládá.

        Bez přepsání vyhazuje ValueError u neplatné adresy.
        """
        if self._server_override:
            return
        server = server.strip().rstrip("/")
        if server == self.client.base_url:
            return
        SyncClient(server)                       # ověření adresy
        if self.client.logged_in:
            self.logout()
        self.settings["server"] = server
        self._save_settings()
        self.client = self._make_client(server)

    def _tokens_changed(self, refresh: str | None) -> None:
        # Volá se z vlákna na pozadí; zápis malého souboru je bezpečný a musí proběhnout hned,
        # jinak by po pádu aplikace zůstal uložený už vyměněný (neplatný) token.
        try:
            if refresh is None:
                self.secrets.clear()
            elif self.remember:
                self.secrets.save(self.client.base_url, self.client.email or self.settings["email"], refresh)
        except OSError as exc:
            log.warning("Přihlášení nelze uložit: %s", exc)

    # ------------------------------------------------------------------ stav
    @property
    def logged_in(self) -> bool:
        return self.client.logged_in and self.client.user is not None

    @property
    def account(self) -> str:
        return self.client.email.casefold()

    def _set_status(self, text: str) -> None:
        self.status = text
        self.changed.emit()

    def profile_state(self, record: dict) -> str:
        """Krátký popis stavu synchronizace jednoho místního profilu."""
        if not self.logged_in:
            return "jen v tomto počítači" if not record.get("remote_id") else "uloženo na serveru (nepřihlášeno)"
        if record.get("local_only"):
            return "jen místní (obnovený profil)" if record.get("recovered") else "jen místní (na serveru smazán)"
        if record.get("remote_id"):
            if (record.get("remote_account"), record.get("remote_server")) != (self.account, self.client.base_url):
                return "patří k jinému účtu"
            if any(a.local_id == record["id"] for a in self.pending_conflicts):
                return "konflikt – čeká na rozhodnutí"
            return "synchronizováno" if self._clean(record) else "čeká na nahrání"
        return "čeká na nahrání"

    def _clean(self, record: dict) -> bool:
        store = self.ctrl.profile_store
        try:
            from ..core.sync_config import export_shared
            # Aktivní profil: rozhoduje běžící konfigurace (na disk se zapisuje s malým zpožděním).
            cfg = self.ctrl.cfg if record["id"] == store.active_id() else store.load(record["id"])
            return sync_engine.profile_hash(record["name"], export_shared(cfg)) == record.get("synced_hash")
        except (OSError, ValueError, KeyError):
            return False

    # ------------------------------------------------------------------ účet
    def _job(self, key: str, fn: Callable[[], object], done: Callable[[object], None]) -> bool:
        # Úlohu, kterou Controller nespustí (stejný klíč běží, aplikace končí), vůbec nezačínat –
        # jinak by busy zůstalo nastavené navždy.
        if self.busy or self.ctrl._stopping or ("sync-" + key) in self.ctrl._busy:
            return False
        self.busy = True
        self._set_status("Komunikuji se serverem…")

        def finished(result: _Result):
            self.busy = False
            done(result.value)

        self.ctrl._run_bg("sync-" + key, _safe(fn), finished)
        return True

    def start(self) -> None:
        """Po startu aplikace obnoví uložené přihlášení (pokud existuje)."""
        stored = self.secrets.load()
        if stored is None or stored[0] != self.client.base_url:
            return
        _server, email, token = stored
        self.settings["email"] = email
        self._resume(token)

    @property
    def resume_pending(self) -> bool:
        """Uložená relace čeká na obnovu (server zatím nedostupný)."""
        return self._resume_pending

    @staticmethod
    def _transient(exc: Exception) -> bool:
        """Výpadek sítě, chyba serveru (5xx) nebo omezení (429) – obnovu má smysl zopakovat."""
        if not isinstance(exc, SyncError):
            return False
        return exc.code == "network_error" or (exc.status is not None and (exc.status >= 500 or exc.status == 429))

    def _resume(self, token: str) -> None:
        gen = self._resume_gen
        client = self.client                    # klient zachycený při spuštění obnovy

        def done(result):
            if gen != self._resume_gen:
                # Mezitím odhlášeno: obnova na pozadí mohla klientovi vrátit tokeny – relaci ukončit
                # i na serveru a tokeny zahodit. Callback odpojeného klienta už soubor nezmění.
                self._logout_late(client)
                return
            if isinstance(result, Exception):
                self._resume_pending = self._transient(result) and bool(self.client.refresh_token)
                if self._resume_pending:
                    self._set_status(f"Server není dostupný, pracuji offline. ({result})")
                    self._periodic.start()
                else:
                    self._periodic.stop()
                    if isinstance(result, SyncError) and result.status == 401:
                        self._set_status("Přihlášení vypršelo. Přihlaste se znovu v Nastavení → Účet.")
                    else:
                        self._set_status(f"Přihlášení nelze obnovit: {result}")
                return
            self._logged_in()

        self._job("resume", lambda: client.resume(token), done)

    def login(self, email: str, password: str, code: str | None, remember: bool,
              done: Callable[[SyncError | None], None]) -> None:
        self._auth("login", lambda: self.client.login(email, password, code), email, remember, done)

    def register(self, email: str, password: str, license_key: str, remember: bool,
                 done: Callable[[SyncError | None], None]) -> None:
        self._auth("register", lambda: self.client.register(email, password, license_key), email, remember, done)

    def _auth(self, key, fn, email, remember, done) -> None:
        self.remember = remember
        self.settings["email"] = email.strip()

        def finished(result):
            if isinstance(result, Exception):
                error = result if isinstance(result, SyncError) else SyncError(str(result))
                self._set_status("Nepřihlášeno: " + str(error) if error.code != "totp_required"
                                 else "Zadejte kód z autentikátoru.")
                done(error)
                return
            self._save_settings()
            self._logged_in()
            done(None)

        if not self._job(key, fn, finished):
            done(SyncError("Probíhá jiná operace, zkuste to za okamžik."))

    def _logged_in(self) -> None:
        self._resume_pending = False
        self._periodic.start()
        self._set_status(f"Přihlášeno jako {self.client.email}.")
        self.sync_now()

    def logout(self) -> None:
        self._resume_pending = False
        self._resume_gen += 1
        self._periodic.stop()
        self._debounce.stop()
        client = self.client
        self.pending_conflicts, self.errors = [], []
        self._asked.clear()
        if client.access_token and not self.ctrl._stopping:
            # Relaci na serveru ukončí samostatný klient; ten hlavní se vyprázdní hned.
            old = SyncClient(client.base_url, access_token=client.access_token)
            self.ctrl._run_bg("sync-logout", old.logout, lambda _r: None)
        with self._tokens_lock:
            # Na síťový zámek klienta v GUI nečekat. Pozdní obnova zůstane ve starém
            # klientovi a její callback už nemůže uložit token ani smazat novou relaci.
            self.client = self._make_client(client.base_url)
            client.access_token = client.refresh_token = client.user = None
            client.mfa = False
            self.secrets.clear()
        self._set_status("Odhlášeno – profily zůstávají v tomto počítači.")

    def _logout_late(self, client: SyncClient) -> None:
        """Zahodí pozdní tokeny odpojeného klienta a ukončí jeho relaci i na serveru."""
        if client is self.client:
            return
        if client.access_token and not self.ctrl._stopping:
            old = SyncClient(client.base_url, access_token=client.access_token)
            self.ctrl._run_bg("sync-logout-late", old.logout, lambda _r: None)
        client.access_token = client.refresh_token = client.user = None
        client.mfa = False

    def run(self, key: str, fn: Callable[[], object], done: Callable[[object], None]) -> None:
        """Obecný požadavek na server na pozadí (správa klíčů, 2FA…). done dostane výsledek nebo výjimku."""
        client = self.client

        def finished(result):
            self._logout_late(client)
            done(result.value)

        self.ctrl._run_bg("sync-x-" + key, _safe(fn), finished)

    # ------------------------------------------------------------------ synchronizace
    def _local_change(self, *_args) -> None:
        if self.logged_in and self.ctrl.profile_store is not None:
            self._debounce.start()
            self.changed.emit()

    def resolve(self, local_id: str, choice: str) -> None:
        self._resolutions[local_id] = choice
        self.pending_conflicts = [a for a in self.pending_conflicts if a.local_id != local_id]

    def sync_now(self) -> None:
        store = self.ctrl.profile_store
        if store is None or self.ctrl._stopping:
            return
        if self._resume_pending and self.client.refresh_token:
            if not self.busy:
                self._resume(self.client.refresh_token)
            return
        if not self.logged_in:
            return
        if self.busy:
            self._again = True
            return
        try:
            if self.ctrl._save_timer.isActive():
                self.ctrl._save_timer.stop()
                self.ctrl._save_now()
            snap = sync_engine.snapshot(store, self.account, self.client.base_url)
        except (OSError, ValueError, KeyError) as exc:
            self._set_status(f"Synchronizace selhala: {exc}")
            return
        resolutions, self._resolutions = self._resolutions, {}
        client = self.client

        def done(result):
            if client is not self.client:
                self._logout_late(client)
                self._again = False
                self.changed.emit()            # starý výsledek nepatří ani nové relaci téhož účtu
                return
            self._finished(snap, result)

        self._job("run", lambda: sync_engine.run(client, snap, resolutions), done)

    def _finished(self, snap, result) -> None:
        # Po chybě počkáme na další ruční/periodický běh; trvalý výpadek nesmí
        # založit rychlé opakování. Nový běh vždy načte aktuální místní změny.
        again, self._again = self._again, False
        store = self.ctrl.profile_store
        if isinstance(result, SyncError) and result.status == 401 and not self.client.logged_in:
            self._periodic.stop()
            self._set_status("Přihlášení vypršelo nebo bylo zrušeno. Přihlaste se znovu.")
            return
        if isinstance(result, Exception):
            self._set_status(f"Synchronizace odložena: {result}")
            return
        if not self.logged_in or snap.account != self.account:
            self.changed.emit()                  # mezitím odhlášeno – výsledek se zahodí
            return
        try:
            out = sync_engine.apply(store, snap, result, self.ctrl.cfg.copy())
        except (OSError, ValueError, KeyError) as exc:
            self._set_status(f"Synchronizaci nelze uložit: {exc}")
            return
        self.errors = out.errors
        self.pending_conflicts = out.conflicts
        self.last_sync = time.time()
        if out.active_cfg is not None:
            self.ctrl.apply_profile_config(out.active_cfg)
        if out.changed:
            self.ctrl.profiles_changed.emit()
        parts = [f"Synchronizováno {time.strftime('%H:%M:%S')}"]
        if out.uploaded:
            parts.append(f"nahráno {out.uploaded}")
        if out.downloaded:
            parts.append(f"staženo {out.downloaded}")
        if out.conflicts:
            parts.append(f"konflikty: {len(out.conflicts)}")
        if out.errors:
            parts.append("chyby: " + "; ".join(out.errors[:3]))
        self._set_status(f"Přihlášeno jako {self.client.email}. " + ", ".join(parts) + ".")
        fresh = [a for a in out.conflicts if (a.local_id, (a.remote or {}).get("revision")) not in self._asked]
        self._asked.update((a.local_id, (a.remote or {}).get("revision")) for a in fresh)
        if fresh:
            self.conflicts_found.emit(fresh)
        if again or (out.changed and any(a.kind == "fork" for a in result)):
            QTimer.singleShot(0, self.sync_now)
