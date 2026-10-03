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
        # zonder naam: sensoren zijn zichtbaar voor elke gebruiker; namen enkel voor beheerders in de kaarten
        u = self.coordinator.last_unlock
        if not u:
            return None
        m = str(u.get("method") or "")
        how = "op afstand" if m.startswith("op afstand") else f"via {m}" if m else ""
        return f"{'Geopend' if u.get('opened') else 'Geweigerd'} {how}".strip()[:255]

    @property
    def icon(self):
        u = self.coordinator.last_unlock
        return "mdi:door-closed-lock" if u and not u["opened"] else "mdi:door-open"

    @property
    def extra_state_attributes(self):
        # Zonder kaartnummers: attributen zijn leesbaar voor elke gebruiker, ook zonder beheerdersrechten.
        # enkel de laatste toegang zonder naam of kaartnummer; geen lijst met recente toegangen en uren
        # (de kaarten halen die enkel voor beheerders op)
        hide = ("card", "name", "user", "recent")
        return {k: v for k, v in (self.coordinator.last_unlock or {}).items() if k not in hide}


class CountSensor(_Base):
    _attr_state_class = "measurement"
    # Enkel het aantal: namen, codes en kaartnummers zijn voor beheerders (via de kaarten of list_codes),
    # attributen zijn leesbaar voor elke gebruiker.

    def __init__(self, coordinator, key, name, icon):
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.door_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._key)
