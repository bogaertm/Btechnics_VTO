"""Coordinator per deur: pollt logboek (30 s) en codes/badges (5 min), stuurt events."""
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import VTOClient, VTOError
from .const import CODES_INTERVAL, DOMAIN, EVENT_UNLOCK, LOG_FETCH_COUNT, LOG_INTERVAL, METHODS

_LOGGER = logging.getLogger(__name__)

class DoorCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, door_id: str, door_name: str, client: VTOClient, registry):
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {door_name}", update_interval=timedelta(seconds=LOG_INTERVAL))
        self.door_id = door_id
        self.door_name = door_name
        self.client = client
        self.registry = registry
        self.info = {}
        self.codes = []
        self.cards = []
        self.last_unlock = None
        self._last_codes_fetch = None
        self._entity_id = None

    def _run(self, fn, *a):
        """Sync API-aanroep in executor, met login per beurt (VTO houdt sessies kort)."""
        self.client.login()
        try:
            return fn(*a)
        finally:
            self.client.logout()

    def _fetch(self, with_codes: bool):
        # LOG_FETCH_COUNT: de VTO bewaart per deur een vaste ringbuffer van ca. 1000 records
        # (oudste eerst); we moeten telkens de VOLLEDIGE buffer ophalen, anders blijven we
        # eeuwig op de oudste 100 hangen en missen we alle recente toegangen.
        out = {"unlocks": self.client.unlocks(LOG_FETCH_COUNT)}
        if with_codes:
            out["codes"] = self.client.codes()
            out["cards"] = self.client.cards()
            if not self.info:
                out["info"] = self.client.info()
        return out

    async def _async_update_data(self):
        now = dt_util.utcnow()
        with_codes = self._last_codes_fetch is None or (now - self._last_codes_fetch).total_seconds() >= CODES_INTERVAL
        try:
            data = await self.hass.async_add_executor_job(self._run, self._fetch, with_codes)
        except (VTOError, OSError) as e:
            raise UpdateFailed(f"{self.door_name}: {e}") from e
        if with_codes:
            self.codes, self.cards = data["codes"], data["cards"]
            self.info = data.get("info") or self.info
            self._last_codes_fetch = now
        await self._process_unlocks(data["unlocks"])
        return {"codes": len(self.codes), "cards": len(self.cards), "last_unlock": self.last_unlock}

    async def _process_unlocks(self, recs):
        """Verwerkt enkel records met een RecNo hoger dan het laatst bewaarde punt (persistent,
        overleeft herstarts). Bij de allereerste keer dat deze deur ooit gezien wordt, trekken we
        een stille baseline (niets loggen) om te vermijden dat de volledige bestaande logboek-
        geschiedenis van het toestel in één keer als "nieuw" binnenkomt."""
        recs = sorted(recs, key=lambda r: (r.get("CreateTime", 0), r.get("RecNo", 0)))
        if not recs:
            return
        last_recno = self.registry.get_last_recno(self.door_id)
        newest_recno = max(r.get("RecNo", 0) for r in recs)
        if last_recno is None:
            await self.registry.set_last_recno(self.door_id, newest_recno)
            self.last_unlock = self._fmt(recs[-1])
            return
        new_recs = [r for r in recs if r.get("RecNo", 0) > last_recno]
        if not new_recs:
            return
        for r in new_recs:
            self.last_unlock = self._fmt(r)
            self.hass.bus.async_fire(EVENT_UNLOCK, self.last_unlock)
            self._log_to_logbook(self.last_unlock)
        await self.registry.set_last_recno(self.door_id, newest_recno)

    def _log_to_logbook(self, unlock):
        """Schrijft een echte logboekregel (via logbook.log), zodat deze filterbaar is per deur/sensor
        in het Home Assistant Logboek en in een Logboek-kaart op het dashboard."""
        if self._entity_id is None:
            registry = er.async_get(self.hass)
            self._entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{self.door_id}_last_unlock") or False
        if not self._entity_id:
            return
        status = "geopend" if unlock["opened"] else "geweigerd"
        self.hass.async_create_task(
            self.hass.services.async_call(
                "logbook", "log",
                {
                    "name": f"VTO {unlock['door']}",
                    "message": f"{unlock['name']} via {unlock['method']} ({status})",
                    "entity_id": self._entity_id,
                    "domain": DOMAIN,
                },
                blocking=False,
            )
        )

    def _fmt(self, r):
        # CreateTime van het toestel is een Unix-epoch (UTC). We converteren expliciet via
        # Home Assistant's eigen tijdzone-instelling (Europe/Brussels), onafhankelijk van de
        # systeemtijdzone van de container waarin Home Assistant draait.
        local_time = dt_util.as_local(dt_util.utc_from_timestamp(r.get("CreateTime", 0)))
        return {
            "door_id": self.door_id, "door": self.door_name,
            "name": r.get("CardName") or r.get("UserID") or "?",
            "method": METHODS.get(r.get("Method"), str(r.get("Method"))),
            "opened": r.get("Status") == 1, "card": r.get("CardNo", ""),
            "time": local_time.isoformat(timespec="seconds"),
            "recno": r.get("RecNo"),
        }

    async def async_refresh_codes(self):
        self._last_codes_fetch = None
        await self.async_request_refresh()
