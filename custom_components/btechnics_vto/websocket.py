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
USER_OPS_KEY = f"{DOMAIN}_user_ops"
# gebruikers zonder beheerrechten: enkel eigen tijdelijke codes, met deze grenzen
USER_MAX_ACTIVE = 10
USER_MAX_DAYS = 7
USER_MAX_AHEAD_DAYS = 31


def _admin(connection) -> bool:
    return bool(connection.user and connection.user.is_admin)


def _coords(hass):
    out = {}
    for d in hass.data.get(DOMAIN, {}).values():
        out.update(d["coords"])
    return out


@callback
def async_register(hass: HomeAssistant):
    websocket_api.async_register_command(hass, ws_doors)
    websocket_api.async_register_command(hass, ws_errors)
    websocket_api.async_register_command(hass, ws_history)
    websocket_api.async_register_command(hass, ws_codes)
    websocket_api.async_register_command(hass, ws_manage_list)
    websocket_api.async_register_command(hass, ws_manage_action)
    websocket_api.async_register_command(hass, ws_user_codes)
    websocket_api.async_register_command(hass, ws_user_add)
    websocket_api.async_register_command(hass, ws_user_stop)
    websocket_api.async_register_command(hass, ws_user_days)


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
    admin = _admin(connection)
    if not admin:
        # gebruiker zonder beheerrechten: enkel status en aantallen van vandaag, geen namen, uren of foto's
        for d in doors:
            d.update(last_unlock=None, recent=[], codes=None, cards=None)
        stats = {}
    connection.send_result(msg["id"], {"doors": doors, "archive": stats, "time_zone": hass.config.time_zone, "admin": admin})


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
    if mgr is not None:
        # wie de tijdelijke code maakte (gebruiker zonder beheerrechten)
        for e in entries:
            uid = mgr.reg.managed[e["id"]].get("owner")
            if uid:
                u = await hass.auth.async_get_user(uid)
                e["owner_name"] = u.name if u and u.name else "onbekend"
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


# ---------------------------------------------------------------- gebruikers zonder beheerrechten

def _entry_out(cid, m, names):
    from .registry import secret
    return {
        "id": cid, "kind": m["kind"], "name": m["name"], "secret": secret(m), "status": m["status"],
        "until": m.get("until"), "valid_from": m.get("valid_from"), "valid_until": m.get("valid_until"),
        "max_uses": m.get("max_uses"), "uses": m.get("uses") or 0, "created": m.get("created"),
        "doors": sorted(({"id": d, "name": names.get(d, d)} for d in m["doors"]), key=lambda x: x["name"].lower()),
        "stored": sorted(({"id": d, "name": names.get(d, d)} for d in m["stored"]), key=lambda x: x["name"].lower()),
    }


def _user_error(e, connection) -> str:
    """Foutmelding voor een gebruiker zonder beheerrechten: geen namen van anderen of technische details."""
    txt = str(e)
    if _admin(connection) or "tijdelijke codes" in txt or "verleden" in txt or "na het begin" in txt:
        return txt
    if "niet overal" in txt or "Nog actief" in txt:
        return "niet op alle deuren gelukt (een toestel is niet bereikbaar); Home Assistant probeert het elke minuut opnieuw"
    if "onbekende deur" in txt:
        return "onbekende deur"
    return "niet gelukt; probeer opnieuw of vraag het aan een beheerder"


def _own_codes(mgr, uid):
    return [(cid, m) for cid, m in mgr.reg.managed.items() if m.get("kind") == "code" and m.get("owner") == uid]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/user/codes"})
