"""Btechnics VTO: centraal codebeheer en logboek voor Dahua VTO's."""
import asyncio
import logging
from datetime import timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .api import VTOClient, VTOError
from .archive import AccessArchive
from .const import ARCHIVE_FILE, CONF_DOORS, CONF_HOST, CONF_HTTPS, CONF_PASSWORD, CONF_USERNAME, DOMAIN, LOG_FETCH_COUNT
from .coordinator import API_ERRORS, DoorCoordinator
from .registry import CodeRegistry
from .websocket import ARCHIVE_KEY
from .websocket import async_register as async_register_websocket

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]
SERVICES = ["add_code", "update_code", "remove_code", "refresh", "list_codes", "list_log", "device_time"]

# Eén gedeeld register voor ALLE config entries en voor de hele levensduur van Home Assistant:
# meerdere instanties op hetzelfde opslagbestand zouden elkaars codes en doorloopunten overschrijven.
REG_KEY = f"{DOMAIN}_shared_registry"
REG_LOCK_KEY = f"{DOMAIN}_shared_registry_lock"
WRITE_LOCK_KEY = f"{DOMAIN}_write_lock"
GLOBAL_KEY = f"{DOMAIN}_global_setup"
CARDS_URL = "/btechnics_vto_static"
CARDS_FILE = "btechnics-vto-cards.js"

CODE_SCHEMA = vol.All(cv.string, vol.Match(r"^\d{4,8}$", msg="code moet 4 tot 8 cijfers zijn"))


class CodeExists(VTOError):
    """De code staat al op het toestel; er werd niets geschreven."""


async def _async_get_registry(hass: HomeAssistant) -> CodeRegistry:
    lock = hass.data.setdefault(REG_LOCK_KEY, asyncio.Lock())
    async with lock:
        reg = hass.data.get(REG_KEY)
        if reg is None:
            reg = CodeRegistry(hass)
            await reg.load()
            hass.data[REG_KEY] = reg
    return reg


async def _async_global_setup(hass: HomeAssistant) -> AccessArchive:
    """Eenmalig per Home Assistant: toegangsarchief, WebSocket-commando's, dashboardkaarten
    en het dagelijks opruimen van het archief. Gedeeld door alle config entries."""
    lock = hass.data.setdefault(REG_LOCK_KEY, asyncio.Lock())
    async with lock:
        if GLOBAL_KEY in hass.data:
            return hass.data.get(ARCHIVE_KEY)
        try:
            archive = await hass.async_add_executor_job(AccessArchive, hass.config.path(ARCHIVE_FILE))
        except Exception:  # noqa: BLE001  het archief is optioneel: de deuren moeten altijd werken
            _LOGGER.exception("Toegangsarchief kon niet geopend worden; deuren werken verder zonder archief")
            archive = None
        if archive is not None:
            hass.data[ARCHIVE_KEY] = archive
        async_register_websocket(hass)
        await _async_register_cards(hass)

        async def _prune(_now=None):
            try:
                n = await hass.async_add_executor_job(archive.prune)
                if n:
                    _LOGGER.info("Toegangsarchief: %s oude records opgeruimd", n)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Toegangsarchief opruimen mislukt")

        if archive is not None:
            await _prune()
            async_track_time_interval(hass, _prune, timedelta(hours=12))
        hass.data[GLOBAL_KEY] = True
    return archive


