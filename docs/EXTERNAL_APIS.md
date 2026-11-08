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

## Aktualizace (výchozí zapnuto, lze vypnout v Nastavení → Obecné)

Kontrola při startu (po 20 s) a každých 6 hodin. Zdroj (pole „Zdroj aktualizací“):

| Zdroj | Dotaz | Poznámka |
|---|---|---|
| GitHub (výchozí `https://github.com/mersl77/netmonitor/releases`) | `GET https://api.github.com/repos/{owner}/{repo}/releases/latest` | funguje jen pro **veřejný** repozitář; bez přihlášení 60 dotazů/h na IP |
| vlastní JSON manifest (např. na zelina.eu) | `GET <url>` | `{"version","url","notes","windows":{"url","sha256"}}` |

Automatická instalace (jen nainstalovaná verze pro Windows): stáhne `NetMonitor-<verze>-setup.exe`, ověří
**SHA-256** (u GitHubu pole `digest` souboru, u manifestu `sha256`) – bez shody nic neinstaluje –, spustí
tichou instalaci Inno Setup (`/VERYSILENT`, bez práv správce) a instalátor aplikaci znovu spustí.
