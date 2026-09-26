"""Beheer van codes en badges: blokkeren (tijdelijk of tot deblokkeren), uit dienst, herstellen.

Een code of badge wordt geblokkeerd door ze van de toestellen te halen: dat werkt altijd en is het
veiligst (de codetabel van deze VTO's kent geen status of geldigheid, vastgesteld 26/09/2026). Het
volledige record wordt per deur bewaard, zodat deblokkeren of herstellen exact hetzelfde terugzet.

Elke schrijfactie draait onder het gedeelde schrijfslot, controleert vlak ervoor op het toestel of
het record nog exact overeenkomt, en leest na een fout het toestel opnieuw uit (een time-out kan
komen nadat het toestel de actie al uitvoerde). Wat half lukt, blijft zichtbaar en geeft een melding.
"""
import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import VTOError
from .registry import CodeRegistry, rec_identity, secret

_LOGGER = logging.getLogger(__name__)

MANAGER_KEY = "btechnics_vto_manager"


class CodeInUse(VTOError):
    """De code of badge staat al op het toestel bij iemand anders; er werd niets geschreven."""


# ---------- toestelacties (executor, binnen één VTO-sessie) ----------

def _read(client, kind: str) -> list:
    return client.codes() if kind == "code" else client.cards()


def _find(recs, recno):
    return next((r for r in recs if int(r.get("RecNo", -1)) == int(recno)), None)


def _strip(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k != "RecNo"}


def _take(client, kind: str, recno: int, name: str, sec: str):
    """Record van het toestel halen als het nog exact overeenkomt, en nakijken dat het echt weg is.
    Geeft het record terug (zonder RecNo), of None als het al weg was."""
    rec = _find(_read(client, kind), recno)
    if rec is None:
        return None
    if rec_identity(rec, kind) != (name, sec):
        raise VTOError(f"veiligheidscontrole: RecNo {recno} is intussen gewijzigd op het toestel, niets verwijderd")
    if kind == "code":
        client.remove_code(recno)
    else:
        client.remove_card(recno)
    if _find(_read(client, kind), recno) is not None:
        raise VTOError(f"RecNo {recno} staat na het verwijderen nog op het toestel")
    return _strip(rec)


def _put(client, kind: str, record: dict, name: str, sec: str) -> int:
    """Record terugzetten. Staat exact hetzelfde er al (iemand zette het manueel terug), dan wordt
    dat gebruikt; staat de code of badge er bij iemand anders, dan wordt niets geschreven."""
    for r in _read(client, kind):
        if rec_identity(r, kind)[1] == sec:
            if rec_identity(r, kind)[0] == name:
                return int(r["RecNo"])
            raise CodeInUse(f"{'code' if kind == 'code' else 'badge'} staat al op het toestel bij {rec_identity(r, kind)[0] or '?'}")
    if kind == "code":
        recno = client.add_code(name, sec, record)
    else:
        recno = client.add_card(_strip(record))
    if recno < 0:
        raise VTOError("toestel gaf geen RecNo terug")
    return recno


def _rename_card(client, recno: int, old_name: str, sec: str, name: str):
    rec = _find(client.cards(), recno)
    if rec is None or rec_identity(rec, "badge") != (old_name, sec):
        raise VTOError(f"veiligheidscontrole: RecNo {recno} is intussen gewijzigd op het toestel, niets aangepast")
    new = _strip(rec)
    new["CardName"] = name
    new["UserName"] = name
    client.update_card(recno, new)


