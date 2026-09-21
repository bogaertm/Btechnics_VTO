"""Sensoren per deur: laatste unlock, aantal codes, aantal badges."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import DeviceInfo
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
    _attr_icon = "mdi:door-open"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.door_id}_last_unlock"
        self._attr_name = "Laatste unlock"

    @property
    def native_value(self):
        u = self.coordinator.last_unlock
        return u["name"] if u else None

    @property
    def extra_state_attributes(self):
        return self.coordinator.last_unlock or {}


class CountSensor(_Base):
    _attr_state_class = "measurement"

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
            lijst = [
                {"naam": (r.get("UserID") or "").strip() or "?", "code": r.get("CommonPassword", "")}
                for r in self.coordinator.codes
            ]
        else:
            lijst = [
                {"naam": r.get("CardName") or r.get("UserID") or "?", "kaart": r.get("CardNo", "")}
                for r in self.coordinator.cards
            ]
        lijst.sort(key=lambda x: x["naam"].lower())
        return {"lijst": lijst}
