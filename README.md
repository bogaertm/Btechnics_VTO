# Btechnics VTO

Home Assistant integratie voor centraal beheer van toegangscodes en een jaaroverzicht van alle toegangen op meerdere Dahua VTO buitenposten (getest op DHI-VTO4202FB-P firmware 4.511 en DHI-VTO4202F-P-S2 firmware 4.600).

## Wat het doet

- Per deur: sensor "Laatste unlock" (naam, methode, tijdstip, geopend of geweigerd, de 50 recentste toegangen), aantal codes, aantal badges
- Toegangsarchief van een jaar (de toestellen zelf bewaren maar ongeveer 1000 toegangen)
- Drie dashboardkaarten die automatisch beschikbaar zijn: deuren, toegangshistoriek (zoeken, filteren, per persoon, per maand, CSV) en codes
- Event `btechnics_vto_unlock` op de eventbus bij elke nieuwe toegang, plus een regel in het logboek van Home Assistant
- Services om codes toe te voegen, te wijzigen en te verwijderen op een of meerdere deuren tegelijk

## Een VTO toevoegen

1. Instellingen, Apparaten en diensten, Integratie toevoegen, "Btechnics VTO"
2. Naam van de deur (zo verschijnt ze op het dashboard), IP adres, HTTPS ja of nee, gebruiker en wachtwoord van de VTO
3. De integratie test meteen de verbinding. Een naam of IP adres dat al in gebruik is, wordt geweigerd
4. Klaar: de sensoren worden aangemaakt, de volledige historiek die nog op het toestel staat komt in het archief, en de deur verschijnt vanzelf op alle dashboardkaarten

Een deur verwijderen gaat via dezelfde pagina. De historiek van die deur blijft in het archief bewaard.

## Dashboardkaarten

De kaarten worden door de integratie zelf geladen, er is geen manuele resource nodig.

```yaml
type: custom:btechnics-vto-overzicht     # status van alle deuren
type: custom:btechnics-vto-toegang       # toegangshistoriek, optie period: 1, 7, 30, 90 of 365
type: custom:btechnics-vto-codes         # codes en badges per persoon
```

De toegangshistoriek en de codes zijn enkel zichtbaar voor beheerders.

## Bescherming van bestaande codes

Codes die al op de toestellen stonden vóór de integratie werden geïnstalleerd, zijn alleen lezen. Ze kunnen via de integratie nooit gewijzigd of verwijderd worden. Enkel codes die via `btechnics_vto.add_code` zijn aangemaakt, kunnen nadien via `update_code` en `remove_code` aangepast worden, en elke schrijfactie controleert eerst op het toestel of het record nog exact die code is. De integratie bewaart daarvoor een register in `.storage/btechnics_vto_registry`.

## Services

```yaml
service: btechnics_vto.add_code
data:
  name: "Jan Peeters"
  code: "123456"
  doors: ["Cafe", "Kammerstraat"]
```

`update_code` en `remove_code` werken met de `id` die `add_code` teruggeeft (ook op te vragen via `list_codes`). `list_log` geeft het logboek rechtstreeks van de toestellen.

## Werking

De integratie spreekt de VTO's aan via dezelfde RPC2 interface als hun eigen webinterface (RecordFinder en RecordUpdater op de tabellen AccessControlCommonPassword, AccessControlCard en AccessControlCardRec). Logboek elke 30 seconden, codes en badges elke 5 minuten.

Een hoofdtoestel bewaart ook een kopie van elke toegang van zijn onderstations (veld VTONumber, bv. Cafe 8001 met Kammerstraat 8002). De integratie bepaalt per deur automatisch het eigen toestelnummer en toont en meldt enkel de eigen toegangen; kopieen blijven in het archief maar worden niet geteld.

Het toestel bewaart het tijdstip van een toegang als zijn eigen kloktijd. De integratie leest elke 5 minuten de klok en de tijdsinstellingen van het toestel en rekent elk tijdstip om naar de echte tijd. Met `btechnics_vto.sync_clock` (enkel beheerders) zet je op afstand zomertijd en tijdsynchronisatie aan; `btechnics_vto.device_time` toont de klok van elk toestel.

Het logboek van een VTO is een ringbuffer van maximaal 1000 records, oudste eerst, waarin het recordnummer positioneel is. Nieuwe toegangen worden daarom herkend op positie en inhoud, nooit op recordnummer. Het archief staat in `btechnics_vto_toegang.db` in de configmap en bewaart 400 dagen.
