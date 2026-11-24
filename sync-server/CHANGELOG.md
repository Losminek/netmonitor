# NetMonitor Sync – změny

## 1.2.1
- Zapnutí dvoufázového ověření vyžaduje aktuální heslo.
- Tolerance pro ztracenou odpověď při obnově přihlášení platí pro každý token jen jednou.
- Obnovovací tokeny jsou podepsané serverem; relaci ukončí jen opakované použití tokenu, který server vydal.
- `FORWARDED_ALLOW_IPS` ve výchozím stavu nevěří žádné síti; nastavte IP kontejneru NPM (a rozsahy Cloudflare).
- Nový volitelný `TOTP_KEY` pro tajemství 2FA. Nedešifrovatelné tajemství vrací srozumitelnou chybu 503 místo 500.

## 1.2.0
- Správa: přehled zařízení účtu a odhlášení vybraného zařízení. Ukončené relace se po 30 dnech mažou.

## 1.1.0
- Chybná přihlášení se počítají na dvojici IP + e-mail; odpověď je stejná pro existující i neexistující účet.
- Dvoufázové ověření správce se zapíná jen na serveru (`python -m app.cli totp-setup`, QR kód v terminálu);
  `create-admin` jej nastaví rovnou.
- Správa vyžaduje kód z autentikátoru zadaný za posledních 12 hodin (`ADMIN_MFA_HOURS`).
- Zapnutí 2FA běžným uživatelem ukončí všechny jeho dosavadní relace.
- Obnovovací tokeny nesou id relace: použití kteréhokoli staršího tokenu relaci ukončí; krátce po výměně
  se toleruje opakování (ztracená odpověď). Výměna tokenu i spotřeba kódu 2FA jsou atomické.
- Tajemství 2FA jsou v databázi šifrovaná (stávající se zašifrují při startu).
- Smazání profilu s `?revision=` nesmaže novější změnu; PUT vrací přesně zapsanou revizi;
  limit počtu profilů platí i při souběžných požadavcích.
- Databáze se při startu sama doplní o nové sloupce.
- Image `latest` a číslo verze se publikují jen z větve main; compose používá pevnou verzi.

## 1.0.0
- První vydání: účty, licenční klíče, profily, správa.
