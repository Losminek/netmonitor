# Rozdíly platforem a jejich omezení

## ICMP (ping)

| OS | Implementace | Oprávnění |
|---|---|---|
| Windows | `IcmpSendEcho` (iphlpapi.dll) přes ctypes | běžný uživatel, bez admin práv; IPv6 zatím přes `ping -6` |
| Linux | ICMP `SOCK_DGRAM` socket | potřebuje `net.ipv4.ping_group_range` zahrnující GID uživatele (Ubuntu/Fedora výchozí ano). Jinak automaticky systémový `ping` (setuid/capability) |
| macOS | ICMP `SOCK_DGRAM` socket | běžný uživatel |

Výběr probíhá automaticky při startu (ověřením pingu na 127.0.0.1); použitá implementace je vidět
v *Nastavení → Síť → Ping*. Timeout je vždy samostatný stav, nikdy „latence 30 000 ms“.

Windows API vrací RTT s rozlišením 1 ms; aplikace jej zpřesňuje vlastním měřením (perf_counter),
pokud spadá do intervalu ⟨hlášená, hlášená + 1,5 ms⟩.

## Režim „Na ploše“ (A) vs. „Vždy navrchu“ (B)

| Prostředí | A – Desktop widget | B – Always on top | Pozice okna |
|---|---|---|---|
| Windows 10/11 | ✔ `HWND_BOTTOM` (znovu při každé aktivaci). **Win+D dočasně skryje widget** – omezení Windows (Rainmeter to obchází hookem do Exploreru, což je křehké) | ✔ | ✔ |
| Linux X11 | ✔ `_NET_WM_STATE_BELOW` – závisí na správci oken (GNOME/KDE/Xfce ano) | ✔ | ✔ |
| Linux Wayland | ✖ protokol klientům neumožňuje vrstvu ani pozici | ✖ (kromě KDE pravidel okna) | ✖ |
| macOS | ✔ nejnižší úroveň okna, kterou Qt nabízí; nad ikonami plochy být nemusí | ✔ | ✔ |

**Wayland fallback:** pokud je k dispozici XWayland (`DISPLAY` je nastaven), aplikace se automaticky spustí
přes něj (`QT_QPA_PLATFORM=xcb`) – pak funguje pozice i režimy. Vypnout: *Nastavení → Síť → Na Waylandu použít
XWayland*. Bez XWaylandu běží jako běžné okno a v nastavení je zobrazeno vysvětlení.

**Click-through** (ignorování myši) je povolen jen tam, kde existuje ikona v oznamovací oblasti – jinak by ho
nebylo jak vypnout. Vypíná se z menu ikony.

## Síťové údaje

| Údaj | Windows | Linux | macOS |
|---|---|---|---|
| Aktivní rozhraní | UDP `connect()` trik (bez odeslání paketu) + psutil | totéž | totéž |
| Brána | GetAdaptersAddresses | `/proc/net/route` (nejnižší metrika) | `route -n get default` |
| DNS | GetAdaptersAddresses (per adaptér) | `resolvectl dns <if>` → `nmcli` → `/run/systemd/resolve/resolv.conf` → `/etc/resolv.conf` | `scutil --dns` (scoped resolver rozhraní) |
| Typ (Wi-Fi/Ethernet/VPN) | IfType + popis adaptéru | `/sys/class/net/<if>` | `networksetup -listallhardwareports` |
| Rychlost linky | psutil / ReceiveLinkSpeed | psutil / sysfs / `iw` bitrate | psutil |
| SSID, signál | `netsh wlan show interfaces` | `nmcli` → `iw` → `/proc/net/wireless` | `ipconfig getsummary` |

**Rychlost linky ≠ rychlost internetu.** Zobrazuje se vyjednaná rychlost adaptéru (u Wi-Fi aktuální PHY rate).

**Stub resolver:** na Linuxu se `127.0.0.53` nikdy nevydává za upstream. Je-li jen stub, zobrazí se upstream
servery systemd-resolved; jinak stub s poznámkou „(lokální stub)“ a vysvětlením v tooltipu.

**SSID a poloha:**
- Windows 11 24H2+: `netsh wlan` vrací SSID jen pokud je povolena služba polohy pro desktopové aplikace
  (*Nastavení → Ochrana osobních údajů a zabezpečení → Poloha*). Jinak `N/A` + vysvětlení v tooltipu.
- macOS 14.4+: SSID je bez oprávnění k poloze skryté („redacted“) → `N/A`.

## Uspání / probuzení, změny sítě

Plánovač porovnává skok reálného času s monotónními hodinami; po probuzení okamžitě přeplánuje všechna měření,
vymaže DNS cache a znovu načte síť i WAN IP. Změna rozhraní, IP, brány, DNS nebo SSID má stejný efekt.
