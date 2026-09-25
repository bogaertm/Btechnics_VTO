"""Register van codes die via de integratie zijn aangemaakt, gedeeld door alle config entries.

Alles wat NIET in dit register staat is een bestaande code en is alleen-lezen.
Daarbovenop staat een snapshot van alle RecNo's bij eerste start (protected):
die kunnen nooit gewijzigd of verwijderd worden, ook niet als het register corrupt zou zijn.

Ook het logboek-doorloopunt per deur wordt hier bewaard (de inhoud van de laatst verwerkte
records in toestelvolgorde, plus het tijdstip van het allerlaatste), zodat een herstart van
Home Assistant niets dubbel logt en niets verliest.

Er mag maar één instantie per Home Assistant bestaan (zie __init__.py): meerdere instanties op
hetzelfde opslagbestand zouden elkaars gegevens overschrijven.
"""
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import STORAGE_KEY, STORAGE_VERSION


class CodeRegistry:
    def __init__(self, hass: HomeAssistant):
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.managed: dict = {}      # id -> {name, code, doors: {door_id: recno}, created, updated}
        self.protected: dict = {}    # door_id -> [recno, ...]  (snapshot bestaande codes)
        self.log_state: dict = {}    # door_id -> {"tail": [recordsleutels], "t": epoch laatste record}

    async def load(self):
        data = await self._store.async_load() or {}
        self.managed = data.get("managed", {})
        self.protected = data.get("protected", {})
        self.log_state = data.get("log_state", {})

    async def save(self):
        await self._store.async_save({"managed": self.managed, "protected": self.protected, "log_state": self.log_state})

    async def snapshot_protected(self, door_id: str, recnos: list):
        """Enkel bij de eerste keer dat een deur gezien wordt: alle bestaande RecNo's vergrendelen."""
        if door_id not in self.protected:
            self.protected[door_id] = sorted(int(r) for r in recnos)
            await self.save()

    def is_protected(self, door_id: str, recno: int) -> bool:
        return int(recno) in self.protected.get(door_id, [])

    def is_managed(self, door_id: str, recno: int) -> bool:
        return any(int(m["doors"].get(door_id, -1)) == int(recno) for m in self.managed.values())

    def may_write(self, door_id: str, recno: int) -> bool:
        """Schrijven op een RecNo mag enkel als hij in het register staat EN niet beschermd is."""
        return self.is_managed(door_id, recno) and not self.is_protected(door_id, recno)

    def get_log_state(self, door_id: str):
        """None = geen (geldig) doorloopunt: de coordinator trekt dan een stille baseline.
        Het oude formaat (een los RecNo-getal uit v0.1.9) is onbruikbaar en telt ook als None."""
        s = self.log_state.get(door_id)
        if (
            isinstance(s, dict) and isinstance(s.get("t"), int) and isinstance(s.get("tail"), list)
            and all(isinstance(k, list) for k in s["tail"])
        ):
            return s
        return None

    async def set_log_state(self, door_id: str, state: dict):
        self.log_state[door_id] = {"tail": [list(k) for k in state["tail"]], "t": int(state["t"])}
        await self.save()

    async def add(self, name: str, code: str, doors: dict) -> str:
        cid = uuid.uuid4().hex[:8]
        now = dt_util.now().isoformat(timespec="seconds")
        self.managed[cid] = {"name": name, "code": code, "doors": doors, "created": now, "updated": now}
        await self.save()
        return cid

    async def update(self, cid: str, name: str, code: str, doors: dict):
        m = self.managed[cid]
        m.update({"name": name, "code": code, "doors": doors, "updated": dt_util.now().isoformat(timespec="seconds")})
        await self.save()

    async def remove(self, cid: str):
        self.managed.pop(cid, None)
        await self.save()

    def export(self) -> dict:
        return {"managed": self.managed, "protected": self.protected}
