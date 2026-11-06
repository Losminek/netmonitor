# Externí služby

Aplikace funguje i **zcela bez internetových API** – vypínač *Nastavení → Síť → Povolit externí dotazy*.
Pak se WAN IP a ISP nezobrazují (`vypnuto` / `N/A`); vše ostatní (LAN, DNS, ping, historie) běží dál.

Odesílá se jen prostý HTTPS GET s hlavičkou `User-Agent: NetMonitor/<verze>`. Žádné identifikátory,
žádná historie, žádná telemetrie. Nejsou potřeba API klíče ani registrace.

## WAN IPv4 (výchozí interval 5 min, při výpadku ICMP max. 1× za minutu)

Zkouší se v pořadí, první platná IPv4 vyhrává:

| # | URL | Provozovatel |
|---|---|---|
| 1 | `https://api.ipify.org` | ipify |
| 2 | `https://ipv4.icanhazip.com` | Cloudflare (icanhazip) |
| 3 | `https://checkip.amazonaws.com` | AWS |
| 4 | `https://ifconfig.me/ip` | ifconfig.me |

Připraveno pro IPv6 (`api6.ipify.org`, `ipv6.icanhazip.com`) – zatím se nepoužívá.

## ISP / ASN (cache 24 h na IP adresu, dotaz jen při změně WAN IP nebo vypršení)

| Volba | URL | Poznámka |
|---|---|---|
| ipinfo (výchozí) | `https://ipinfo.io/<ip>/json` → pole `org` | bez tokenu s omezeným limitem; formát „AS42908 …“ |
| RIPEstat | `https://stat.ripe.net/data/network-info/…` + `…/as-overview/…` | oficiální data RIPE NCC, bez limitu pro běžné použití |

Selže-li zvolený zdroj, automaticky se použije druhý. Bez dostupného údaje se zobrazí `N/A`.

## Kontrola nové verze (volitelné, výchozí vypnuto)

Pouze pokud uživatel vyplní vlastní URL (HTTPS JSON `{"version": "1.0.1", "url": "…"}`).
