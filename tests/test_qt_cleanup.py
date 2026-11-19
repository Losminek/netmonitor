"""Regrese L45: Qt cykly se musí uvolnit mezi testy, mimo processEvents()."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_qt_cycles_are_collected_between_tests(tmp_path):
    tests_dir = Path(__file__).resolve().parent
    (tmp_path / "conftest.py").write_text(
        (tests_dir / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "test_lifecycle.py").write_text(textwrap.dedent('''\
        import gc
        import weakref

        import pytest
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtWidgets import QApplication

        from netmonitor.core.config import AppConfig
        from netmonitor.gui.controller import Controller
        from netmonitor.gui.sync_service import SyncService

        refs = []

        @pytest.fixture(scope="module", autouse=True)
        def qapp():
            app = QApplication.instance() or QApplication([])
            # Automatický GC nesmí náhodou zakrýt chybějící úklid mezi testy.
            gc.disable()
            yield app
            gc.enable()

        @pytest.fixture
        def qt_objects(tmp_path):
            cfg = AppConfig.from_dict({"network": {"external_api": False}})
            ctrl = Controller(cfg, tmp_path / "config.json", tmp_path / "history.sqlite3")
            sync = SyncService(ctrl)
            ctrl.sync = sync
            sync._periodic.start()
            sync._debounce.start()
            refs.extend(weakref.ref(obj) for obj in (
                ctrl, sync, ctrl._save_timer, sync._periodic, sync._debounce))
            yield
            ctrl.shutdown()

        def test_01_leave_cycles(qt_objects):
            assert all(ref() is not None for ref in refs)

        def test_02_next_event_loop_is_clean():
            assert len(refs) == 5
            assert all(ref() is None for ref in refs), "Qt objekty přežily konec testu"
            QCoreApplication.processEvents()
        '''), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               NETMONITOR_HOME=str(tmp_path / "home"), PYTHONPATH=str(tests_dir.parent / "src"))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--confcutdir", str(tmp_path), "test_lifecycle.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
