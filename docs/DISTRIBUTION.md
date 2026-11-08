# Distribuce

Všechny buildy používají jeden spec `packaging/netmonitor.spec` (PyInstaller ≥ 6.3, režim onedir –
rychlý start, vhodný pro autostart). Build skripty vytvoří vlastní venv `.venv-build` a nic mimo projekt nemění.

## Windows 10/11 x64

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1            # + testy
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -SkipTests
```

- Požadavky: Python 3.12/3.13 x64 z python.org (`py` launcher), volitelně Inno Setup 6
  (`winget install JRSoftware.InnoSetup`) pro instalátor.
- Výstup: `dist\NetMonitor\NetMonitor.exe` (+ celá složka), `dist\NetMonitor-<verze>-win64.zip`,
  `dist\NetMonitor-<verze>-setup.exe`.
- Instalátor: bez admin práv do `%LOCALAPPDATA%\Programs\NetMonitor`, volitelný autostart (HKCU\…\Run),
  zástupce v nabídce Start.
- Autostart z aplikace: *Nastavení → Obecné* zapisuje `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
- SmartScreen: nepodepsaný EXE vyvolá varování „Neznámý vydavatel“. Pro podpis: certifikát pro podpis kódu
  + `signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 dist\NetMonitor\NetMonitor.exe`.

## Linux

```bash
./scripts/build_linux.sh            # tar.gz s install.sh (bez sudo, do ~/.local)
./scripts/build_linux.sh --deb      # + .deb (/opt/netmonitor, /usr/bin/netmonitor)
./scripts/build_linux.sh --appimage # + AppImage (vyžaduje appimagetool)
```

- Systémové knihovny Qt: `sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libegl1`.
- Build je vázaný na glibc systému, kde vznikl – pro širokou kompatibilitu sestavujte na nejstarší
  podporované distribuci (např. Debian 12).
- Autostart: `~/.config/autostart/netmonitor.desktop` (XDG; GNOME, KDE, Xfce, Cinnamon…).

## macOS (Intel i Apple Silicon)

```bash
./scripts/build_macos.sh --dmg
```

- Sestavuje pro architekturu použitého Pythonu (`uname -m`). Universal2 vyžaduje universal2 Python
  i všechny wheely – PySide6 universal2 wheely existují; pak lze do specu přidat `target_arch="universal2"`.
- `Info.plist`: `LSUIElement` (bez ikony v Docku), `NSLocationWhenInUseUsageDescription` (SSID).
- Autostart: LaunchAgent `~/Library/LaunchAgents/eu.zelina.netmonitor.plist`.

### Podpis a notarizace (vyžaduje placený Apple Developer účet)

1. Certifikát „Developer ID Application“ v Klíčence.
2. `export CODESIGN_IDENTITY="Developer ID Application: Jméno (TEAMID)"` – skript podepíše s hardened runtime.
3. Jednorázově: `xcrun notarytool store-credentials NETMONITOR --apple-id … --team-id … --password <app-specific>`
4. `export NOTARY_PROFILE=NETMONITOR && ./scripts/build_macos.sh --dmg` – odešle k notarizaci a „stapluje“.

Bez podpisu: uživatel musí aplikaci poprvé otevřít přes pravé tlačítko → Otevřít.

## Nuitka (alternativa)

```bash
python -m nuitka --standalone --enable-plugin=pyside6 --windows-console-mode=disable \
  --include-data-dir=src/netmonitor/resources=netmonitor/resources packaging/netmonitor_entry.py
```
Nuitka nebyla v rámci projektu ověřena – výchozí a otestovaná cesta je PyInstaller.
