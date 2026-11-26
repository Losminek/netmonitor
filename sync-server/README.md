# NetMonitor Sync – server pro účty, licenční klíče a profily

Samostatný server (FastAPI + PostgreSQL), se kterým NetMonitor 1.4 synchronizuje profily.
Image: `ghcr.io/mersl77/netmonitor-sync` (sestavuje GitHub Actions z této složky).

**Stav:** verze 1.1.0, nasazeno na `sync.zelina.eu`.

## Co server hlídá

| Ochrana | Jak |
|---|---|
| Registrace | jen s platným licenčním klíčem; `REGISTRATION_ENABLED=false` ji vypne úplně |
| Licenční klíč | 100 bitů náhody, v databázi jen otisk SHA-256, počet použití, volitelná platnost, zneplatnění |
| Zaplavení požadavky | limit na IP: 240 požadavků/min celkem, 10 přihlášení/5 min, 5 registrací/hod (HTTP 429) |
| Hádání hesla | Argon2id; po 5 chybných pokusech z jedné IP na jeden e-mail pauza 15 min (stejná odpověď pro existující i neexistující účet) |
| Správce | správa jen s kódem z autentikátoru zadaným za posledních 12 h (`ADMIN_MFA_HOURS`); 2FA správce se zapíná jen na serveru |
| Tajemství 2FA | v databázi šifrovaná klíčem odvozeným z `TOTP_KEY` (není-li zadaný, z `JWT_SECRET`) |
| Relace | přístupový token 15 min, obnovovací token se při každém použití vymění; použití staršího tokenu relaci ukončí (krátká tolerance pro ztracenou odpověď) |
| Data | profil vidí jen jeho účet (ani správce ne); max. 20 profilů na účet, 512 KiB na profil |
| Souběh | zápis i smazání profilu kontrolují revizi; konflikt = HTTP 409, nic se nepřepíše |

## Nasazení přes SSH (Docker Compose)

Takto je nasazen `sync.zelina.eu` (složka `/opt/netmonitor-sync-v2`):

```sh
( set -e
mkdir -p /opt/netmonitor-sync-v2 && cd /opt/netmonitor-sync-v2
curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/mersl77/netmonitor/main/sync-server/docker-compose.yml
if [ ! -f .env ]; then printf 'POSTGRES_PASSWORD=%s\nJWT_SECRET=%s\nFORWARDED_ALLOW_IPS=%s\n' "$(openssl rand -hex 24)" "$(openssl rand -hex 32)" "$(docker inspect -f '{{with index .NetworkSettings.Networks "nginx-proxy-manager_default"}}{{.IPAddress}}{{end}}' nginx-proxy-manager)" > .env; fi
chmod 600 .env
docker compose up -d --wait
curl -s http://127.0.0.1:8766/health )
docker compose exec api python -m app.cli create-admin      # zeptá se na e-mail, heslo a zapne 2FA
```

Aktualizace serveru: stáhnout nový `docker-compose.yml` (obsahuje pevnou verzi image) a
`docker compose up -d --wait`. Databáze se při startu sama doplní o nové sloupce.
Od 1.2.2 je `FORWARDED_ALLOW_IPS` povinný: bez něj stack nenaběhne (viz níže).

