"""Regrese 1.0–1.3.3: chybějící libxcb-cursor0 způsobila pád při startu Qt (xcb)."""
import pytest

from netmonitor.main import select_qt_platform, xcb_available


@pytest.mark.parametrize("xcb_ok", [True, False])
def test_wayland_with_xwayland(xcb_ok: bool):
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    selected, warning = select_qt_platform(env, True, "linux", lambda: xcb_ok)
    assert selected == "xcb;wayland"
    if xcb_ok:
        assert warning is None
    else:
        assert warning and "Debian/Ubuntu: sudo apt install libxcb-cursor0)" in warning
    assert env == {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}


def test_x11_without_xcb():
    selected, warning = select_qt_platform({"DISPLAY": ":0"}, True, "linux", lambda: False)
    assert selected is None
    assert warning and "Debian/Ubuntu: sudo apt install libxcb-cursor0)" in warning


def unexpected_xcb_check() -> bool:
    pytest.fail("Předkontrola xcb se v této situaci nemá spouštět")


@pytest.mark.parametrize("value", ["wayland", "xcb", "offscreen", ""])
def test_user_platform_is_preserved(value: str):
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "QT_QPA_PLATFORM": value}
    assert select_qt_platform(env, True, "linux", unexpected_xcb_check) == (None, None)
    assert env["QT_QPA_PLATFORM"] == value


@pytest.mark.parametrize(("env", "prefer_xwayland", "platform"), [
    ({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, False, "linux"),
    ({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, True, "win32"),
    ({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, True, "darwin"),
    ({"WAYLAND_DISPLAY": "wayland-0"}, True, "linux"),
    ({}, True, "linux"),
])
def test_no_platform_change(env: dict[str, str], prefer_xwayland: bool, platform: str):
    assert select_qt_platform(env, prefer_xwayland, platform, unexpected_xcb_check) == (None, None)


def test_x11_with_xcb():
    assert select_qt_platform({"DISPLAY": ":0"}, True, "linux", lambda: True) == (None, None)


@pytest.mark.parametrize("available", [True, False])
def test_xcb_library_precheck(monkeypatch, available: bool):
    def load(name: str):
        assert name == "libxcb-cursor.so.0"
        if not available:
            raise OSError("Knihovna není dostupná")
        return object()

    monkeypatch.setattr("netmonitor.main.ctypes.CDLL", load)
    assert xcb_available() is available
