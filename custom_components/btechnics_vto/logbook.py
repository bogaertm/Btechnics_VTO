"""Toont unlock-events overzichtelijk in het Home Assistant Logboek, automatisch per dag gegroepeerd."""
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, EVENT_UNLOCK


@callback
def async_describe_events(hass: HomeAssistant, async_describe_event):
    registry = er.async_get(hass)

    @callback
    def async_describe_unlock_event(event):
        data = event.data
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{data.get('door_id')}_last_unlock")
        status = "geopend" if data.get("opened") else "geweigerd"
        return {
            "name": f"VTO {data.get('door', '?')}",
            "message": f"{data.get('name', '?')} via {data.get('method', '?')} ({status})",
            "entity_id": entity_id,
        }

    async_describe_event(DOMAIN, EVENT_UNLOCK, async_describe_unlock_event)