class Manager:
    def __init__(self, hass: HomeAssistant, reg: CodeRegistry, write_lock: asyncio.Lock, coords_fn):
        self.hass = hass
        self.reg = reg
        self.lock = write_lock
        self._coords = coords_fn
        self._unsub = None
        self._warned = set()

    # ---------- algemeen ----------

    async def _exec(self, c, fn, *a):
        try:
            return await self.hass.async_add_executor_job(c._run, fn, c.client, *a)
        except HomeAssistantError:
            raise
        except Exception as e:  # noqa: BLE001
            raise HomeAssistantError(f"{c.door_name}: {e}") from e

    async def _device(self, c, kind):
        try:
            return await self._exec(c, _read, kind)
        except HomeAssistantError as e:
            _LOGGER.error("%s: kon de %s niet nalezen: %s", c.door_name, "codes" if kind == "code" else "badges", e)
            return None

    def notify(self, message: str, key: str | None = None):
        _LOGGER.error(message)
        if self.hass.services.has_service("persistent_notification", "create"):
            data = {"title": "Btechnics VTO: nakijken", "message": message}
            if key:
                data["notification_id"] = f"btechnics_vto_{key}"   # zelfde melding vervangen, niet stapelen
            self.hass.async_create_task(self.hass.services.async_call("persistent_notification", "create", data))

    async def user_name(self, context) -> str:
        uid = getattr(context, "user_id", None)
        if not uid:
            return "automatisch"
        user = await self.hass.auth.async_get_user(uid)
        return user.name if user and user.name else "onbekend"

    async def guarded(self, fn, *a):
        """Onder het schrijfslot, afgeschermd tegen annulering; daarna het register gelijkzetten."""
        async def _locked():
            async with self.lock:
                return await fn(*a)
        try:
            return await asyncio.shield(self.hass.async_create_task(_locked()))
        finally:
            await self.adopt_all()

    def entry(self, cid: str) -> dict:
        m = self.reg.managed.get(cid)
        if m is None:
            raise HomeAssistantError("onbekende id")
        return m

    def door_names(self, doors) -> list:
        coords = self._coords()
        return [coords[d].door_name if d in coords else d for d in doors]

    # ---------- register gelijkzetten met de toestellen ----------

    async def adopt(self, door_id: str, codes, cards):
        """Na elke verse uitlezing. Tijdens een schrijfactie niet: die zet het register zelf juist."""
        if self.lock.locked():
            return
        if self.reg.adopt(door_id, codes, cards):
            await self.reg.save()

    async def adopt_all(self):
        if self.lock.locked():
            return
        changed = False
        for did, c in self._coords().items():
            if c.last_update_success:
                changed |= self.reg.adopt(did, c.codes, c.cards)
        if changed:
            await self.reg.save()

    async def _refresh(self, doors):
        coords = self._coords()
        for d in doors:
            if d in coords:
                await coords[d].async_refresh_codes()

    # ---------- acties ----------

    async def block(self, cid: str, until, user: str, retire: bool = False):
        """Van alle toestellen halen en bewaren. until: datetime (UTC) of None (tot deblokkeren)."""
        m = self.entry(cid)
        if m["status"] == "retired" and not m["doors"]:
            raise HomeAssistantError(f"'{m['name']}' is al uit dienst")
        if m["status"] == "blocked" and not m["doors"] and not retire:
            # enkel de einddatum aanpassen
            m["until"] = until.isoformat() if until else None
            self.reg.touch(m)
            self.reg.log(user, "blokkering aangepast", m, self.door_names(m["stored"]), _until_txt(until))
            await self.reg.save()
            return {"id": cid, "status": m["status"]}
        kind, name, sec = m["kind"], m["name"], secret(m)
        coords = self._coords()
        errors, done, unsure = [], [], []
        for did, recno in list(m["doors"].items()):
            c = coords.get(did)
            if c is None:
                errors.append(f"deur {did} is niet geladen")
                continue
            # Eerst een verse kopie van het toestel lezen en bewaren, pas daarna verwijderen:
            # wat er ook misloopt, het record kan nooit verloren gaan.
            recs = await self._device(c, kind)
            if recs is None:
                errors.append(f"{c.door_name}: toestel niet bereikbaar, niets verwijderd")
                continue
            cur = _find(recs, recno)
            if cur is not None and rec_identity(cur, kind) != (name, sec):
                errors.append(f"{c.door_name}: veiligheidscontrole, RecNo {recno} is intussen gewijzigd op het toestel, niets verwijderd")
                continue
            had = did in m["stored"]
            backup = _strip(cur) if cur is not None else (m["stored"].get(did) or (
                {"UserID": name, "CommonPassword": sec} if kind == "code" else None))
            if backup is None:
                errors.append(f"{c.door_name}: badge was al weg van het toestel en kan niet bewaard worden")
                m["doors"].pop(did)
                continue
            m["stored"][did] = backup
            if cur is not None:
                await self.reg.save()
                try:
                    await self._exec(c, _take, kind, recno, name, sec)
                except HomeAssistantError as e:
                    recs = await self._device(c, kind)
                    if recs is None:
                        # onzeker of het weg is: kopie en verwijzing blijven, de volgende uitlezing beslist
                        unsure.append(did)
                        errors.append(f"{e} (onzeker of verwijderd, kopie bewaard)")
                        continue
                    if _find(recs, recno) is not None:
                        if not had:
                            m["stored"].pop(did)
                        errors.append(str(e))
                        continue
            m["doors"].pop(did)
            done.append(did)
        if done or unsure or not m["doors"]:
            m["status"] = "retired" if retire else "blocked"
            m["until"] = None if retire or until is None else until.isoformat()
        self.reg.touch(m)
        action = "uit dienst" if retire else "geblokkeerd"
        detail = "" if retire else _until_txt(until)
        if errors:
            detail = (detail + "; " if detail else "") + "niet gelukt: " + "; ".join(errors)
        self.reg.log(user, action, m, self.door_names(done), detail)
        await self.reg.save()
        await self._refresh(done)
        if errors:
            still = self.door_names(m["doors"])
            msg = f"'{name}' {action}, maar niet overal: {'; '.join(errors)}."
            if still:
                msg += f" Nog actief op {', '.join(still)}: probeer opnieuw of kijk het toestel na."
            self.notify(msg, cid)
            raise HomeAssistantError(msg)
        return {"id": cid, "status": m["status"]}

    async def unblock(self, cid: str, user: str, action: str = "gedeblokkeerd"):
        """Alle bewaarde records terug op de toestellen zetten."""
        m = self.entry(cid)
        if not m["stored"]:
            if m["status"] == "active":
                raise HomeAssistantError(f"'{m['name']}' is niet geblokkeerd")
            m["status"], m["until"] = "active", None
            await self.reg.save()
            return {"id": cid, "status": "active"}
        kind, name, sec = m["kind"], m["name"], secret(m)
        coords = self._coords()
        errors, done = [], []
        for did, rec in list(m["stored"].items()):
            c = coords.get(did)
            if c is None:
                errors.append(f"deur {did} is niet geladen")
                continue
            try:
                recno = await self._exec(c, _put, kind, rec, name, sec)
            except HomeAssistantError as e:
                recs = await self._device(c, kind)
                hits = [r for r in (recs or []) if rec_identity(r, kind) == (name, sec)]
                if isinstance(e.__cause__, CodeInUse) or len(hits) != 1:
                    errors.append(str(e))
                    continue
                recno = int(hits[0]["RecNo"])   # toch geschreven (time-out na het opslaan)
            m["doors"][did] = recno
            m["stored"].pop(did)
            done.append(did)
        if not m["stored"]:
            m["status"], m["until"] = "active", None
        self.reg.touch(m)
        detail = ("niet gelukt: " + "; ".join(errors)) if errors else ""
        self.reg.log(user, action, m, self.door_names(done), detail)
        await self.reg.save()
        await self._refresh(done)
        if errors:
            msg = f"'{name}' {action}, maar niet overal: {'; '.join(errors)}."
            self.notify(msg, cid)
            raise HomeAssistantError(msg)
        return {"id": cid, "status": m["status"]}

    async def edit_stored(self, cid: str, name, code, user: str):
        """Naam of code aanpassen van een geblokkeerde of uit dienst gehaalde ingang (enkel in het register)."""
        m = self.entry(cid)
        if m["doors"]:
            raise HomeAssistantError("staat nog op een toestel: pas aan via update_code")
        old = (m["name"], secret(m))
        if name:
            m["name"] = name
        if code:
            if m["kind"] != "code":
                raise HomeAssistantError("het nummer van een badge kan niet aangepast worden")
            m["code"] = code
        for rec in m["stored"].values():
            if m["kind"] == "code":
                rec["UserID"], rec["CommonPassword"] = m["name"], m["code"]
            else:
                rec["CardName"] = rec["UserName"] = m["name"]
        self.reg.touch(m)
        parts = []
        if m["name"] != old[0]:
            parts.append(f"naam {old[0]} naar {m['name']}")
        if secret(m) != old[1]:
            parts.append("code gewijzigd")
        self.reg.log(user, "aangepast", m, self.door_names(m["stored"]), ", ".join(parts))
        await self.reg.save()
        return {"id": cid}

    async def rename_badge(self, cid: str, name: str, user: str):
        m = self.entry(cid)
        if m["kind"] != "badge":
            raise HomeAssistantError("geen badge")
        if not m["doors"]:
            return await self.edit_stored(cid, name, None, user)
        coords = self._coords()
        old, errors, done = m["name"], [], []
        for did, recno in list(m["doors"].items()):
            c = coords.get(did)
            if c is None:
                errors.append(f"deur {did} is niet geladen")
                continue
            try:
                await self._exec(c, _rename_card, recno, old, m["card"], name)
                done.append(did)
            except HomeAssistantError as e:
                errors.append(str(e))
        if done:
            if len(done) == len(m["doors"]):
                m["name"] = name
            else:
                # half gelukt: de deuren met de nieuwe naam worden bij het gelijkzetten een eigen ingang
                for did in done:
                    m["doors"].pop(did)
            for rec in m["stored"].values():
                rec["CardName"] = rec["UserName"] = name
        self.reg.touch(m)
        self.reg.log(user, "aangepast", m, self.door_names(done), f"naam {old} naar {name}"
                     + (("; niet gelukt: " + "; ".join(errors)) if errors else ""))
        await self.reg.save()
        await self._refresh(done)
        if errors:
            msg = f"Badge '{old}': naam niet overal aangepast: {'; '.join(errors)}."
            self.notify(msg, cid)
            raise HomeAssistantError(msg)
        return {"id": cid}

    async def forget(self, cid: str, user: str):
        """Een ingang die uit dienst is definitief uit de lijst halen (de toegangshistoriek blijft)."""
        m = self.entry(cid)
        if m["status"] != "retired" or m["doors"]:
            raise HomeAssistantError("enkel wat uit dienst is en op geen enkel toestel meer staat, kan uit de lijst")
        self.reg.log(user, "definitief verwijderd", m, self.door_names(m["stored"]))
        self.reg.managed.pop(cid)
        await self.reg.save()
        return {"id": cid}

    # ---------- planner: blokkering met einddatum ----------

    @callback
    def start(self):
        self._unsub = async_track_time_interval(self.hass, self._tick, timedelta(minutes=1))

    @callback
    def stop(self):
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _tick(self, _now=None):
        now = dt_util.utcnow()
        for cid, m in list(self.reg.managed.items()):
            until = dt_util.parse_datetime(m["until"]) if m.get("status") == "blocked" and m.get("until") else None
            if until is None or until > now:
                continue
            try:
                await self.guarded(self.unblock, cid, "planner", "automatisch gedeblokkeerd")
                self._warned.discard(cid)
            except HomeAssistantError as e:
                if cid not in self._warned:
                    self._warned.add(cid)
                    _LOGGER.error("Automatisch deblokkeren van %s mislukt, nieuwe poging elke minuut: %s", m.get("name"), e)


def _until_txt(until) -> str:
    if until is None:
        return "tot deblokkeren"
    return "tot " + dt_util.as_local(until).strftime("%d/%m/%Y %H:%M")
