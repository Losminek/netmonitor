# Stav projektu – verze 1.0.0 (6. 11. 2026)

Vývoj a ověřování proběhlo v **Linuxu (Ubuntu 24.04, Python 3.13, PySide6 6.12)** bez fyzické Wi-Fi a bez
skutečného displeje (Qt offscreen). Windows a macOS kód je napsán, ale **na těchto systémech zatím nebyl spuštěn**.

## Implementováno

| Oblast | Stav |
|---|---|
| Monitorovací jádro: paralelní ICMP, monotonic plánování, žádné překrývání, retries, timeout jako stav | ✔ |
| ICMP: Windows IcmpSendEcho · ICMP socket (DGRAM/RAW) · systémový ping bez shellu | ✔ |
| Statistiky: RTT akt./min/max/průměr, loss % (okno 100 + celkem), OK/FAIL, poslední úspěch, výpadky | ✔ |
| Stav připojení ONLINE/DEGRADED/OFFLINE/UNKNOWN po vrstvách, odolný vůči selhání jednoho cíle | ✔ |
| WAN IPv4 (4 poskytovatelé, fallback, perioda, detekce změny, předchozí IP, čas změny, vypínač) | ✔ |
| ISP/ASN (ipinfo / RIPEstat, cache 24 h, N/A) | ✔ |
| Aktivní rozhraní, typ, LAN IP, brána, rychlost linky, SSID/signál, IPv6, VPN, ruční výběr | ✔ |
| DNS (systemd-resolved stub rozpoznán, NetworkManager, scutil, GetAdaptersAddresses) | ✔ |
| Průhledný widget dle návrhu, HiDPI, 600×850 výchozí, škálování, auto-přizpůsobení řádků | ✔ |
| Režimy Desktop / Always on top, click-through, zámek, průhlednost, velikost, pozice, multi-monitor | ✔ |
| Kontextové menu, tray (barva = stav), notifikace s prodlevou a cooldownem | ✔ |
| Nastavení (6 záložek), živé promítání, editor cílů, pořadí, test, import/export, výchozí | ✔ |
| Historie SQLite: minutové agregace, dávkový zápis, události, retence, migrace, grafy 1h/24h/7d/30d, CSV | ✔ |
| Detekce probuzení a změny sítě, automatické obnovení | ✔ |
| Autostart: HKCU Run / XDG autostart / LaunchAgent | ✔ |
| Single-instance zámek, atomický zápis konfigurace, záloha poškozené konfigurace | ✔ |
| Build: PyInstaller spec, Windows (ZIP + Inno Setup), Linux (tar.gz, .deb, AppImage), macOS (.app, DMG) | ✔ |

## Otestováno (automaticky, Linux)

127 testů (`pytest`): validace IPv4/IPv6/domén, konfigurace (load/save/clamp/migrace/import/export),
parsování výstupu ping (Linux, Windows CZ/EN, macOS, unreachable s rc=0), bezpečné argumenty, úspěšný ping,
timeout, nedostupný host, DNS chyba, packet loss, přechody ONLINE/OFFLINE, paralelismus (pomalý cíl neblokuje),
žádné souběžné požadavky na jeden cíl, bezpečné ukončení bez zbylých vláken, historie (agregace, UPSERT,
buckety, retence, CSV, migrace v1→v2), vyhodnocení stavu, notifikace (prodleva, cooldown), WAN fallback a změny,
parsování `/proc/net/route`, `resolv.conf`, `nmcli`, `netsh wlan` (CZ/EN), `scutil --dns`, layout Windows
struktur (x64 offsety), GUI integrační test (widget, živá změna konfigurace, nastavení, historie, uložení).

Ručně ověřeno v Linuxu: reálný ping 127.0.0.1 (raw socket i systémový ping jako neprivilegovaný uživatel),
reálná WAN IP a ISP přes HTTPS, reálné rozhraní/brána/DNS, PyInstaller build, instalace .deb, `--diagnose`
z buildu, vykreslení widgetu (offscreen) a porovnání s návrhem.

## Neotestováno / zbývá

| Položka | Poznámka |
|---|---|
| **Běh na Windows 10/11** | IcmpSendEcho, GetAdaptersAddresses, netsh, HKCU Run, HWND_BOTTOM – kód hotový, nespuštěný |
| **Běh na macOS** | nespuštěno; podpis/notarizace vyžaduje Apple Developer účet |
| Skutečný kompozitor (průhlednost nad plochou, X11/Wayland chování) | ověřeno jen offscreen render |
| Skutečná Wi-Fi (SSID/signál) | parsování otestováno na vzorcích výstupů |
| Uspání/probuzení v praxi | logika implementována, nebyla fyzicky vyzkoušena |
| IPv6 ping na Windows přes nativní API | zatím přes `ping -6`; Icmp6SendEcho2 = budoucí rozšíření |
| WAN IPv6 | připraveno (seznam poskytovatelů), neaktivní |
| Plný anglický překlad | připravená infrastruktura + překlad widgetu/menu; dialogy česky |
| Nuitka build | zdokumentováno, neověřeno (výchozí je PyInstaller) |
