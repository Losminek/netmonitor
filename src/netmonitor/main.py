"""Vstupní bod NetMonitoru.

    netmonitor                 spustí widget
    netmonitor --minimized     spustí skrytě (jen ikona v oznamovací oblasti)
    netmonitor --diagnose      textová diagnostika bez GUI (síť, DNS, WAN, ping)
    netmonitor --debug         podrobný log
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path

from . import APP_NAME, __version__
from .core import config as cfgmod
from .utils.logging_setup import setup_logging
from .utils.paths import data_dir, ensure_dirs

log = logging.getLogger("netmonitor")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="netmonitor", description="NetMonitor – desktopový síťový monitor")
    p.add_argument("--minimized", action="store_true", help="spustit skrytě v oznamovací oblasti")
    p.add_argument("--debug", action="store_true", help="podrobné logování")
    p.add_argument("--config", type=Path, help="cesta ke config.json")
    p.add_argument("--diagnose", action="store_true", help="vypsat diagnostiku a skončit")
    p.add_argument("--reset-position", action="store_true", help="vrátit widget do pravého horního rohu")
    p.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)       # interní: render do PNG a konec
    p.add_argument("--screenshot-delay", type=float, default=6.0, help=argparse.SUPPRESS)
    p.add_argument("--screenshot-bg", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p.parse_args(argv)


def diagnose(cfg: cfgmod.AppConfig) -> int:
    from .core.network_info import NetworkInfoProvider
    from .core.ping_engine import Resolver, ping_host, select_pinger
    from .core.wan_info import WanState, fetch_wan_ip
    out = {"version": __version__, "platform": sys.platform, "python": sys.version.split()[0]}
    snap = NetworkInfoProvider().snapshot()
    a = snap.active
    out["network"] = {"default_iface": snap.default_iface, "has_default_route": snap.has_default_route,
                      "active": a.__dict__ if a else None, "error": snap.error}
    pinger = select_pinger()
    out["icmp"] = pinger.name
    r = Resolver()
    out["ping"] = {f"{t.name} ({t.host})": (lambda res: {"status": res.status.value,
                                                          "rtt_ms": round(res.rtt_ms, 2) if res.rtt_ms is not None else None,
                                                          "ip": res.address, "error": res.error})(
        ping_host(t.host, t.timeout_s, pinger, r)) for t in cfg.ping.targets if t.enabled}
    if cfg.network.external_api:
        w = fetch_wan_ip()
        out["wan"] = {"ip": w.ip, "provider": w.provider, "error": w.error}
        if w.ip and cfg.network.isp_detection:
            out["wan"]["isp"] = WanState(None).isp_for(w.ip, cfg.network.isp_provider, 0)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    ensure_dirs()
    setup_logging(args.debug)
    log.info("%s %s start (%s, Python %s)", APP_NAME, __version__, sys.platform, sys.version.split()[0])
    cfg_path = args.config or cfgmod.default_config_path()
    cfg = cfgmod.load_config(cfg_path)
    if not cfg_path.exists():
        cfgmod.save_config(cfg, cfg_path)
    if args.diagnose:
        return diagnose(cfg)
    if args.reset_position:
        cfg.appearance.x = cfg.appearance.y = None

    # Wayland: kompozitor neumožňuje pozicovat okna ani vrstvy => preferuj XWayland, je-li k dispozici
    if (sys.platform.startswith("linux") and cfg.network.linux_prefer_xwayland and os.environ.get("WAYLAND_DISPLAY")
            and os.environ.get("DISPLAY") and "QT_QPA_PLATFORM" not in os.environ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        log.info("Wayland detekován – používám XWayland (xcb) kvůli pozici a vrstvě okna")

    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

    from .gui.i18n import set_language
    from .gui.icons import app_icon

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName("Zelina")
    QApplication.setApplicationVersion(__version__)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon("ok"))
    set_language(cfg.general.language)

    lock = QLockFile(str(data_dir() / "netmonitor.lock"))
    lock.setStaleLockTime(0)
    if not args.screenshot and not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, "NetMonitor už běží (ikona v oznamovací oblasti).")
        return 0

    from .gui.controller import Controller
    from .gui.history_dialog import HistoryDialog
    from .gui.main_widget import MainWidget
    from .gui.settings_dialog import SettingsDialog
    from .gui.tray import Tray

    tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
    app.setProperty("tray_available", tray_ok)
    if not tray_ok and cfg.appearance.click_through:
        cfg.appearance.click_through = False      # bez tray by nešlo vypnout
        log.warning("Oznamovací oblast nedostupná – click-through vypnut")

    hist_path = None
    if args.screenshot:
        import tempfile
        hist_path = Path(tempfile.mkdtemp()) / "h.sqlite3"
    ctrl = Controller(cfg, None if args.screenshot else cfg_path, hist_path)
    widget = MainWidget(ctrl)
    tray = None
    if tray_ok and not args.screenshot:
        tray = Tray(widget)
        tray.show()

    dialogs: dict[str, object] = {}

    def history(tid: str = ""):
        d = dialogs.get("history")
        if d is None:
            d = dialogs["history"] = HistoryDialog(ctrl)
        d.show_target(tid)

    def settings():
        d = dialogs.get("settings")
        if d is None:
            d = dialogs["settings"] = SettingsDialog(ctrl, widget)
            d.history_requested.connect(history)
        d.load()
        d.show()
        d.raise_()
        d.activateWindow()

    widget.open_settings.connect(settings)
    widget.open_history.connect(history)
    widget.quit_requested.connect(app.quit)
    app.aboutToQuit.connect(ctrl.shutdown)

    # synchronizace autostartu s realitou (např. po přesunu EXE)
    try:
        from . import platform as plat
        real = plat.current().autostart_enabled()
        if cfg.general.autostart:
            plat.current().set_autostart(True, ctrl.autostart_command())
        elif real and not args.screenshot:    # zapnuto zvenku (např. instalátorem) => převezmi stav
            cfg.general.autostart = True
            ctrl.schedule_save()
    except Exception:  # noqa: BLE001
        log.exception("Autostart sync selhal")

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    sig_timer = QTimer(interval=500, timeout=lambda: None)   # umožní Pythonu zpracovat SIGINT
    sig_timer.start()

    ctrl.start()
    start_hidden = (args.minimized or cfg.general.start_minimized) and tray_ok
    if not start_hidden:
        widget.show()

    if cfg.general.check_updates and cfg.general.update_url and tray:
        _check_update(ctrl, cfg.general.update_url, tray)

    if args.screenshot:
        QTimer.singleShot(int(args.screenshot_delay * 1000),
                          lambda: (_save_screenshot(widget, args.screenshot, args.screenshot_bg), app.quit()))
    rc = app.exec()
    lock.unlock()
    log.info("Konec (%s)", rc)
    return rc


def _save_screenshot(widget, path: Path, bg: Path | None) -> None:
    """Vykreslí průhledný widget na zadané pozadí (ověření vzhledu bez kompozitoru)."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
    img = widget.grab().toImage()
    out = QImage(img.width() + 80, img.height() + 80, QImage.Format.Format_ARGB32)
    p = QPainter(out)
    if bg and bg.exists():
        b = QImage(str(bg)).scaled(out.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
        p.drawImage(0, 0, b)
    else:
        g = QLinearGradient(0, 0, out.width(), out.height())
        g.setColorAt(0, QColor("#041a3d"))
        g.setColorAt(0.6, QColor("#0a4c9c"))
        g.setColorAt(1, QColor("#08305f"))
        p.fillRect(out.rect(), g)
    p.drawImage(QPoint(40, 40), img)
    p.end()
    out.save(str(path))


def parse_update_info(data: dict) -> tuple[str, str] | None:
    """Podporuje GitHub Releases API (tag_name/html_url) i vlastní JSON (version/url)."""
    if not isinstance(data, dict):
        return None
    ver = str(data.get("tag_name") or data.get("version") or "").strip().lstrip("vV")
    url = str(data.get("html_url") or data.get("url") or "")
    return (ver, url) if ver else None


def is_newer(latest: str, current: str = __version__) -> bool:
    def key(v: str):
        out = []
        for part in v.split("."):
            num = "".join(ch for ch in part if ch.isdigit())
            out.append(int(num) if num else 0)
        return tuple(out + [0] * (4 - len(out)))
    try:
        return key(latest) > key(current)
    except ValueError:
        return False


def _check_update(ctrl, url: str, tray) -> None:
    if not url.startswith("https://"):
        return

    def job():
        from .core.wan_info import http_get
        return json.loads(http_get(url, 8))
    from PySide6.QtWidgets import QSystemTrayIcon

    def done(data):
        info = parse_update_info(data)
        if info and is_newer(info[0]):
            log.info("Dostupná nová verze %s: %s", *info)
            tray.showMessage("NetMonitor", f"Je dostupná nová verze {info[0]}\n{info[1]}",
                             QSystemTrayIcon.MessageIcon.Information, 15000)
    ctrl._run_bg("update-check", job, done)


if __name__ == "__main__":
    sys.exit(main())
