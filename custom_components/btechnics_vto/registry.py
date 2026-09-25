"""Register van codes die via de integratie zijn aangemaakt.

Alles wat NIET in dit register staat is een bestaande code en is alleen-lezen.
Daarbovenop staat een snapshot van alle RecNo's bij eerste start (protected):
die kunnen nooit gewijzigd of verwijderd worden, ook niet als het register corrupt zou zijn.

Ook het logboek-doorloopunt (laatst verwerkte RecNo per deur) wordt hier bewaard, zodat een
herstart van Home Assistant niet telkens een nieuwe "baseline" trekt en zo echte, nog niet
getoonde toegangsgebeurtenissen zou verliezen.
"""
import uuid
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION


class CodeRegistry:
    def __init__(self, hass: HomeAssistant):
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.managed: dict = {}      # id -> {name, code, doors: {door_id: recno}, created, updated}
        self.protected: dict = {}    # door_id -> [recno, ...]  (snapshot bestaande codes)
        self.log_state: dict = {}    # door_id -> laatst verwerkte RecNo uit het logboek

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

    def get_last_recno(self, door_id: str):
        """None = deze deur is nog nooit gezien: de coordinator moet dan een stille baseline trekken."""
        return self.log_state.get(door_id)

    async def set_last_recno(self, door_id: str, recno: int):
        self.log_state[door_id] = int(recno)
        await self.save()

    async def add(self, name: str, code: str, doors: dict) -> str:
        cid = uuid.uuid4().hex[:8]
        now = datetime.now().isoformat(timespec="seconds")
        self.managed[cid] = {"name": name, "code": code, "doors": doors, "created": now, "updated": now}
        await self.save()
        return cid

    async def update(self, cid: str, name: str, code: str, doors: dict):
        m = self.managed[cid]
        m.update({"name": name, "code": code, "doors": doors, "updated": datetime.now().isoformat(timespec="seconds")})
        await self.save()

    async def remove(self, cid: str):
        self.managed.pop(cid, None)
        await self.save()

    def export(self) -> dict:
        return {"managed": self.managed, "protected": self.protected}
