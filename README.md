# NetMonitor

Nativní desktopový **průhledný widget** pro Windows 10/11, Linux a macOS, který v reálném čase zobrazuje
informace o síťovém připojení (WAN IP, ISP, adaptér, LAN IP, brána, rychlost linky, SSID, DNS) a paralelně
monitoruje dostupnost uživatelsky definovaných IP adres a domén přes ICMP.

- Python 3.12+ · PySide6 (Qt 6) · psutil · SQLite · JSON konfigurace
- Žádný Electron, server, cloud, účet, telemetrie ani placené API
- Všechny hodnoty ve widgetu pochází ze skutečného měření/detekce

![rozložení](docs/layout.txt)

## Rychlý start (ze zdrojů)

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt
python -m pip install -e .
netmonitor              # nebo:  python -m netmonitor
```

Užitečné přepínače:

| Přepínač | Význam |
|---|---|
| `--minimized` | start skrytě (jen ikona v oznamovací oblasti) |
| `--diagnose` | textová diagnostika (síť, DNS, WAN, ISP, ping všech cílů) do JSON a konec |
| `--reset-position` | vrátí widget do pravého horního rohu |
| `--debug` | podrobný log |
| `--config CESTA` | jiný konfigurační soubor |

## Sestavení

| OS | Příkaz | Výstup |
|---|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1` | `dist\NetMonitor\NetMonitor.exe`, ZIP, volitelně instalátor (Inno Setup 6) |
| Linux | `./scripts/build_linux.sh [--deb] [--appimage]` | `dist/NetMonitor/`, tar.gz, volitelně .deb / AppImage |
| macOS | `./scripts/build_macos.sh [--dmg]` | `dist/NetMonitor.app`, volitelně DMG |

PyInstaller neumí cross-kompilaci – každý OS se sestavuje na sobě. Podrobnosti: [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md).

## Testy

```bash
pip install -r requirements-dev.txt
pytest            # Linux bez displeje: QT_QPA_PLATFORM=offscreen pytest
```

## Ovládání

- **Levé tlačítko + tah** – přesun (není-li pozice uzamčena)
- **Klik na řádek cíle** – detail s grafem (1 h / 24 h / 7 d / 30 d), statistikami a výpadky
- **Pravé tlačítko** – menu: Nastavení, Historie, režim (Na ploše / Vždy navrchu), zámek, click-through,
  průhlednost, velikost, skrytí do oznamovací oblasti, ukončení
- **Úchyt vpravo dole** – změna velikosti
- **Dvojklik na nadpis** – Nastavení
- **Ikona v oznamovací oblasti** – barva = stav připojení; klik = zobrazit/skrýt; menu (zde lze vypnout click-through)

## Kde jsou data

| | Windows | Linux | macOS |
|---|---|---|---|
| Konfigurace | `%APPDATA%\NetMonitor\config.json` | `~/.config/netmonitor/config.json` | `~/Library/Application Support/NetMonitor/` |
| Historie (SQLite), stav WAN | `%LOCALAPPDATA%\NetMonitor\` | `~/.local/share/netmonitor/` | `~/Library/Application Support/NetMonitor/` |
| Logy | `%LOCALAPPDATA%\NetMonitor\logs\` | `~/.local/share/netmonitor/logs/` | `~/Library/Logs/NetMonitor/` |

Proměnná `NETMONITOR_HOME` přesměruje vše do jedné složky (testy, přenosný režim).

## Dokumentace

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) – moduly, vlákna, tok dat
- [docs/PLATFORM_NOTES.md](docs/PLATFORM_NOTES.md) – rozdíly Windows / X11 / Wayland / macOS, ICMP oprávnění
- [docs/EXTERNAL_APIS.md](docs/EXTERNAL_APIS.md) – jaké externí služby a proč (vypnutelné)
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) – diagnostika problémů
- [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) – buildy, instalátory, podpis a notarizace
- [docs/STATUS.md](docs/STATUS.md) – co je implementováno, otestováno a co zbývá

## Licence

MIT (kód). Přibalená písma Michroma a Exo 2: SIL Open Font License 1.1 (`src/netmonitor/resources/fonts/OFL-*.txt`).
