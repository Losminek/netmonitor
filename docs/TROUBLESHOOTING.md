# Diagnostika problémů

## 1. Textová diagnostika

```
NetMonitor.exe --diagnose          (Windows, přenosná verze – výstup jen při spuštění z konzole s přesměrováním)
netmonitor --diagnose              (instalace ze zdrojů)
python -m netmonitor --diagnose
```

Vypíše JSON: aktivní rozhraní, brána, DNS (+ zdroj), Wi-Fi, použitá ICMP implementace, výsledek pingu všech cílů,
WAN IP a ISP. Pozn.: GUI build (`console=False`) na Windows nemá konzoli – pro diagnostiku spusťte
`NetMonitor.exe --diagnose > diag.json` v PowerShellu, nebo použijte verzi ze zdrojů.

## 2. Logy

Rotující log `netmonitor.log` (max. 3 × 1 MB): viz tabulka v README. `--debug` zapne podrobné logování.
Při tvrdém pádu (chyba v nativní knihovně) se do stejné složky zapíše `crash.log` s výpisem všech vláken –
přiložte ho k hlášení chyby.

## Časté situace

| Příznak | Příčina / řešení |
|---|---|
| Všechny cíle TIMEOUT, LAN = DEGRADED „ICMP může být blokováno“ | Firewall/VPN blokuje ICMP, HTTPS funguje. Ověřte `ping 8.8.8.8` v terminálu. |
| Linux: použita implementace `system-ping` | `ping_group_range` nepovoluje ICMP sockety: `sudo sysctl -w net.ipv4.ping_group_range='0 2147483647'` (trvale do `/etc/sysctl.d/`). Funguje i bez toho. |
| SSID = N/A (Windows 11) | Povolte polohu pro desktopové aplikace (viz PLATFORM_NOTES). |
| DNS ukazuje `127.0.0.53 (lokální stub)` | systemd-resolved bez dostupných upstream údajů; `resolvectl status` ukáže skutečné servery. |
| Widget po Win+D zmizí | Omezení Windows v režimu „Na ploše“; klik na ikonu v oznamovací oblasti jej vrátí. Alternativa: režim „Vždy navrchu“ s nižší průhledností a click-through. |
| Widget nejde přesunout | Zamčená pozice (pravé tlačítko → Uzamknout pozici), nebo Wayland bez XWaylandu. |
| Click-through zapnut a widget nereaguje | Vypněte jej z menu ikony v oznamovací oblasti. |
| Widget mimo obrazovku (odpojený monitor) | Automaticky se vrátí na hlavní monitor; ručně `--reset-position`. |
| „NetMonitor už běží“ | Single-instance zámek `netmonitor.lock` v datové složce; po pádu se sám uvolní. |
| ISP = N/A | Vypnutá detekce/externí dotazy, nebo oba zdroje nedostupné (log: „ISP lookup … selhal“). |
| Windows: „Systém Windows ochránil váš počítač“ | Instalátor není podepsaný certifikátem; *Další informace → Přesto spustit*. Otisk SHA-256 souboru je v Releases. |
| Traceroute nic neukazuje / „tracert nebyl nalezen“ | Windows: `C:\Windows\System32\tracert.exe`; Linux: `sudo apt install traceroute`. |
| Wake-on-LAN zařízení neprobudí | WoL musí být zapnuté v BIOSu/UEFI i v ovladači karty; zařízení v jiné podsíti potřebuje „Broadcast pro WoL“ nebo předání broadcastu na routeru. |
| Poškozená/nečitelná konfigurace nebo WAN cache | Původní soubor se odloží jako `config.json.corrupt-<čas>-<id>` / `wan_state.json.corrupt-<čas>-<id>` a zaloguje se varování. Použijí se výchozí hodnoty / prázdná cache. Pokud odložení selže, originál se zachová a jeho přepsání se zablokuje. Obnovu nastavení lze provést ze zachované zálohy po opravě JSONu. |

## Reset

Smažte (nebo přejmenujte) `config.json`. Historie: `history.sqlite3` v datové složce.