@callback
def ws_user_codes(hass, connection, msg):
    """Eigen tijdelijke codes (ook voor gebruikers zonder beheerrechten); uit dienst enkel de laatste 7 dagen."""
    from .manage import MANAGER_KEY
    mgr = hass.data.get(MANAGER_KEY)
    coords = _coords(hass)
    names = {did: c.door_name for did, c in coords.items()}
    recent = (dt_util.now() - timedelta(days=7)).isoformat()
    entries = []
    if mgr is not None and connection.user:
        for cid, m in _own_codes(mgr, connection.user.id):
            if m["status"] == "retired" and (m.get("updated") or "") < recent:
                continue
            entries.append(_entry_out(cid, m, names))
    entries.sort(key=lambda e: (e["status"] == "retired", e.get("valid_until") or "", e["name"].lower()))
    doors = [{"id": did, "name": c.door_name} for did, c in sorted(coords.items(), key=lambda x: x[1].door_name.lower())]
    connection.send_result(msg["id"], {"doors": doors, "entries": entries, "audit": [], "unknown_cards": [], "admin": _admin(connection),
                                       "limits": {"active": USER_MAX_ACTIVE, "days": USER_MAX_DAYS, "ahead_days": USER_MAX_AHEAD_DAYS}})


def _local_span(end, start):
    """Duur op de klok van Home Assistant (7 dagen blijft 7 dagen, ook over de wissel naar zomer- of wintertijd)."""
    return dt_util.as_local(end).replace(tzinfo=None) - dt_util.as_local(start).replace(tzinfo=None)


def _local_dt(value: str):
    v = dt_util.parse_datetime(value)
    if v is None:
        raise ValueError(f"ongeldig tijdstip: {value}")
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(v)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/user/add",
    vol.Required("name"): vol.All(str, vol.Length(min=1, max=30)),
    vol.Required("doors"): vol.All([str], vol.Length(min=1)),
    vol.Optional("valid_from"): str,
    vol.Required("valid_until"): str,
    vol.Optional("max_uses"): vol.In([1]),
})
@websocket_api.async_response
async def ws_user_add(hass, connection, msg):
    """Tijdelijke code maken als gebruiker: code door Home Assistant gekozen, maximaal 7 dagen, maximaal 10 actief."""
    from types import SimpleNamespace
    from homeassistant.exceptions import HomeAssistantError
    from .manage import MANAGER_KEY
    mgr, ops = hass.data.get(MANAGER_KEY), hass.data.get(USER_OPS_KEY)
    if mgr is None or ops is None or not connection.user:
        connection.send_error(msg["id"], "not_ready", "nog niet klaar")
        return
    try:
        vuntil = _local_dt(msg["valid_until"])
        vfrom = _local_dt(msg["valid_from"]) if msg.get("valid_from") else None
    except ValueError as e:
        connection.send_error(msg["id"], "invalid_format", str(e))
        return
    now = dt_util.utcnow()
    start = max(now, vfrom) if vfrom else now
    err = None
    if vuntil <= now:
        err = "het einde ligt in het verleden"
    elif vfrom and vuntil <= vfrom:
        err = "het einde moet na het begin liggen"
    elif _local_span(vuntil, start) > timedelta(days=USER_MAX_DAYS, minutes=1):
        err = f"een tijdelijke code is maximaal {USER_MAX_DAYS} dagen geldig"
    elif vfrom and vfrom - now > timedelta(days=USER_MAX_AHEAD_DAYS):
        err = f"het begin mag maximaal {USER_MAX_AHEAD_DAYS} dagen vooruit liggen"
    elif sum(1 for _, m in _own_codes(mgr, connection.user.id) if m["status"] != "retired") >= USER_MAX_ACTIVE:
        err = f"je hebt al {USER_MAX_ACTIVE} actieve tijdelijke codes; stop er eerst een"
    if err:
        connection.send_error(msg["id"], "failed", err)
        return
    # eigenaar en limiet gaan mee naar het toevoegen zelf: daar worden ze onder het schrijfslot nagekeken en gezet
    # (twee gelijktijdige aanvragen kunnen de limiet zo niet samen overschrijden)
    data = {"name": msg["name"].strip(), "doors": msg["doors"], "valid_until": vuntil,
            "owner": connection.user.id, "owner_limit": USER_MAX_ACTIVE}
    if vfrom and vfrom > now:
        data["valid_from"] = vfrom
    if msg.get("max_uses"):
        data["max_uses"] = 1
    try:
        res = await ops["add"](SimpleNamespace(data=data, context=connection.context(msg)))
    except (HomeAssistantError, vol.Invalid) as e:
        connection.send_error(msg["id"], "failed", _user_error(e, connection))
        return
    cid = res and res.get("id")
    connection.send_result(msg["id"], {"ok": True, "id": cid, "result": {"id": cid}})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/user/stop", vol.Required("entry"): str})
