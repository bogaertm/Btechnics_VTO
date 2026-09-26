"""Register van alle codes en badges, gedeeld door alle config entries.

Sinds v0.4.0 (beslissing Matthias, 26/09/2026) is elke code en badge op de toestellen beheerbaar:
wat op een toestel staat en nog niet in het register zit, wordt automatisch opgenomen ("adopt").
Een ingang ("entry") groepeert dezelfde code of badge over meerdere deuren en heeft een status:
active (op de toestellen), blocked (tijdelijk van de toestellen gehaald, eventueel tot een tijdstip)
of retired (uit dienst, van de toestellen gehaald maar herstelbaar). Van wat niet op een toestel
staat, bewaart "stored" per deur het volledige record, zodat het exact teruggezet kan worden.
Elke schrijfactie controleert eerst op het toestel of het record nog exact overeenkomt.

"protected" (de RecNo's van bij de eerste start) blijft bewaard ter informatie, maar blokkeert niets meer.

Ook het logboek-doorloopunt per deur wordt hier bewaard (de inhoud van de laatst verwerkte
records in toestelvolgorde, plus het tijdstip van het allerlaatste), zodat een herstart van
Home Assistant niets dubbel logt en niets verliest.

Er mag maar één instantie per Home Assistant bestaan (zie __init__.py): meerdere instanties op
hetzelfde opslagbestand zouden elkaars gegevens overschrijven.
"""
import uuid

AUDIT_MAX = 1000

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import STORAGE_KEY, STORAGE_VERSION


