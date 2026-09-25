"""WebSocket-commando's voor de dashboardkaarten (deuren, toegangshistoriek, codes)."""
import time
from datetime import date, timedelta

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN

ARCHIVE_KEY = f"{DOMAIN}_archive"


def _coords(hass):
    out = {}
    for d in hass.data.get(DOMAIN, {}).values():
        out.update(d["coords"])
    return out


@callback
def async_register(hass: HomeAssistant):
    websocket_api.async_register_command(hass, ws_doors)
    websocket_api.async_register_command(hass, ws_history)
    websocket_api.async_register_command(hass, ws_codes)


def _day_start(hass, days_back: int = 0) -> int:
    """Middernacht (tijdzone van Home Assistant) van vandaag min days_back dagen; correct rond zomer/wintertijd."""
    return int(dt_util.start_of_local_day(dt_util.now().date() - timedelta(days=days_back)).timestamp())


def _date_start(value: str, extra_days: int = 0) -> int:
    return int(dt_util.start_of_local_day(date.fromisoformat(value) + timedelta(days=extra_days)).timestamp())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/doors"})
@websocket_api.async_response
async def ws_doors(hass, connection, msg):
    """Alle geladen deuren, met entiteiten en de tellers van vandaag (uit het archief)."""
    ent = er.async_get(hass)
    archive = hass.data.get(ARCHIVE_KEY)
    today, stats = {}, {}
    if archive is not None:
        today = await hass.async_add_executor_job(archive.counts_since, _day_start(hass))
        stats = await hass.async_add_executor_job(archive.stats)
    doors = []
    for did, c in sorted(_coords(hass).items(), key=lambda x: x[1].door_name.lower()):
        doors.append({
            "id": did,
            "name": c.door_name,
            "available": bool(c.last_update_success),
            "last_unlock": c.last_unlock,
            "recent": c.recent[:10],
            "codes": len(c.codes),
            "cards": len(c.cards),
            "today": today.get(did, {"opened": 0, "refused": 0}),
            "entities": {
                k: ent.async_get_entity_id("sensor", DOMAIN, f"{did}_{u}")
                for k, u in (("last_unlock", "last_unlock"), ("codes", "codes"), ("cards", "cards"))
            },
        })
    connection.send_result(msg["id"], {"doors": doors, "archive": stats, "time_zone": hass.config.time_zone})


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/history",
    vol.Optional("door_ids"): [str],
    vol.Optional("search"): str,
    vol.Optional("person"): str,
    vol.Optional("status", default="all"): vol.In(["all", "opened", "refused"]),
    vol.Optional("start"): vol.Coerce(int),
    vol.Optional("end"): vol.Coerce(int),
    vol.Optional("days"): vol.All(vol.Coerce(int), vol.Range(min=1, max=800)),
    vol.Optional("date_from"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
    vol.Optional("date_to"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
    vol.Optional("limit", default=200): vol.All(vol.Coerce(int), vol.Range(min=0, max=50000)),
    vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    vol.Optional("max_id"): vol.All(vol.Coerce(int), vol.Range(min=0)),
})
@websocket_api.async_response
async def ws_history(hass, connection, msg):
    archive = hass.data.get(ARCHIVE_KEY)
    if archive is None:
        connection.send_error(msg["id"], "not_ready", "Toegangsarchief is nog niet geladen")
        return
    tz = dt_util.get_default_time_zone()
    kw = {k: msg[k] for k in ("door_ids", "search", "person", "status", "start", "end", "limit", "offset", "max_id") if k in msg}
    try:
        if "days" in msg:
            kw["start"] = _day_start(hass, msg["days"] - 1)
        if "date_from" in msg:
            kw["start"] = _date_start(msg["date_from"])
        if "date_to" in msg:
            kw["end"] = _date_start(msg["date_to"], 1)
    except ValueError as e:
        connection.send_error(msg["id"], "invalid_format", f"ongeldige datum: {e}")
        return
    t0 = time.monotonic()
    res = await hass.async_add_executor_job(lambda: archive.query(tz, **kw))
    res["query_ms"] = int((time.monotonic() - t0) * 1000)
    connection.send_result(msg["id"], res)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/codes"})
@callback
def ws_codes(hass, connection, msg):
    """Codes en badges per persoon over alle deuren heen (enkel voor beheerders)."""
    coords = _coords(hass)
    people = {}
    for did, c in coords.items():
        for r in c.codes:
            n = (r.get("UserID") or "").strip() or "?"
            people.setdefault(n.lower(), {"name": n, "codes": {}, "cards": {}})["codes"].setdefault(did, []).append(r.get("CommonPassword", ""))
        for r in c.cards:
            n = (r.get("CardName") or r.get("UserID") or "").strip() or "?"
            people.setdefault(n.lower(), {"name": n, "codes": {}, "cards": {}})["cards"].setdefault(did, []).append(r.get("CardNo", ""))
    doors = [{"id": did, "name": c.door_name} for did, c in sorted(coords.items(), key=lambda x: x[1].door_name.lower())]
    connection.send_result(msg["id"], {"doors": doors, "people": sorted(people.values(), key=lambda p: p["name"].lower())})