@websocket_api.async_response
async def ws_user_stop(hass, connection, msg):
    """Eigen tijdelijke code stoppen (uit dienst)."""
    from types import SimpleNamespace
    from homeassistant.exceptions import HomeAssistantError
    from .manage import MANAGER_KEY
    mgr, ops = hass.data.get(MANAGER_KEY), hass.data.get(USER_OPS_KEY)
    m = mgr.reg.managed.get(msg["entry"]) if mgr is not None else None
    if m is None or ops is None or not connection.user or (m.get("owner") != connection.user.id and not _admin(connection)):
        connection.send_error(msg["id"], "not_found", "onbekende code")
        return
    if m["status"] == "retired" and not m.get("doors"):
        connection.send_error(msg["id"], "failed", "deze code is al gestopt")
        return
    try:
        await ops["retire"](SimpleNamespace(data={"id": msg["entry"]}, context=connection.context(msg)))
    except (HomeAssistantError, vol.Invalid) as e:
        connection.send_error(msg["id"], "failed", _user_error(e, connection))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/user/days",
    vol.Optional("door_ids"): [str],
    vol.Optional("days"): vol.All(vol.Coerce(int), vol.Range(min=1, max=400)),
    vol.Optional("date_from"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
    vol.Optional("date_to"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
})
@websocket_api.async_response
async def ws_user_days(hass, connection, msg):
    """Beperkte historiek voor elke gebruiker: enkel aantallen per dag en per deur, geen namen, uren of foto's."""
    archive = hass.data.get(ARCHIVE_KEY)
    if archive is None:
        connection.send_error(msg["id"], "not_ready", "Toegangsarchief is nog niet geladen")
        return
    kw = {"limit": 0}
    if msg.get("door_ids"):
        kw["door_ids"] = msg["door_ids"]
    try:
        kw["start"] = _date_start(msg["date_from"]) if "date_from" in msg else _day_start(hass, msg.get("days", 30) - 1)
        if "date_to" in msg:
            kw["end"] = _date_start(msg["date_to"], 1)
    except ValueError as e:
        connection.send_error(msg["id"], "invalid_format", f"ongeldige datum: {e}")
        return
    if kw["start"] < _day_start(hass, 399):
        kw["start"] = _day_start(hass, 399)
    tz = dt_util.get_default_time_zone()
    r = await hass.async_add_executor_job(lambda: archive.query(tz, **kw))
    connection.send_result(msg["id"], {
        "total": r["total"], "opened": r["opened"], "refused": r["refused"],
        "days": [{"day": d["day"], "count": d["count"], "opened": d["opened"], "refused": d["refused"], "doors": d["doors"]} for d in r["days"]],
    })



@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/errors",
                                  vol.Optional("days", default=60): vol.All(vol.Coerce(int), vol.Range(min=1, max=60))})
@websocket_api.async_response
async def ws_errors(hass, connection, msg):
    """Foutenlog van de laatste 60 dagen (waarschuwingen en fouten van deze integratie), enkel beheerders."""
    log = hass.data.get(f"{DOMAIN}_errorlog")
    if log is None:
        connection.send_error(msg["id"], "not_ready", "foutenlog niet beschikbaar")
        return
    await hass.async_add_executor_job(log.flush_to_db)
    connection.send_result(msg["id"], await hass.async_add_executor_job(log.query, msg["days"]))
