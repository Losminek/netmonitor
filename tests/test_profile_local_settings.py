"""Přepnutí profilu zachová místní volby i při starším úplném snapshotu (L36)."""
import pytest

from netmonitor.core.config import AppConfig, PingTarget, load_config
from netmonitor.core.profiles import ProfileStore
from netmonitor.gui import controller, profile_prompts

from .fakes import FakePinger
from .test_gui_smoke import qapp  # noqa: F401


@pytest.mark.parametrize("automatic", [False, True], ids=["rucne", "podle-site"])
@pytest.mark.parametrize("local_enabled", [False, True], ids=["mistni-vypnuto", "mistni-zapnuto"])
def test_switch_preserves_machine_settings(qapp, tmp_path, monkeypatch, automatic, local_enabled):
    mine = AppConfig()
    mine.ping.targets = [PingTarget("Místní", "192.0.2.1", id="local")]
    mine.network.adapter_auto = local_enabled
    mine.network.adapter_name = "eth-local"
    mine.network.external_api = local_enabled
    mine.network.isp_detection = local_enabled
    mine.network.refresh_s = 17
    mine.general.language = "cs" if local_enabled else "en"
    mine.general.autostart = local_enabled
    mine.general.check_updates = local_enabled
    mine.general.receive_beta_updates = local_enabled
    mine.general.auto_update = local_enabled
    mine.appearance.x, mine.appearance.y = 700, 300
    mine.appearance.width, mine.appearance.height = 650, 900
    mine.appearance.screen = "LOCAL-SCREEN"
    mine.appearance.locked = local_enabled
    mine.appearance.compact = local_enabled
    mine.history.enabled = local_enabled
    mine.history.retention_days = 42
    mine.normalized()

    other = AppConfig()
    other.network.adapter_auto = not local_enabled
    other.network.adapter_name = "eth-old"
    other.network.external_api = not local_enabled
    other.general.language = "en" if local_enabled else "cs"
    other.general.autostart = not local_enabled
    other.general.check_updates = not local_enabled
    other.general.receive_beta_updates = not local_enabled
    other.general.auto_update = not local_enabled
    other.appearance.screen = "OLD-SCREEN"
    other.appearance.locked = not local_enabled
    other.ping.targets = [PingTarget("Firemní", "198.51.100.1", id="work")]
    other.ping.thresholds.green_max_ms = 25
    other.ping.thresholds.orange_max_ms = 75
    other.notifications.enabled = False
    other.notifications.high_latency = True
    other.notifications.delay_s = 23
    other.network.ip_profiles = [{"name": "Kancelář", "dhcp": False, "ip": "198.51.100.2",
                                  "mask": "255.255.255.0", "gateway": "198.51.100.1",
                                  "dns_dhcp": False, "dns": ["192.0.2.53"]}]
    other.network.switch_ip_profile = "Kancelář"
    other.normalized()

    monkeypatch.setattr(controller, "select_pinger", FakePinger)
    ctrl = controller.Controller(mine.copy(), tmp_path / "config.json", tmp_path / "history.sqlite3")
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(ctrl.cfg)
    monkeypatch.setattr(ctrl, "profile_store", store)
    home = store.active_id()
    work = store.create("Firemní", other)
    monkeypatch.setattr(ctrl, "_run_bg", lambda key, fn, done=None: done(fn()) if done else fn())
    refreshed = []
    monkeypatch.setattr(ctrl, "refresh_network", lambda: refreshed.append("network"))
    monkeypatch.setattr(ctrl, "refresh_wan", lambda **kw: refreshed.append("wan"))
    offered = []
    ctrl.ip_preset_offered.connect(offered.append)

    def check(cfg, shared):
        assert cfg.general == mine.general
        assert cfg.appearance == mine.appearance
        assert cfg.history == mine.history
        network = cfg.to_dict()["network"]
        local_network = mine.to_dict()["network"]
        for key in ("ip_profiles", "switch_ip_profile"):
            assert network.pop(key) == getattr(shared.network, key)
            local_network.pop(key)
        assert network == local_network
        assert cfg.ping == shared.ping
        assert cfg.notifications == shared.notifications

    try:
        if automatic:
            store.bind_network(work, "wifi:Example", "Wi-Fi „Example“")
            ctrl.profile_suggested.connect(lambda pid, name, net: profile_prompts.suggest_profile(ctrl, pid, name, net))
            monkeypatch.setattr(profile_prompts.QMessageBox, "show", lambda self: None)
            ctrl._suggest_profile("wifi:Example", "Wi-Fi „Example“")
            box = profile_prompts._open[-1]
            next(b for b in box.buttons() if b.text() == "Přepnout").click()
        else:
            ctrl.switch_profile(work)
        assert store.active_id() == work
        check(ctrl.cfg, other)
        check(store.load(work), other)
        check(store.load(home), mine)
        # Start i systémový autostart čtou config.json, nikoli celý snapshot aktivního profilu.
        restarted_store = ProfileStore(store.root)
        restarted = load_config(ctrl.config_path)
        restarted_store.initialize(restarted)
        assert restarted_store.active_id() == work
        check(restarted, other)
        assert ctrl.notifier.cfg == other.notifications
        assert offered == ["Kancelář"]
        ctrl.switch_profile(home)
        check(ctrl.cfg, mine)
        check(store.load(home), mine)
        check(load_config(ctrl.config_path), mine)
        assert offered == ["Kancelář"]
        assert refreshed == []
    finally:
        ctrl.shutdown()
