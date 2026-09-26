"""Sensoren per deur: laatste unlock, aantal codes, aantal badges."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coords = hass.data[DOMAIN][entry.entry_id]["coords"]
    ents = []
    for c in coords.values():
        ents += [LastUnlockSensor(c), CountSensor(c, "codes", "Codes", "mdi:dialpad"), CountSensor(c, "cards", "Badges", "mdi:badge-account-horizontal")]
    async_add_entities(ents)


class _Base(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator):
        super().__init__(coordinator)
        c = coordinator
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, c.door_id)}, name=f"VTO {c.door_name}", manufacturer="Dahua",
            model=c.info.get("type"), sw_version=c.info.get("version"), serial_number=c.info.get("serial"),
        )


class LastUnlockSensor(_Base):
    # De lijst met recente toegangen en het kaartnummer niet in de databank-historiek bewaren:
    # ze staan altijd live op het toestel en zouden de databank nodeloos doen groeien.
    _unrecorded_attributes = frozenset({"recent", "card"})

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.door_id}_last_unlock"
        self._attr_name = "Laatste unlock"

    @property
    def native_value(self):
        u = self.coordinator.last_unlock
        return u["name"][:255] if u else None

    @property
    def icon(self):
        u = self.coordinator.last_unlock
        return "mdi:door-closed-lock" if u and not u["opened"] else "mdi:door-open"

    @property
    def extra_state_attributes(self):
        # Zonder kaartnummers: attributen zijn leesbaar voor elke gebruiker, ook zonder beheerdersrechten.
        u = {k: v for k, v in (self.coordinator.last_unlock or {}).items() if k != "card"}
        return {**u, "recent": [{k: v for k, v in r.items() if k != "card"} for r in self.coordinator.recent]}


class CountSensor(_Base):
    _attr_state_class = "measurement"
    # Enkel de namen in het attribuut "lijst": codes en kaartnummers zijn voor beheerders
    # (via de kaart of list_codes), attributen zijn leesbaar voor elke gebruiker.
    _unrecorded_attributes = frozenset({"lijst"})

    def __init__(self, coordinator, key, name, icon):
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.door_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._key)

    @property
    def extra_state_attributes(self):
        # Volledige lijst als attribuut, zodat een dashboardkaart (markdown/template)
        # alle codes of kaarten van deze deur kan tonen zonder aparte service-aanroep.
        if self._key == "codes":
            lijst = [{"naam": (r.get("UserID") or "").strip() or "?"} for r in self.coordinator.codes]
        else:
            lijst = [{"naam": r.get("CardName") or r.get("UserID") or "?"} for r in self.coordinator.cards]
        lijst.sort(key=lambda x: str(x["naam"]).lower())
        return {"lijst": lijst}
