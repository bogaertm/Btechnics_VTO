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

De toegangshistoriek en de codes zijn enkel zichtbaar voor beheerders. Sensoren en het deurenoverzicht tonen namen, nooit codes of badgenummers.

## Beheer van codes en badges

Alle codes en badges op de toestellen zijn beheerbaar via de kaart `btechnics-vto-codes` of via services, enkel voor beheerders.

| Actie | Wat er gebeurt |
|---|---|
| Aanpassen | Naam, code of deuren wijzigen. Bij badges enkel de naam. |
| Blokkeren | Van alle toestellen halen en bewaren, tot een gekozen moment (daarna vanzelf terug) of tot je deblokkeert. |
| Uit dienst | Van alle toestellen halen en bewaren, herstelbaar. |
| Herstellen of deblokkeren | Exact hetzelfde record terug op dezelfde deuren. |
| Definitief verwijderen | Enkel voor codes en badges die uit dienst zijn. |

Een toestel kent geen status of geldigheid voor codes, daarom wordt blokkeren gedaan door het record te verwijderen en het in Home Assistant te bewaren. Voor elke schrijfactie controleert de integratie op het toestel of het record nog exact overeenkomt. Wie wat deed, staat in het overzicht Wijzigingen op de kaart. Het register staat in `.storage/btechnics_vto_registry`.

Een nieuwe badge: hou ze eerst voor een lezer (ze wordt geweigerd), dan verschijnt haar nummer bij Nieuwe badge op de kaart. Ze krijgt dezelfde velden en rechten als de bestaande badges. De kaart ververst elke 30 s.

## Services

```yaml
service: btechnics_vto.add_code
data:
  name: "Jan Peeters"
  code: "123456"
  doors: ["Cafe", "Kammerstraat"]

service: btechnics_vto.block
data:
  id: "..."            # uit list_codes
  until: "2026-10-01 08:00:00"   # weglaten = tot deblokkeren
```

Verder: `add_badge`, `update_code`, `remove_code`, `unblock`, `retire`, `restore`, `forget`, `rename_badge`, `list_codes`, `list_log`, `device_time`, `sync_clock`. Alle services zijn enkel voor beheerders.

## Werking

De integratie spreekt de VTO's aan via dezelfde RPC2 interface als hun eigen webinterface (RecordFinder en RecordUpdater op de tabellen AccessControlCommonPassword, AccessControlCard en AccessControlCardRec). Logboek elke 30 seconden, codes en badges elke 5 minuten.

Een hoofdtoestel bewaart ook een kopie van elke toegang van zijn onderstations (veld VTONumber, bv. Cafe 8001 met Kammerstraat 8002). De integratie bepaalt per deur automatisch het eigen toestelnummer en toont en meldt enkel de eigen toegangen; kopieen blijven in het archief maar worden niet geteld.

Het toestel bewaart het tijdstip van een toegang als zijn eigen kloktijd. De integratie leest elke 5 minuten de klok en de tijdsinstellingen van het toestel en rekent elk tijdstip om naar de echte tijd. Met `btechnics_vto.sync_clock` (enkel beheerders) zet je op afstand zomertijd en tijdsynchronisatie aan; `btechnics_vto.device_time` toont de klok van elk toestel.

Het logboek van een VTO is een ringbuffer van maximaal 1000 records, oudste eerst, waarin het recordnummer positioneel is. Nieuwe toegangen worden daarom herkend op positie en inhoud, nooit op recordnummer. Het archief staat in `btechnics_vto_toegang.db` in de configmap en bewaart 400 dagen.