class CodeRegistry:
    def __init__(self, hass: HomeAssistant):
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.managed: dict = {}      # id -> {name, code, doors: {door_id: recno}, created, updated}
        self.protected: dict = {}    # door_id -> [recno, ...]  (snapshot bestaande codes)
        self.log_state: dict = {}    # door_id -> {"tail": [recordsleutels], "t": epoch laatste record, "len": buffergrootte}
        self.audit: list = []        # wijzigingen: wie, wat, wanneer (recentste laatst)

    async def load(self):
        data = await self._store.async_load() or {}
        self.managed = data.get("managed", {})
        self.protected = data.get("protected", {})
        self.log_state = data.get("log_state", {})
        self.audit = data.get("audit", [])
        for m in self.managed.values():
            _defaults(m)

    async def save(self):
        await self._store.async_save({"managed": self.managed, "protected": self.protected, "log_state": self.log_state,
                                      "audit": self.audit})

    async def snapshot_protected(self, door_id: str, recnos: list):
        """Enkel bij de eerste keer dat een deur gezien wordt: alle bestaande RecNo's vergrendelen."""
        if door_id not in self.protected:
            self.protected[door_id] = sorted(int(r) for r in recnos)
            await self.save()

    def is_protected(self, door_id: str, recno: int) -> bool:
        return int(recno) in self.protected.get(door_id, [])

    def is_managed(self, door_id: str, recno: int, kind: str = "code") -> bool:
        return any(m["kind"] == kind and int(m["doors"].get(door_id, -1)) == int(recno) for m in self.managed.values())

    def may_write(self, door_id: str, recno: int, kind: str = "code") -> bool:
        """Schrijven op een RecNo mag enkel als hij in het register staat (en het toestel bevestigt
        vlak voor elke schrijfactie dat het record nog exact overeenkomt)."""
        return any(m["kind"] == kind and int(m["doors"].get(door_id, -1)) == int(recno) for m in self.managed.values())

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
        self.log_state[door_id] = {"tail": [list(k) for k in state["tail"]], "t": int(state["t"]), "len": int(state.get("len", 0))}
        await self.save()

    async def add(self, name: str, code: str, doors: dict) -> str:
        cid = self._new(kind="code", name=name, secret=code, doors=doors, source="home assistant")
        await self.save()
        return cid

    def _new(self, kind: str, name: str, secret: str, doors: dict, source: str) -> str:
        cid = uuid.uuid4().hex[:8]
        while cid in self.managed:
            cid = uuid.uuid4().hex[:8]
        now = dt_util.now().isoformat(timespec="seconds")
        m = {"kind": kind, "name": name, "doors": dict(doors), "created": now, "updated": now, "source": source}
        m["code" if kind == "code" else "card"] = secret
        self.managed[cid] = _defaults(m)
        return cid

    def adopt(self, door_id: str, codes: list, cards: list) -> bool:
        """Register gelijkzetten met wat er nu op het toestel staat (verse uitlezing).

        Verwijzingen naar records die weg of gewijzigd zijn, vallen weg (een actieve ingang zonder
        deuren verdwijnt). Records die nog nergens bij horen, komen bij een actieve ingang met dezelfde
        naam en code/badge die deze deur nog niet heeft, of worden een nieuwe ingang."""
        changed = False
        for kind, recs in (("code", codes), ("badge", cards)):
            if recs is None:
                continue
            present = {}
            for r in recs:
                try:
                    present[int(r.get("RecNo"))] = rec_identity(r, kind)
                except (TypeError, ValueError):
                    continue
            for cid, m in list(self.managed.items()):
                if m["kind"] != kind or door_id not in m["doors"]:
                    continue
                if present.get(int(m["doors"][door_id])) != (m["name"], secret(m)):
                    m["doors"].pop(door_id)
                    changed = True
                    if m["status"] == "active" and not m["doors"] and not m["stored"]:
                        self.managed.pop(cid)
            taken = {int(m["doors"][door_id]) for m in self.managed.values() if m["kind"] == kind and door_id in m["doors"]}
            for recno, (name, sec) in sorted(present.items()):
                if recno in taken:
                    continue
                target = next((m for m in self.managed.values()
                               if m["kind"] == kind and m["status"] == "active" and m["name"] == name
                               and secret(m) == sec and door_id not in m["doors"]), None)
                if target is not None:
                    target["doors"][door_id] = recno
                else:
                    self._new(kind=kind, name=name, secret=sec, doors={door_id: recno}, source="toestel")
                changed = True
        return changed

    def log(self, user: str, action: str, m: dict, doors: list, detail: str = ""):
        self.audit.append({
            "ts": dt_util.now().isoformat(timespec="seconds"), "user": user, "action": action,
            "kind": m.get("kind", "code"), "name": m.get("name", ""), "doors": doors, "detail": detail,
        })
        del self.audit[:-AUDIT_MAX]

    async def update(self, cid: str, name: str, code: str, doors: dict):
        m = self.managed[cid]
        m.update({"name": name, "code": code, "doors": doors, "updated": dt_util.now().isoformat(timespec="seconds")})
        await self.save()

    def touch(self, m: dict):
        m["updated"] = dt_util.now().isoformat(timespec="seconds")

    async def remove(self, cid: str):
        self.managed.pop(cid, None)
        await self.save()

    async def forget_door(self, door_id: str):
        """Deur verwijderd: doorloopunt en bescherming weg, en de deur uit beheerde codes halen.
        Zo start een nieuw toestel met dezelfde naam met een schone lei. Bewaarde (geblokkeerde of
        uit dienst gehaalde) records blijven, zodat ze na het opnieuw toevoegen van de deur terug kunnen."""
        self.log_state.pop(door_id, None)
        self.protected.pop(door_id, None)
        for cid in list(self.managed):
            m = self.managed[cid]
            m["doors"].pop(door_id, None)
            if not m["doors"] and not m["stored"]:
                self.managed.pop(cid)
        await self.save()

    def export(self) -> dict:
        return {"managed": self.managed, "protected": self.protected}


def _defaults(m: dict) -> dict:
    """Ingangen van voor v0.4.0 aanvullen (dat waren altijd actieve codes)."""
    m.setdefault("kind", "code")
    m.setdefault("status", "active")
    m.setdefault("stored", {})
    m.setdefault("until", None)
    m.setdefault("valid_from", None)
    m.setdefault("valid_until", None)
    m.setdefault("source", "home assistant")
    return m


def secret(m: dict) -> str:
    return m["code"] if m["kind"] == "code" else m["card"]


def rec_identity(r: dict, kind: str):
    """(naam, code of badgenummer) van een record zoals het toestel het bewaart."""
    if kind == "code":
        return ((r.get("UserID") or "").strip(), r.get("CommonPassword") or "")
    return ((r.get("CardName") or r.get("UserName") or "").strip(), r.get("CardNo") or "")