async def _async_register_cards(hass: HomeAssistant):
    """De eigen dashboardkaarten automatisch beschikbaar maken (geen manuele resource nodig)."""
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARDS_URL, str(Path(__file__).parent / "frontend"), False)]
    )
    add_extra_js_url(hass, f"{CARDS_URL}/{CARDS_FILE}?v={version}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    reg = await _async_get_registry(hass)
    archive = await _async_global_setup(hass)
    coords = {}
    for door in entry.data[CONF_DOORS]:
        client = VTOClient(door[CONF_HOST], door[CONF_HTTPS], door[CONF_USERNAME], door[CONF_PASSWORD])
        c = DoorCoordinator(hass, entry, door["id"], door["name"], client, reg, archive)
        await c.async_config_entry_first_refresh()
        # eerste keer: alle bestaande codes vergrendelen
        await reg.snapshot_protected(door["id"], [r["RecNo"] for r in c.codes])
        coords[door["id"]] = c
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"coords": coords}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            # laatste entry weg: services opruimen. Het register blijft bewust in het geheugen,
            # zodat een lopende service en een herladen entry altijd dezelfde instantie gebruiken.
            for s in SERVICES:
                hass.services.async_remove(DOMAIN, s)
            hass.data.pop(DOMAIN, None)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Config entry verwijderd: opgeslagen toestand van die deuren opruimen (historiek blijft bewaard)."""
    reg = await _async_get_registry(hass)
    archive = hass.data.get(ARCHIVE_KEY)
    for door in entry.data.get(CONF_DOORS, []):
        await reg.forget_door(door["id"])
        if archive is not None:
            await hass.async_add_executor_job(archive.detach_door, door["id"])


def _all_coords(hass):
    # Coordinators van ALLE config entries samen, niet enkel de eerst-geladen entry.
    coords = {}
    for d in hass.data.get(DOMAIN, {}).values():
        coords.update(d["coords"])
    return coords


def _door_ids(coords, doors):
    ids = {c.door_id for c in coords.values()}
    names = {c.door_name.lower(): c.door_id for c in coords.values()}
    out = []
    for d in doors:
        did = d if d in ids else names.get(str(d).lower())
        if not did:
            raise HomeAssistantError(f"onbekende deur: {d}")
        if did not in out:
            out.append(did)
    return out


# ---------- toestelacties (draaien in de executor, binnen één VTO-sessie) ----------

def _find(codes, recno):
    return next((r for r in codes if int(r.get("RecNo", -1)) == int(recno)), None)


def _matches(rec, name, code) -> bool:
    return rec is not None and (rec.get("UserID") or "").strip() == name and rec.get("CommonPassword") == code


def _safe_add(client: VTOClient, name: str, code: str) -> int:
    """Voegt enkel toe als de code nog niet op het toestel staat (verse controle, niet uit cache)."""
    for r in client.codes():
        if r.get("CommonPassword") == code:
            raise CodeExists(f"code bestaat al ({(r.get('UserID') or '').strip()})")
    recno = client.add_code(name, code)
    if recno < 0:
        raise VTOError("toestel gaf geen RecNo terug voor de nieuwe code")
    return recno


def _safe_update(client: VTOClient, recno: int, old_name: str, old_code: str, name: str, code: str):
    """Wijzigt enkel als het record op dit RecNo nog exact de code is die de integratie aanmaakte."""
    codes = client.codes()
    if not _matches(_find(codes, recno), old_name, old_code):
        raise VTOError(f"veiligheidscontrole: RecNo {recno} is niet meer de code die de integratie aanmaakte, niets gewijzigd")
    if code != old_code:
        for r in codes:
            if r.get("CommonPassword") == code and int(r.get("RecNo", -1)) != int(recno):
                raise CodeExists(f"code bestaat al ({(r.get('UserID') or '').strip()})")
    client.update_code(recno, name, code)


def _safe_remove(client: VTOClient, recno: int, name: str, code: str):
    """Verwijdert enkel als het record op dit RecNo nog exact de code is die de integratie aanmaakte."""
    rec = _find(client.codes(), recno)
    if rec is None:
        return  # staat al niet meer op het toestel
    if not _matches(rec, name, code):
        raise VTOError(f"veiligheidscontrole: RecNo {recno} is niet meer de code die de integratie aanmaakte, niets verwijderd")
    client.remove_code(recno)


def _read_codes(client: VTOClient) -> list:
    return client.codes()


def _locate(codes, name, code):
    hits = [r for r in codes if _matches(r, name, code)]
    return int(hits[0]["RecNo"]) if len(hits) == 1 else None


def _register_services(hass: HomeAssistant):
    if hass.services.has_service(DOMAIN, "add_code"):
        return
    # Alle schrijfacties na elkaar: twee gelijktijdige aanroepen (bv. twee automatiseringen)
    # zouden anders elk met een verouderd beeld van register en toestel verder werken.
    write_lock = hass.data.setdefault(WRITE_LOCK_KEY, asyncio.Lock())

    def _reg() -> CodeRegistry:
        return hass.data[REG_KEY]

    async def _exec(c, fn, *a):
        try:
            return await hass.async_add_executor_job(c._run, fn, c.client, *a)
        except HomeAssistantError:
            raise
        except Exception as e:  # noqa: BLE001  elke fout moet het opruimen/terugdraaien laten lopen
            raise HomeAssistantError(f"{c.door_name}: {e}") from e

    def _notify(message: str):
        """Blijvende melding in Home Assistant voor situaties die manueel nagekeken moeten worden."""
        _LOGGER.error(message)
        if hass.services.has_service("persistent_notification", "create"):
            hass.async_create_task(hass.services.async_call(
                "persistent_notification", "create", {"title": "Btechnics VTO: nakijken", "message": message}
            ))

    async def _guarded(fn, call):
        """Schrijfactie in een eigen taak onder het schrijfslot, afgeschermd tegen annulering:
        als het script dat de service aanriep stopt, loopt de actie (en het eventuele opruimen)
        toch volledig af en blijft het slot bezet tot het echt klaar is."""
        async def _locked():
            async with write_lock:
                return await fn(call)
        return await asyncio.shield(hass.async_create_task(_locked()))

    async def _device_codes(c):
        """Verse lijst codes van het toestel, of None als het toestel niet bereikbaar is."""
        try:
            return await _exec(c, _read_codes)
        except HomeAssistantError as e:
            _LOGGER.error("%s: kon de codes niet nalezen: %s", c.door_name, e)
            return None

    async def add_code(call: ServiceCall):
        return await _guarded(_add_code, call)

    async def _add_code(call: ServiceCall):
        coords = _all_coords(hass)
        reg = _reg()
        name, code = call.data["name"].strip(), call.data["code"]
        if not name:
            raise HomeAssistantError("naam mag niet leeg zijn")
        door_ids = _door_ids(coords, call.data["doors"])
        # eerste controle op alle gekozen deuren vóór er iets geschreven wordt
        for did in door_ids:
            c = coords[did]
            for r in c.codes:
                if r.get("CommonPassword") == code:
                    raise HomeAssistantError(f"code bestaat al op {c.door_name} ({(r.get('UserID') or '').strip()})")
        doors = {}
        attempted = []
        try:
            for did in door_ids:
                attempted.append(did)
                doors[did] = await _exec(coords[did], _safe_add, name, code)
            cid = await reg.add(name, code, doors)
        except HomeAssistantError as err:
            # Alles of niets. Ook de deur waar het misliep kan de code toch bewaard hebben
            # (bv. time-out na het opslaan), dus op elke geprobeerde deur het toestel nalezen.
            if isinstance(err.__cause__, CodeExists):
                attempted.remove(did)  # daar werd niets geschreven, en die code is niet van ons
            stuck, unknown = {}, []
            for d in attempted:
                codes = await _device_codes(coords[d])
                if codes is None:
                    if d in doors:
                        stuck[d] = doors[d]
                    else:
                        unknown.append(coords[d].door_name)
                    continue
                recno = _locate(codes, name, code)
                if recno is None:
                    continue
                try:
                    await _exec(coords[d], _safe_remove, recno, name, code)
                except HomeAssistantError as e:
                    _LOGGER.error("Terugdraaien mislukt op %s (RecNo %s): %s", coords[d].door_name, recno, e)
                    stuck[d] = recno
            msg = str(err)
            if stuck:
                # niet terug te draaien: toch registreren, zodat de code later via remove_code weg kan
                sid = await reg.add(name, code, stuck)
                msg += f" (code bleef staan op {', '.join(coords[d].door_name for d in stuck)}, id {sid})"
            if unknown:
                msg += f" (controleer manueel of de code op {', '.join(unknown)} staat)"
                _notify(f"Code '{name}' toevoegen mislukt; kon niet nagaan of ze toch op {', '.join(unknown)} staat. Kijk dit na op het toestel.")
            raise HomeAssistantError(msg) from err
        finally:
            for did in door_ids:
                await coords[did].async_refresh_codes()
        return {"id": cid, "doors": doors}

    async def update_code(call: ServiceCall):
        return await _guarded(_update_code, call)

    async def _update_code(call: ServiceCall):
        coords = _all_coords(hass)
        reg = _reg()
        cid = call.data["id"]
        if cid not in reg.managed:
            raise HomeAssistantError("onbekende id: enkel codes die via de integratie zijn aangemaakt kunnen gewijzigd worden")
        m = reg.managed[cid]
        old_name, old_code = m["name"], m["code"]
        orig = dict(m["doors"])
        name = call.data.get("name", old_name).strip()
        code = call.data.get("code", old_code)
        if not name:
            raise HomeAssistantError("naam mag niet leeg zijn")
        want = set(_door_ids(coords, call.data["doors"])) if "doors" in call.data else set(orig)
        if not want:
            raise HomeAssistantError("minstens één deur nodig; gebruik remove_code om de code overal te verwijderen")
        for did, recno in orig.items():
            if did not in coords:
                raise HomeAssistantError(f"deur {did} is momenteel niet geladen, probeer later opnieuw")
            if not reg.may_write(did, recno):
                raise HomeAssistantError(f"{coords[did].door_name}: RecNo {recno} is beschermd")
        # cur[deur] = (recno, naam, code) zoals het nu op het toestel staat
        cur = {did: (recno, old_name, old_code) for did, recno in orig.items()}
        add_tried = set()   # nieuwe deuren waar we effectief een record probeerden aan te maken
        released = []
        failed = False
        try:
            for did, recno in orig.items():
                if did in want:
                    await _exec(coords[did], _safe_update, recno, old_name, old_code, name, code)
                    cur[did] = (recno, name, code)
                else:
                    await _exec(coords[did], _safe_remove, recno, old_name, old_code)
                    cur.pop(did)
            for did in want - set(orig):
                add_tried.add(did)
                try:
                    cur[did] = (await _exec(coords[did], _safe_add, name, code), name, code)
                except HomeAssistantError as e:
                    if isinstance(e.__cause__, CodeExists):
                        add_tried.discard(did)   # niets geschreven; die code is van iemand anders
                    raise
        except HomeAssistantError:
            failed = True
            raise
        finally:
            if failed:
                # Na een fout de werkelijke toestand van elke betrokken deur nalezen:
                # een time-out kan optreden nadat het toestel de wijziging al uitvoerde.
                for did in set(orig) | want:
                    codes = await _device_codes(coords[did])
                    if codes is None:
                        continue  # onbereikbaar: laatst gekende toestand (cur) behouden
                    recno = orig.get(did, cur.get(did, (None,))[0])
                    rec = _find(codes, recno) if recno is not None else None
                    if _matches(rec, name, code):
                        cur[did] = (recno, name, code)
                    elif _matches(rec, old_name, old_code):
                        cur[did] = (recno, old_name, old_code)
                    else:
                        found = _locate(codes, name, code) if did in add_tried else None
                        if found is not None:
                            cur[did] = (found, name, code)
                        else:
                            if did in orig and rec is not None:
                                released.append(f"{coords[did].door_name} (RecNo {recno}: nu '{(rec.get('UserID') or '').strip()}')")
                            cur.pop(did, None)
                if released:
                    _notify(
                        f"Code-id {cid} ('{old_name}'): het record op {', '.join(released)} is buiten de integratie gewijzigd "
                        "en wordt niet meer door de integratie beheerd. Kijk op het toestel na of die code nog actief mag blijven."
                    )
            new_doors = {d: v[0] for d, v in cur.items() if v[1:] == (name, code)}
            old_doors = {d: v[0] for d, v in cur.items() if v[1:] == (old_name, old_code) and (name, code) != (old_name, old_code)}
            if not old_doors:
                if new_doors:
                    await reg.update(cid, name, code, new_doors)
                else:
                    await reg.remove(cid)
            else:
                # Gedeeltelijk uitgevoerd: niet-gewijzigde deuren houden de oude naam/code onder dit id,
                # al gewijzigde deuren komen onder een nieuw id. Niets raakt zoek in het register.
                await reg.update(cid, old_name, old_code, old_doors)
                if new_doors:
                    extra = await reg.add(name, code, new_doors)
                    _LOGGER.error("update_code %s gedeeltelijk uitgevoerd; gewijzigde deuren staan onder id %s", cid, extra)
            for did in want | set(orig):
                if did in coords:
                    await coords[did].async_refresh_codes()
        return {"id": cid, "doors": new_doors}

    async def remove_code(call: ServiceCall):
        return await _guarded(_remove_code, call)

    async def _remove_code(call: ServiceCall):
        coords = _all_coords(hass)
        reg = _reg()
        cid = call.data["id"]
        if cid not in reg.managed:
            raise HomeAssistantError("onbekende id: bestaande codes kunnen niet verwijderd worden")
        m = reg.managed[cid]
        name, code, orig = m["name"], m["code"], dict(m["doors"])
        for did, recno in orig.items():
            if did not in coords:
                raise HomeAssistantError(f"deur {did} is momenteel niet geladen, probeer later opnieuw")
            if not reg.may_write(did, recno):
                raise HomeAssistantError(f"{coords[did].door_name}: RecNo {recno} is beschermd")
        remaining = dict(orig)
        failed = False
        try:
            for did, recno in orig.items():
                await _exec(coords[did], _safe_remove, recno, name, code)
                remaining.pop(did)
        except HomeAssistantError:
            failed = True
            raise
        finally:
            if failed:
                for did, recno in list(remaining.items()):
                    codes = await _device_codes(coords[did])
                    if codes is None:
                        continue
                    rec = _find(codes, recno)
                    if not _matches(rec, name, code):
                        # toch verwijderd (time-out na het verwijderen) of intussen iemand anders zijn
                        # code: in beide gevallen niet meer van de integratie, claim loslaten
                        remaining.pop(did)
                        if rec is not None:
                            _notify(
                                f"Code-id {cid} ('{name}'): het record op {coords[did].door_name} (RecNo {recno}) is buiten de "
                                f"integratie gewijzigd (nu '{(rec.get('UserID') or '').strip()}') en werd NIET verwijderd. "
                                "Kijk op het toestel na of die code nog actief mag blijven."
                            )
            if remaining:
                await reg.update(cid, name, code, remaining)
            else:
                await reg.remove(cid)
            for did in orig:
                await coords[did].async_refresh_codes()

    async def refresh(call: ServiceCall):
        for c in _all_coords(hass).values():
            await c.async_refresh_codes()

    async def list_codes(call: ServiceCall):
        coords = _all_coords(hass)
        reg = _reg()
        rows = {}
        for did, c in coords.items():
            for r in c.codes:
                key = ((r.get("UserID") or "").strip(), r.get("CommonPassword"))
                row = rows.setdefault(key, {"name": key[0], "code": key[1], "doors": {}, "managed": False, "id": None})
                row["doors"][c.door_name] = r["RecNo"]
        for cid, m in reg.managed.items():
            row = rows.get((m["name"], m["code"]))
            if row:
                row["managed"], row["id"] = True, cid
        return {"codes": sorted(rows.values(), key=lambda x: x["name"].lower()), "registry": reg.export()}

    async def list_log(call: ServiceCall):
        # Leest rechtstreeks de volledige buffer van elke deur, over alle config entries heen.
        count = call.data.get("count", LOG_FETCH_COUNT)
        raw = call.data.get("raw", False)
        log = []
        for c in _all_coords(hass).values():
            try:
                recs = await hass.async_add_executor_job(c._run, c.client.unlocks, count)
            except API_ERRORS as e:
                raise HomeAssistantError(f"{c.door_name}: {e}") from e
            for r in recs:
                # kopieen van een ander toestel enkel in de ruwe weergave (anders staan ze er dubbel)
                if isinstance(r, dict) and (raw or c.is_own(r)):
                    f = c._fmt(r)
                    if raw:
                        # alle velden zoals het toestel ze bewaart, voor diagnose (een eventueel wachtwoordveld niet)
                        f["raw"] = {k: v for k, v in r.items() if k != "Password"}
                    log.append(f)
        # sorteren op het echte tijdstip (epoch), niet op de tekst: rond de wissel naar
        # wintertijd komt hetzelfde lokale uur twee keer voor
        log.sort(key=lambda f: f["ts"], reverse=True)
        return {"log": [
            {"tijd": f["time"], "deur": f["door"], "naam": f["name"], "methode": f["method"],
             "geopend": f["opened"], "kaart": f["card"], **({"ruw": f["raw"]} if "raw" in f else {})}
            for f in log
        ]}

    async def device_time(call: ServiceCall):
        # Alleen lezen: klok van elk toestel naast de klok van Home Assistant, om tijdsverschillen op te sporen.
        out = []
        for c in _all_coords(hass).values():
            try:
                clk = await hass.async_add_executor_job(c._run, c.client.clock)
            except API_ERRORS as e:
                clk = {"fout": str(e)}
            out.append({"deur": c.door_name, "home_assistant": dt_util.now().isoformat(timespec="seconds"), **clk})
        return {"toestellen": out}

    hass.services.async_register(DOMAIN, "device_time", device_time, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "add_code", add_code, vol.Schema({
        vol.Required("name"): cv.string, vol.Required("code"): CODE_SCHEMA,
        vol.Required("doors"): vol.All(cv.ensure_list, [cv.string])}), supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "update_code", update_code, vol.Schema({
        vol.Required("id"): cv.string, vol.Optional("name"): cv.string, vol.Optional("code"): CODE_SCHEMA,
        vol.Optional("doors"): vol.All(cv.ensure_list, [cv.string])}), supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "remove_code", remove_code, vol.Schema({vol.Required("id"): cv.string}))
    hass.services.async_register(DOMAIN, "refresh", refresh)
    hass.services.async_register(DOMAIN, "list_codes", list_codes, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "list_log", list_log, vol.Schema({
        vol.Optional("count"): vol.All(vol.Coerce(int), vol.Range(min=1, max=5000)),
        vol.Optional("raw", default=False): cv.boolean}), supports_response=SupportsResponse.ONLY)
