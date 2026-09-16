# Btechnics VTO

Home Assistant integratie voor centraal beheer van toegangscodes en het logboek van meerdere Dahua VTO buitenposten (getest op DHI-VTO4202FB-P firmware 4.511 en DHI-VTO4202F-P-S2 firmware 4.600).

## Wat het doet

- Per deur: sensor "Laatste unlock" (naam, methode, tijdstip, geopend of geweigerd als attributen), aantal codes, aantal badges
- Event `btechnics_vto_unlock` op de eventbus bij elke nieuwe logregel
- Services om codes toe te voegen, te wijzigen en te verwijderen op een of meerdere deuren tegelijk

## Bescherming van bestaande codes

Codes die al op de toestellen stonden vóór de integratie werden geïnstalleerd, zijn alleen lezen. Ze kunnen via de integratie nooit gewijzigd of verwijderd worden. Enkel codes die via `btechnics_vto.add_code` zijn aangemaakt, kunnen nadien via `update_code` en `remove_code` aangepast worden. De integratie bewaart daarvoor een register in `.storage/btechnics_vto_registry` en vergrendelt bij eerste start alle bestaande recordnummers.

## Installatie

1. Map `custom_components/btechnics_vto` in de HA config zetten (of via HACS als custom repository)
2. HA herstarten
3. Instellingen, Apparaten en diensten, Integratie toevoegen, "Btechnics VTO"
4. Per deur: naam, IP, HTTPS ja of nee, gebruiker, wachtwoord. Meerdere deuren na elkaar toevoegen

## Services

```yaml
service: btechnics_vto.add_code
data:
  name: "Jan Peeters"
  code: "123456"
  doors: ["Café", "Kammerstraat"]
```

`update_code` en `remove_code` werken met de `id` die `add_code` teruggeeft (ook op te vragen via `list_codes`).

## Werking

De integratie spreekt de VTO's aan via dezelfde RPC2 interface als hun eigen webinterface (RecordFinder en RecordUpdater op de tabellen AccessControlCommonPassword, AccessControlCard en AccessControlCardRec). Logboek elke 30 seconden, codes en badges elke 5 minuten.
