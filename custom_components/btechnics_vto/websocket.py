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
    websocket_api.async_register_command(hass, ws_manage_list)
    websocket_api.async_register_command(hass, ws_manage_action)


def _no_card(r):
    return {k: v for k, v in r.items() if k != "card"} if r else r


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
    photos = hass.data.get(f"{DOMAIN}_photos")
    doors = []
    for did, c in sorted(_coords(hass).items(), key=lambda x: x[1].door_name.lower()):
        doors.append({
            "id": did,
            "name": c.door_name,
            "available": bool(c.last_update_success),
            # zonder kaartnummers: dit commando is voor elke gebruiker
            "last_unlock": _no_card(c.last_unlock),
            "recent": [_no_card(r) for r in c.recent[:10]],
            "events": getattr(c, "events_state", None),
            "codes": len(c.codes),
            "cards": len(c.cards),
            "today": today.get(did, {"opened": 0, "refused": 0}),
            "entities": {
                k: ent.async_get_entity_id("sensor", DOMAIN, f"{did}_{u}")
                for k, u in (("last_unlock", "last_unlock"), ("codes", "codes"), ("cards", "cards"))
            },
        })
    if photos is not None:
        # foto's koppelen aan de recente toegangen (enkel het nummer; de foto zelf is enkel voor beheerders)
        rows = [r for d in doors for r in d["recent"] if r.get("door_id") and r.get("ts")]
        try:
            await hass.async_add_executor_job(photos.match, rows)
            for d in doors:
                u = d["last_unlock"]
                if u:
                    same = next((r for r in d["recent"] if r.get("ts") == u.get("ts") and "photo" in r), None)
                    if same:
                        u["photo"] = same["photo"]
                    elif u.get("ts"):
                        one = [dict(u)]
                        await hass.async_add_executor_job(photos.match, one)
                        if "photo" in one[0]:
                            u["photo"] = one[0]["photo"]
        except Exception:  # noqa: BLE001  foto's zijn een extraatje
            pass
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
    photos = hass.data.get(f"{DOMAIN}_photos")

    def _q():
        r = archive.query(tz, **kw)
        if photos is not None:
            photos.match(r["rows"])
        return r

    res = await hass.async_add_executor_job(_q)
    _remote_users(hass, res["rows"])
    res["query_ms"] = int((time.monotonic() - t0) * 1000)
    connection.send_result(msg["id"], res)


def _remote_users(hass, rows):
    """Bij een opening op afstand de gebruiker uit Wijzigingen tonen (zelfde deur, binnen 2 minuten)."""
    from homeassistant.util import dt as dt_util
    from .manage import MANAGER_KEY
    mgr = hass.data.get(MANAGER_KEY)
    if mgr is None:
        return
    opens = []
    for a in mgr.reg.audit:
        if a.get("action") == "deur geopend op afstand":
            t = dt_util.parse_datetime(a.get("ts") or "")
            if t is not None:
                opens.append((t.timestamp(), a.get("name"), a.get("user")))
    for r in rows:
        if r.get("name") == "Op afstand":
            hits = sorted((abs(t - r["ts"]), user) for t, door, user in opens if door == r.get("door") and abs(t - r["ts"]) <= 120)
            if hits:
                r["method"] = f"op afstand door {hits[0][1]}"


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


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/manage/list"})
@websocket_api.async_response
async def ws_manage_list(hass, connection, msg):
    """Alle codes en badges met status, plus de recentste wijzigingen (enkel voor beheerders)."""
    from .manage import MANAGER_KEY
    from .registry import secret
    coords = _coords(hass)
    mgr = hass.data.get(MANAGER_KEY)
    names = {did: c.door_name for did, c in coords.items()}
    entries = []
    if mgr is not None:
        for cid, m in mgr.reg.managed.items():
            entries.append({
                "id": cid, "kind": m["kind"], "name": m["name"], "secret": secret(m), "status": m["status"],
                "until": m.get("until"), "valid_from": m.get("valid_from"), "valid_until": m.get("valid_until"),
                "max_uses": m.get("max_uses"), "uses": m.get("uses") or 0, "source": m.get("source"), "created": m.get("created"), "updated": m.get("updated"),
                "doors": sorted(({"id": d, "name": names.get(d, d)} for d in m["doors"]), key=lambda x: x["name"].lower()),
                "stored": sorted(({"id": d, "name": names.get(d, d)} for d in m["stored"]), key=lambda x: x["name"].lower()),
            })
    entries.sort(key=lambda e: (e["name"].lower(), e["kind"]))
    doors = [{"id": did, "name": c.door_name} for did, c in sorted(coords.items(), key=lambda x: x[1].door_name.lower())]
    audit = list(reversed(mgr.reg.audit[-200:])) if mgr is not None else []
    # onbekende badges van de laatste 14 dagen (voor Nieuwe badge), zonder badges die al in de lijst staan
    unknown = []
    archive = hass.data.get(ARCHIVE_KEY)
    if archive is not None:
        known = {(e["secret"] or "").upper() for e in entries if e["kind"] == "badge"}
        for c in coords.values():
            known.update((r.get("CardNo") or "").upper() for r in c.cards)
        try:
            rows = await hass.async_add_executor_job(archive.unknown_cards, _day_start(hass, 13))
        except Exception:  # noqa: BLE001  archief is een hulpmiddel, nooit blokkerend
            rows = []
        unknown = [r for r in rows if r["card"] not in known]
    connection.send_result(msg["id"], {"doors": doors, "entries": entries, "audit": audit, "unknown_cards": unknown})


ACTIONS = {
    # actie: (service, velden, antwoord)
    "add": ("add_code", ("name", "code", "doors", "valid_from", "valid_until", "max_uses"), True),
    "validity": ("set_validity", ("id", "valid_from", "valid_until"), True),
    "update": ("update_code", ("id", "name", "code", "doors"), True),
    "rename_badge": ("rename_badge", ("id", "name"), True),
    "add_badge": ("add_badge", ("name", "card", "doors"), True),
    "block": ("block", ("id", "until"), True),
    "unblock": ("unblock", ("id",), True),
    "retire": ("retire", ("id",), True),
    "restore": ("restore", ("id",), True),
    "forget": ("forget", ("id",), True),
    "remove": ("remove_code", ("id",), False),
}


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manage/action",
    vol.Required("action"): vol.In(list(ACTIONS)),
    vol.Optional("entry"): str,     # id van de code of badge ("id" is het berichtnummer van de WebSocket)
    vol.Optional("name"): str,
    vol.Optional("code"): str,
    vol.Optional("card"): str,
    vol.Optional("doors"): [str],
    vol.Optional("until"): str,
    vol.Optional("valid_from"): str,
    vol.Optional("valid_until"): str,
    vol.Optional("max_uses"): int,
})
@websocket_api.async_response
async def ws_manage_action(hass, connection, msg):
    """Voert een beheeractie uit via de gewone services (zelfde controles, logboek met de gebruiker)."""
    from homeassistant.exceptions import HomeAssistantError
    service, fields, response = ACTIONS[msg["action"]]
    src = {**msg, "id": msg.get("entry")}
    data = {k: src[k] for k in fields if k in src and src[k] not in (None, "")}
    try:
        res = await hass.services.async_call(
            DOMAIN, service, data, blocking=True, context=connection.context(msg), return_response=response
        )
    except (HomeAssistantError, vol.Invalid) as e:
        connection.send_error(msg["id"], "failed", str(e))
        return
    connection.send_result(msg["id"], {"ok": True, "result": res})
