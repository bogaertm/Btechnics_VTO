"""Btechnics VTO: centraal codebeheer en logboek voor Dahua VTO's."""
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import VTOClient, VTOError
from .const import CONF_DOORS, CONF_HOST, CONF_HTTPS, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import DoorCoordinator
from .registry import CodeRegistry

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]

CODE_SCHEMA = vol.All(cv.string, vol.Match(r"^\d{4,8}$", msg="code moet 4 tot 8 cijfers zijn"))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    reg = CodeRegistry(hass)
    await reg.load()
    coords = {}
    for door in entry.data[CONF_DOORS]:
        client = VTOClient(door[CONF_HOST], door[CONF_HTTPS], door[CONF_USERNAME], door[CONF_PASSWORD])
        c = DoorCoordinator(hass, door["id"], door["name"], client)
        await c.async_config_entry_first_refresh()
        # eerste keer: alle bestaande codes van deze deur vergrendelen
        await reg.snapshot_protected(door["id"], [r["RecNo"] for r in c.codes])
        coords[door["id"]] = c
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"coords": coords, "registry": reg}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass, entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok


def _get(hass, entry_id):
    d = hass.data[DOMAIN][entry_id]
    return d["coords"], d["registry"]


def _door_ids(coords, doors):
    ids = {c.door_id for c in coords.values()}
    names = {c.door_name.lower(): c.door_id for c in coords.values()}
    out = []
    for d in doors:
        did = d if d in ids else names.get(str(d).lower())
        if not did:
            raise HomeAssistantError(f"onbekende deur: {d}")
        out.append(did)
    return out


def _register_services(hass: HomeAssistant, entry_id: str):
    if hass.services.has_service(DOMAIN, "add_code"):
        return

    async def add_code(call: ServiceCall):
        coords, reg = _get(hass, entry_id)
        name, code = call.data["name"].strip(), call.data["code"]
        door_ids = _door_ids(coords, call.data["doors"])
        # dubbelcheck op alle gekozen deuren vóór er iets geschreven wordt
        for did in door_ids:
            c = coords[did]
            for r in c.codes:
                if r.get("CommonPassword") == code:
                    raise HomeAssistantError(f"code bestaat al op {c.door_name} ({r.get('UserID')})")
        doors = {}
        for did in door_ids:
            c = coords[did]
            try:
                recno = await hass.async_add_executor_job(c._run, c.client.add_code, name, code)
            except (VTOError, OSError) as e:
                raise HomeAssistantError(f"{c.door_name}: {e}") from e
            doors[did] = recno
        cid = await reg.add(name, code, doors)
        for did in door_ids:
            await coords[did].async_refresh_codes()
        return {"id": cid, "doors": doors}

    async def update_code(call: ServiceCall):
        coords, reg = _get(hass, entry_id)
        cid = call.data["id"]
        if cid not in reg.managed:
            raise HomeAssistantError("onbekende id: enkel codes die via de integratie zijn aangemaakt kunnen gewijzigd worden")
        m = reg.managed[cid]
        name = call.data.get("name", m["name"]).strip()
        code = call.data.get("code", m["code"])
        want = set(_door_ids(coords, call.data["doors"])) if "doors" in call.data else set(m["doors"])
        doors = dict(m["doors"])
        # bestaande deuren: update; weggehaalde deuren: remove; nieuwe deuren: insert. Altijd met vergrendelingscheck.
        for did, recno in list(doors.items()):
            c = coords[did]
            if not reg.may_write(did, recno):
                raise HomeAssistantError(f"{c.door_name}: RecNo {recno} is beschermd")
            try:
                if did in want:
                    await hass.async_add_executor_job(c._run, c.client.update_code, recno, name, code)
                else:
                    await hass.async_add_executor_job(c._run, c.client.remove_code, recno)
                    doors.pop(did)
            except (VTOError, OSError) as e:
                raise HomeAssistantError(f"{c.door_name}: {e}") from e
        for did in want - set(doors):
            c = coords[did]
            try:
                doors[did] = await hass.async_add_executor_job(c._run, c.client.add_code, name, code)
            except (VTOError, OSError) as e:
                raise HomeAssistantError(f"{c.door_name}: {e}") from e
        await reg.update(cid, name, code, doors)
        for did in set(want) | set(m["doors"]):
            await coords[did].async_refresh_codes()
        return {"id": cid, "doors": doors}

    async def remove_code(call: ServiceCall):
        coords, reg = _get(hass, entry_id)
        cid = call.data["id"]
        if cid not in reg.managed:
            raise HomeAssistantError("onbekende id: bestaande codes kunnen niet verwijderd worden")
        m = reg.managed[cid]
        for did, recno in m["doors"].items():
            c = coords[did]
            if not reg.may_write(did, recno):
                raise HomeAssistantError(f"{c.door_name}: RecNo {recno} is beschermd")
            try:
                await hass.async_add_executor_job(c._run, c.client.remove_code, recno)
            except (VTOError, OSError) as e:
                raise HomeAssistantError(f"{c.door_name}: {e}") from e
        await reg.remove(cid)
        for did in m["doors"]:
            await coords[did].async_refresh_codes()

    async def refresh(call: ServiceCall):
        coords, _ = _get(hass, entry_id)
        for c in coords.values():
            await c.async_refresh_codes()

    async def list_codes(call: ServiceCall):
        coords, reg = _get(hass, entry_id)
        rows = {}
        for did, c in coords.items():
            for r in c.codes:
                key = (r.get("UserID", "").strip(), r.get("CommonPassword"))
                row = rows.setdefault(key, {"name": key[0], "code": key[1], "doors": {}, "managed": False, "id": None})
                row["doors"][c.door_name] = r["RecNo"]
        for cid, m in reg.managed.items():
            row = rows.get((m["name"], m["code"]))
            if row:
                row["managed"], row["id"] = True, cid
        return {"codes": sorted(rows.values(), key=lambda x: x["name"].lower()), "registry": reg.export()}

    hass.services.async_register(DOMAIN, "add_code", add_code, vol.Schema({
        vol.Required("name"): cv.string, vol.Required("code"): CODE_SCHEMA,
        vol.Required("doors"): vol.All(cv.ensure_list, [cv.string])}), supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "update_code", update_code, vol.Schema({
        vol.Required("id"): cv.string, vol.Optional("name"): cv.string, vol.Optional("code"): CODE_SCHEMA,
        vol.Optional("doors"): vol.All(cv.ensure_list, [cv.string])}), supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "remove_code", remove_code, vol.Schema({vol.Required("id"): cv.string}))
    hass.services.async_register(DOMAIN, "refresh", refresh)
    hass.services.async_register(DOMAIN, "list_codes", list_codes, supports_response=SupportsResponse.ONLY)
