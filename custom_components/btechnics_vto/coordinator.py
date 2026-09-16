"""Coordinator per deur: pollt logboek (30 s) en codes/badges (5 min), stuurt events."""
import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VTOClient, VTOError
from .const import CODES_INTERVAL, DOMAIN, EVENT_UNLOCK, LOG_INTERVAL, METHODS

_LOGGER = logging.getLogger(__name__)


class DoorCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, door_id: str, door_name: str, client: VTOClient):
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {door_name}", update_interval=timedelta(seconds=LOG_INTERVAL))
        self.door_id = door_id
        self.door_name = door_name
        self.client = client
        self.info = {}
        self.codes = []
        self.cards = []
        self.last_unlock = None
        self._last_codes_fetch = None
        self._seen_recnos = None

    def _run(self, fn, *a):
        """Sync API-aanroep in executor, met login per beurt (VTO houdt sessies kort)."""
        self.client.login()
        try:
            return fn(*a)
        finally:
            self.client.logout()

    def _fetch(self, with_codes: bool):
        out = {"unlocks": self.client.unlocks(100)}
        if with_codes:
            out["codes"] = self.client.codes()
            out["cards"] = self.client.cards()
            if not self.info:
                out["info"] = self.client.info()
        return out

    async def _async_update_data(self):
        now = datetime.now()
        with_codes = self._last_codes_fetch is None or (now - self._last_codes_fetch).total_seconds() >= CODES_INTERVAL
        try:
            data = await self.hass.async_add_executor_job(self._run, self._fetch, with_codes)
        except (VTOError, OSError) as e:
            raise UpdateFailed(f"{self.door_name}: {e}") from e
        if with_codes:
            self.codes, self.cards = data["codes"], data["cards"]
            self.info = data.get("info") or self.info
            self._last_codes_fetch = now
        self._process_unlocks(data["unlocks"])
        return {"codes": len(self.codes), "cards": len(self.cards), "last_unlock": self.last_unlock}

    def _process_unlocks(self, recs):
        recs = sorted(recs, key=lambda r: (r.get("CreateTime", 0), r.get("RecNo", 0)))
        if not recs:
            return
        if self._seen_recnos is None:
            self._seen_recnos = {r["RecNo"] for r in recs}
            self.last_unlock = self._fmt(recs[-1])
            return
        for r in recs:
            if r["RecNo"] in self._seen_recnos:
                continue
            self._seen_recnos.add(r["RecNo"])
            self.last_unlock = self._fmt(r)
            self.hass.bus.async_fire(EVENT_UNLOCK, self.last_unlock)
        if len(self._seen_recnos) > 2000:
            self._seen_recnos = {r["RecNo"] for r in recs}

    def _fmt(self, r):
        return {
            "door_id": self.door_id, "door": self.door_name,
            "name": r.get("CardName") or r.get("UserID") or "?",
            "method": METHODS.get(r.get("Method"), str(r.get("Method"))),
            "opened": r.get("Status") == 1, "card": r.get("CardNo", ""),
            "time": datetime.fromtimestamp(r.get("CreateTime", 0)).isoformat(timespec="seconds"),
            "recno": r.get("RecNo"),
        }

    async def async_refresh_codes(self):
        self._last_codes_fetch = None
        await self.async_request_refresh()