**`FORWARDED_ALLOW_IPS` (povinné).** Bez reverzní proxy zadejte `127.0.0.1`.
Za reverzní proxy musí obsahovat IP kontejneru NPM a za Cloudflare
i jeho rozsahy (<https://www.cloudflare.com/ips/>), čárkami oddělené. Ne celou síť `172.16.0.0/12`:
hlavičku `X-Forwarded-For` by pak mohl podvrhnout kterýkoli kontejner v síti i proces na hostu
(přes publikovaný port). IP kontejneru NPM zjistíte příkazem
`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' <kontejner-npm>`;
aby se po restartu neměnila, dejte NPM v jeho stacku pevnou adresu (`ipv4_address`). Server pak bere IP klienta zprava první
nedůvěryhodnou adresu z `X-Forwarded-For`, takže ji podvržená hlavička neovlivní.
Proxy host v NPM by měl mít access list povolující jen Cloudflare a místní síť.

## Nasazení v Portaineru

1. **Stacks → Add stack → Web editor**, název `netmonitor-sync`, vložit obsah `docker-compose.yml`.
2. **Environment variables**:

   | Název | Hodnota |
   |---|---|
   | `POSTGRES_PASSWORD` | náhodné heslo, např. výstup `openssl rand -hex 24` |
   | `JWT_SECRET` | min. 32 znaků, např. výstup `openssl rand -hex 32` |
   | `PROXY_NETWORK` | Docker síť reverzní proxy (výchozí `nginx-proxy-manager_default`) |
   | `API_BIND` | volitelné, výchozí `127.0.0.1:8766` |
   | `FORWARDED_ALLOW_IPS` | IP kontejneru NPM, za Cloudflare i jeho rozsahy (viz výše) |
   | `TOTP_KEY` | volitelné, klíč pro tajemství 2FA (min. 32 znaků); výchozí je `JWT_SECRET` |

3. **Deploy the stack.** Kontrola na hostiteli: `curl -s http://127.0.0.1:8766/health`.
4. V Nginx Proxy Manageru vytvořit Proxy Host `sync.zelina.eu` → `netmonitor-sync-api`, port `8000`
   (schéma http), zapnout SSL (Let's Encrypt, Force SSL).
5. **První správce** – Portainer → kontejner `api` → Console (`/bin/sh`):

   ```sh
   python -m app.cli create-admin
   ```

   E-mail a heslo se zadávají interaktivně (heslo skrytě, nikam se neloguje).
6. V NetMonitoru: Nastavení → Účet a synchronizace → přihlásit se → karta **Správa** →
   zapnout dvoufázové ověření. Teprve potom jde vytvářet licenční klíče.

Stack používá vlastní svazek `netmonitor-sync_db-data`. Rozpracovaný server z bety 1.4.0b4
(`/opt/netmonitor-sync`) má jiné schéma tabulek – nový stack nasazujte s čistou databází a starý vypněte.

## Správa z příkazové řádky (Console kontejneru `api`)

```sh
python -m app.cli create-admin                 # nový správce / povýšení existujícího účtu (+ 2FA)
python -m app.cli totp-setup spravce@domena.cz # zapnutí / nové nastavení 2FA (QR kód v terminálu)
python -m app.cli create-key "Pavel – kancelář" --days 30
python -m app.cli list-users
python -m app.cli reset-password uzivatel@domena.cz
python -m app.cli reset-totp spravce@domena.cz # ztracený telefon správce (hned nabídne nové 2FA)
```

## API

| Metoda | Cesta | Význam |
|---|---|---|
| GET | `/health` | stav serveru |
| POST | `/auth/register` | `{email, password, license_key}` |
| POST | `/auth/login` | `{email, password, totp_code?}` → přístupový a obnovovací token |
| POST | `/auth/refresh`, `/auth/logout`, `/auth/password` | obnovení, odhlášení, změna hesla |
| POST | `/auth/totp/setup`, `/auth/totp/enable` | zapnutí 2FA běžným uživatelem (správce: jen `app.cli totp-setup`) |
| GET/POST | `/profiles` | seznam / nový profil |
| GET/PUT/DELETE | `/profiles/{id}` | čtení / zápis s revizí (i přejmenování) / smazání (`?revision=N`) |
| GET/POST | `/admin/keys`, `/admin/keys/{id}/revoke` | licenční klíče (správce s 2FA) |
| GET/POST | `/admin/users`, `/admin/users/{id}/disable\|enable` | účty (správce s 2FA) |

Chyby mají tvar `{"detail": {"code": "...", "message": "..."}}`.

## Vývoj a testy

```sh
pip install -r requirements-dev.txt
python -m pytest -q tests
JWT_SECRET=$(openssl rand -hex 32) DATABASE_URL=sqlite:///dev.sqlite3 uvicorn app.asgi:app --port 8766
```

## Známá omezení

- Limit požadavků je v paměti jednoho procesu (po restartu kontejneru se nuluje).
- Schéma vzniká přes `create_all` a nové sloupce doplňuje `upgrade_schema` při startu; Alembic zatím není.
- Změna `JWT_SECRET` zneplatní jen přístupové tokeny (15 min); aplikace se obnovovacím tokenem sama
  přihlásí znovu, relace zůstávají. Odhlásit uživatele lze příkazem `reset-password` (ukončí jeho
  relace), zablokováním účtu nebo odhlášením zařízení v kartě Správa.
- Tajemství 2FA jsou šifrovaná klíčem `TOTP_KEY`, a když není zadaný, klíčem `JWT_SECRET`. Server je
  čte nejdřív `TOTP_KEY` a pak `JWT_SECRET` a při přihlášení je přešifruje na `TOTP_KEY`. Přidat nový
  `TOTP_KEY` k stávající instalaci je tedy bezpečné, ale **`JWT_SECRET` neměňte, dokud se všichni
  uživatelé s 2FA aspoň jednou nepřihlásí** – jinak dostanou 503 `totp_unavailable` a je nutné
  `reset-totp`. Nejjednodušší je při změně `JWT_SECRET` nastavit `TOTP_KEY` na jeho původní hodnotu.
- Obnova zapomenutého hesla e-mailem není – heslo resetuje správce příkazem `reset-password`.
- Zálohování databáze (pg_dump svazku) si řeší správce serveru.
