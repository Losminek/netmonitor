# Změny

## 1.3.3
- Oprava skutečné příčiny pádu po traceroute: okno traceroute nemělo trvalý odkaz a po doběhnutí trasy ho Python rušil mimo hlavní vlákno, což shodilo celou aplikaci

## 1.3.2
- Oprava: zapnutí/vypnutí cíle zaškrtávátkem v Nastavení mohlo shodit aplikaci (strom se překresloval uprostřed zpracování kliknutí)

## 1.3.1
- Oprava: traceroute na Windows mohl po několika skocích shodit celou aplikaci. Nově běží přes systémový `tracert` v samostatném procesu.
- Větší rezerva pro odpovědi ICMP na Windows (chybové odpovědi routerů s citovaným paketem)
- Při případném tvrdém pádu se zapíše `logs/crash.log` (výpis všech vláken) pro snadnou diagnostiku
- Číslo verze s odkazem na zelina.eu/projekty přesunuto do horního pravého „tabu“ rámečku widgetu

## 1.3.0
- Wake-on-LAN: pravým tlačítkem na cíl ve widgetu → Probudit; MAC adresa u cíle (tlačítko „Zjistit“ ji přečte z ARP)
- Traceroute k cíli s živou tabulkou skoků
- Test rychlosti internetu (stahování, nahrávání, odezva, jitter) – menu widgetu i detail cíle
- Pravé tlačítko na řádku cíle: detail a graf, testovat nyní, traceroute, probudit
- Výklenek s číslem verze ve spodní hraně widgetu – odkaz na zelina.eu/projekty

## 1.2.0
- Automatické aktualizace: odznak nové verze ve widgetu a v ikoně lišty, jedním klikem stažení (ověření SHA-256), tichá instalace a restart; volitelně plně automaticky
- Výchozí neprůhledné pozadí, posuvník průhlednosti pozadí 0–100 % (Nastavení → Vzhled i pravé tlačítko)
- O aplikaci s odkazem na zelina.eu/projekty

## 1.1.0
- Sekce ping cílů (např. DNS, DOMÉNY, ZAŘÍZENÍ) – nadpisy ve widgetu i v nastavení
- Volné řazení cílů přetažením myší nebo šipkami, nový cíl se vkládá za vybraný řádek
- Nastavení IP adresy síťové karty kliknutím na blok síťové karty (DHCP / pevná IP / DNS, profily, kontrola obsazené IP)

## 1.0.1
- Lepší čitelnost na světlé tapetě, zmenšování widgetu až na 260 × 340, zkracování dlouhých textů
- Oprava rychlosti linky > 4,29 Gb/s na Windows, UTF-8 BOM pro skripty Windows
