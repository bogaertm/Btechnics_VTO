"""Tests voor het jaararchief, de WebSocket-API van de kaarten en het toevoegen van deuren."""
from datetime import datetime, timezone
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.btechnics_vto.const import DOMAIN
from custom_components.btechnics_vto.websocket import ARCHIVE_KEY

from . import fake_vto
from .fake_vto import FakeClient, FakeDevice
from .test_integration import T0, coord, devices, door, events, fill, poll, restart, setup_two_entries  # noqa: F401


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp())


def archive(hass):
    return hass.data[ARCHIVE_KEY]


def rows(hass, door_id=None):
    import sqlite3
    c = sqlite3.connect(archive(hass)._path)
    q = "SELECT door_id, ts, name, status FROM access" + (" WHERE door_id = ?" if door_id else "") + " ORDER BY id"
    out = c.execute(q, (door_id,) if door_id else ()).fetchall()
    c.close()
    return out


# ---------------------------------------------------------------- archief

async def test_backfill_neemt_volledige_buffer_over(hass, devices):
    cafe, kam = devices
    fill(cafe, 1000)
    fill(kam, 250)
    await setup_two_entries(hass)
    assert len(rows(hass, "cafe")) == 1000 and len(rows(hass, "kammerstraat")) == 250
    assert rows(hass, "cafe")[0][2] == "Oud0" and rows(hass, "cafe")[-1][2] == "Oud999"


async def test_archief_groeit_voorbij_de_buffer_zonder_dubbels(hass, devices):
    cafe, _ = devices
    fill(cafe, 1000)
    entries = await setup_two_entries(hass)
    for i in range(1500):
        cafe.add_log(T0 + i * 60, name=f"N{i}")
        if i % 97 == 0:
            await poll(hass, "cafe")
    await poll(hass, "cafe")
    await restart(hass, entries)
    await poll(hass, "cafe")
    names = [r[2] for r in rows(hass, "cafe")]
    assert len(names) == 2500 == len(set(names))          # 1000 oud + 1500 nieuw, alles precies één keer
    assert len(cafe.log) == 1000                          # het toestel zelf houdt er maar 1000


async def test_onvolledige_backfill_herstelt_zichzelf(hass, devices):
    cafe, _ = devices
    fill(cafe, 1000)
    cafe.partial_next = 300
    await setup_two_entries(hass)
    assert len(rows(hass, "cafe")) == 300
    await poll(hass, "cafe")
    names = [r[2] for r in rows(hass, "cafe")]
    assert names == [f"Oud{i}" for i in range(1000)]      # volledig en in de juiste volgorde


async def test_identieke_records_allebei_bewaard(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    cafe.add_log(T0, name="?", status=0)
    await poll(hass, "cafe")
    cafe.add_log(T0, name="?", status=0)
    await poll(hass, "cafe")
    assert len(rows(hass, "cafe")) == 2


async def test_bestaande_installatie_krijgt_ook_backfill(hass, devices, hass_storage):
    from custom_components.btechnics_vto.const import STORAGE_KEY
    cafe, _ = devices
    fill(cafe, 500)
    # v0.2.0: doorloopunt bestaat al, archief nog niet
    tail = [[str(r["CreateTime"]), "", r["UserID"], "0", "1"] for r in cafe.log[-10:]]
    hass_storage[STORAGE_KEY] = {"version": 1, "minor_version": 1, "key": STORAGE_KEY, "data": {
        "managed": {}, "protected": {}, "log_state": {"cafe": {"tail": tail, "t": cafe.log[-1]["CreateTime"]}}}}
    await setup_two_entries(hass)
    assert len(rows(hass, "cafe")) == 500


async def test_opruimen_na_400_dagen(hass, devices):
    cafe, _ = devices
    cafe.add_log(T0 - 401 * 86400, name="TeOud")
    cafe.add_log(T0 - 399 * 86400, name="NetGoed")
    await setup_two_entries(hass)
    n = await hass.async_add_executor_job(archive(hass).prune, T0)
    assert n == 1 and [r[2] for r in rows(hass, "cafe")] == ["NetGoed"]


async def test_archieffout_blokkeert_de_deur_niet(hass, devices, events):
    cafe, _ = devices
    await setup_two_entries(hass)
    with patch.object(type(archive(hass)), "sync", side_effect=OSError("schijf vol")):
        cafe.add_log(T0, name="Toch")
        await poll(hass, "cafe")
    assert coord(hass, "cafe").last_update_success is True
    assert [e["name"] for e in events] == ["Toch"]


# ---------------------------------------------------------------- websocket: historiek

async def seed(hass, devices):
    cafe, kam = devices
    cafe.add_log(utc(2026, 9, 30, 22, 30), name="Eliot")               # 1 okt 00:30 in Brussel
    cafe.add_log(utc(2026, 9, 30, 21, 30), name="Eliot")               # 30 sep 23:30 in Brussel
    cafe.add_log(utc(2026, 10, 2, 8, 0), name="?", method=20, status=0)
    cafe.add_log(utc(2026, 10, 2, 9, 0), name="Jan Hoozee", method=1, card="ECBA62F1")
    kam.add_log(utc(2026, 10, 2, 9, 0), name="Aardig")
    kam.add_log(utc(2026, 10, 3, 9, 0), name="eliot")                  # zelfde persoon, andere schrijfwijze
    await setup_two_entries(hass)


async def hist(client, **kw):
    await client.send_json_auto_id({"type": f"{DOMAIN}/history", **kw})
    msg = await client.receive_json()
    assert msg["success"], msg
    return msg["result"]


async def test_ws_historiek_filters(hass, devices, hass_ws_client):
    await seed(hass, devices)
    c = await hass_ws_client(hass)
    r = await hist(c)
    assert r["total"] == 6 and r["opened"] == 5 and r["refused"] == 1
    names = [x["name"] for x in r["rows"]]
    assert names[0] == "eliot" and set(names[1:3]) == {"Aardig", "Jan Hoozee"}  # recentste eerst (1 en 2 zelfde seconde)
    assert [x["ts"] for x in r["rows"]] == sorted((x["ts"] for x in r["rows"]), reverse=True)
    assert (await hist(c, door_ids=["kammerstraat"]))["total"] == 2
    assert (await hist(c, search="ELI"))["total"] == 3                          # hoofdletterongevoelig, beide deuren
    assert (await hist(c, search="ecba62"))["rows"][0]["name"] == "Jan Hoozee"  # op badgenummer
    assert (await hist(c, person="Eliot"))["total"] == 3
    assert (await hist(c, person="?"))["total"] == 1
    assert (await hist(c, search="100%"))["total"] == 0                          # geen SQL-jokers
    ref = await hist(c, status="refused")
    assert ref["total"] == 1 and ref["rows"][0]["method"] == "ongeldige invoer klavier" and ref["rows"][0]["name"] == "?"
    assert (await hist(c, status="opened"))["total"] == 5
    page = await hist(c, limit=2, offset=2)
    assert page["total"] == 6 and len(page["rows"]) == 2
    people = {p["name"]: p for p in r["people"]}
    assert people["Eliot"]["count"] == 3 and set(people["Eliot"]["doors"]) == {"Cafe", "Kammerstraat"}
    # maanden in de tijdzone van Home Assistant, niet in UTC
    assert r["months"] == [{"month": "2026-09", "count": 1, "opened": 1}, {"month": "2026-10", "count": 5, "opened": 4}]


async def test_ws_historiek_datumbereik_in_lokale_tijd(hass, devices, hass_ws_client):
    await seed(hass, devices)
    c = await hass_ws_client(hass)
    r = await hist(c, date_from="2026-10-01", date_to="2026-10-01")
    assert [x["ts"] for x in r["rows"]] == [utc(2026, 9, 30, 22, 30)]            # enkel 1 okt lokale tijd
    r = await hist(c, date_from="2026-09-30", date_to="2026-09-30")
    assert [x["ts"] for x in r["rows"]] == [utc(2026, 9, 30, 21, 30)]
    await c.send_json_auto_id({"type": f"{DOMAIN}/history", "date_from": "2026-13-01"})
    assert not (await c.receive_json())["success"]


async def test_ws_historiek_dagen(hass, devices, hass_ws_client):
    cafe, _ = devices
    from homeassistant.util import dt as dt_util
    now = int(dt_util.utcnow().timestamp())
    cafe.add_log(now - 60, name="Net")
    cafe.add_log(now - 3 * 86400, name="DrieDagen")
    cafe.add_log(now - 200 * 86400, name="Lang")
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    assert [x["name"] for x in (await hist(c, days=1))["rows"]] == ["Net"]
    assert [x["name"] for x in (await hist(c, days=7))["rows"]] == ["Net", "DrieDagen"]
    assert (await hist(c, days=365))["total"] == 3


# ---------------------------------------------------------------- websocket: deuren en codes

async def test_ws_deuren_en_vandaag(hass, devices, hass_ws_client):
    cafe, _ = devices
    from homeassistant.util import dt as dt_util
    now = int(dt_util.utcnow().timestamp())
    cafe.add_log(now - 30, name="A")
    cafe.add_log(now - 20, name="B", status=0)
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    await c.send_json_auto_id({"type": f"{DOMAIN}/doors"})
    r = (await c.receive_json())["result"]
    d = {x["id"]: x for x in r["doors"]}
    assert set(d) == {"cafe", "kammerstraat"}
    assert d["cafe"]["today"] == {"opened": 1, "refused": 1}
    assert d["cafe"]["last_unlock"]["name"] == "B" and d["cafe"]["available"] is True
    assert d["cafe"]["entities"]["last_unlock"] == "sensor.vto_cafe_laatste_unlock"
    assert r["time_zone"] == "Europe/Brussels" and r["archive"]["cafe"]["count"] == 2


async def test_ws_codes_enkel_voor_beheerders(hass, devices, hass_ws_client, hass_read_only_access_token):
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    await c.send_json_auto_id({"type": f"{DOMAIN}/codes"})
    r = (await c.receive_json())["result"]
    assert [d["name"] for d in r["doors"]] == ["Cafe", "Kammerstraat"]
    adriaan = next(p for p in r["people"] if p["name"] == "Adriaan")
    assert adriaan["codes"] == {"cafe": ["936100"]}
    ro = await hass_ws_client(hass, hass_read_only_access_token)
    await ro.send_json_auto_id({"type": f"{DOMAIN}/codes"})
    msg = await ro.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------- deuren toevoegen

async def flow_add(hass, **kw):
    data = {"name": "Achterdeur", "host": "10.0.0.3", "https": False, "username": "admin", "password": "x", **kw}
    with patch("custom_components.btechnics_vto.config_flow.VTOClient", FakeClient):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], data)
    return r


async def test_nieuwe_vto_toevoegen_verschijnt_overal(hass, devices, hass_ws_client):
    await setup_two_entries(hass)
    back = FakeDevice("10.0.0.3")
    fake_vto.DEVICES["10.0.0.3"] = back
    fill(back, 20)
    r = await flow_add(hass)
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "more"
    with patch("custom_components.btechnics_vto.VTOClient", FakeClient), \
         patch("custom_components.btechnics_vto.config_flow.VTOClient", FakeClient):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"add_another": False})
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["title"] == "Achterdeur"
    assert hass.states.get("sensor.vto_achterdeur_laatste_unlock").state == "Oud19"
    c = await hass_ws_client(hass)
    await c.send_json_auto_id({"type": f"{DOMAIN}/doors"})
    names = [d["name"] for d in (await c.receive_json())["result"]["doors"]]
    assert names == ["Achterdeur", "Cafe", "Kammerstraat"]
    assert len(rows(hass, "achterdeur")) == 20                  # meteen in het archief


async def test_dubbele_naam_of_ip_geweigerd(hass, devices):
    await setup_two_entries(hass)
    r = await flow_add(hass, name="cafe", host="10.0.0.9")
    assert r["type"] is FlowResultType.FORM and r["errors"] == {"name": "name_exists"}
    r = await flow_add(hass, name="Nieuw", host=" 10.0.0.1 ")
    assert r["errors"] == {"host": "host_exists"}
    r = await flow_add(hass, name="!!!", host="10.0.0.9")
    assert r["errors"] == {"name": "invalid_name"}


# ---------------------------------------------------------------- scenario's uit de derde review

async def test_deur_verwijderen_en_opnieuw_toevoegen_zelfde_naam(hass, devices, events, hass_storage):
    from custom_components.btechnics_vto.const import STORAGE_KEY
    cafe, kam = devices
    fill(cafe, 40)
    e1, e2 = await setup_two_entries(hass)
    assert await hass.config_entries.async_remove(e1.entry_id)
    await hass.async_block_till_done()
    data = hass_storage[STORAGE_KEY]["data"]
    assert "cafe" not in data["log_state"] and "cafe" not in data["protected"]
    # vervangtoestel onder dezelfde naam, met een eigen (nieuwere) historiek en eigen codes
    new = FakeDevice("10.0.0.7")
    fake_vto.DEVICES["10.0.0.7"] = new
    for i in range(60):
        new.add_log(T0 + 10_000 + i, name=f"Nieuw{i}")
    new.add_code_rec("Bestaand", "424242")
    r = await flow_add(hass, name="Cafe", host="10.0.0.7")
    with patch("custom_components.btechnics_vto.VTOClient", FakeClient), \
         patch("custom_components.btechnics_vto.config_flow.VTOClient", FakeClient):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"add_another": False})
        await hass.async_block_till_done()
    assert events == []                                             # geen vloed van valse meldingen
    assert len(rows(hass, "cafe")) == 60                            # volledige backfill van het nieuwe toestel
    assert hass_storage[STORAGE_KEY]["data"]["protected"]["cafe"] == [1]   # nieuwe codes meteen beschermd
    assert len([r for r in rows(hass) if r[0].startswith("cafe~verwijderd~")]) == 40   # oude historiek bewaard


async def test_archief_kapot_deuren_werken_toch(hass, devices, events):
    cafe, _ = devices
    with patch("custom_components.btechnics_vto.AccessArchive", side_effect=OSError("disk I/O error")):
        await setup_two_entries(hass)
    assert ARCHIVE_KEY not in hass.data
    cafe.add_log(T0, name="Werkt")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["Werkt"]


async def test_momentopname_pagina_zonder_dubbels(hass, devices, hass_ws_client):
    cafe, _ = devices
    fill(cafe, 120)
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    p1 = await hist(c, limit=50)
    for i in range(30):                                             # nieuwe toegangen tijdens het bladeren
        cafe.add_log(T0 + i, name=f"Tussendoor{i}")
    await poll(hass, "cafe")
    p2 = await hist(c, limit=50, offset=50, max_id=p1["max_id"])
    p3 = await hist(c, limit=50, offset=100, max_id=p1["max_id"])
    ids = [x["id"] for x in p1["rows"] + p2["rows"] + p3["rows"]]
    assert len(ids) == 120 == len(set(ids)) and p3["total"] == 120
    assert (await hist(c, limit=0))["total"] == 150


async def test_geen_schrijfactie_zonder_nieuwe_toegangen(hass, devices):
    import sqlite3
    cafe, _ = devices
    fill(cafe, 50)
    await setup_two_entries(hass)
    await poll(hass, "cafe")
    other = sqlite3.connect(archive(hass)._path)
    before = other.execute("PRAGMA data_version").fetchone()[0]
    for _ in range(5):
        await poll(hass, "cafe")
    assert other.execute("PRAGMA data_version").fetchone()[0] == before
    cafe.add_log(T0, name="Nieuw")
    await poll(hass, "cafe")
    assert other.execute("PRAGMA data_version").fetchone()[0] != before
    other.close()


async def test_zoeken_en_persoon_unicode(hass, devices, hass_ws_client):
    cafe, _ = devices
    cafe.add_log(T0, name="Élise")
    cafe.add_log(T0 + 1, name="ÉLISE")
    cafe.add_log(T0 + 2, name="Gwijde Blöte")
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    assert (await hist(c, person="élise"))["total"] == 2
    assert (await hist(c, search="élis"))["total"] == 2
    assert (await hist(c, search="BLÖTE"))["total"] == 1
    assert len([p for p in (await hist(c))["people"] if p["name"].casefold() == "élise"]) == 1


async def test_historiek_enkel_voor_beheerders(hass, devices, hass_ws_client, hass_read_only_access_token):
    await setup_two_entries(hass)
    ro = await hass_ws_client(hass, hass_read_only_access_token)
    await ro.send_json_auto_id({"type": f"{DOMAIN}/history"})
    msg = await ro.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------- hoofdtoestel met kopieen van een onderstation

def test_eigen_toestelnummer_bepalen():
    from custom_components.btechnics_vto.records import own_numbers
    # Cafe (8001) bewaart kopieen van Kammerstraat (8002), ook als die drukker is
    assert own_numbers({"cafe": {"8001": 10, "8002": 900}, "kam": {"8002": 900}}) == {"cafe": "8001", "kam": "8002"}
    # onderstation dat (nog) niet in Home Assistant zit: meest voorkomende nummer is het eigen
    assert own_numbers({"cafe": {"8001": 50, "8003": 20}}) == {"cafe": "8001"}
    assert own_numbers({}) == {}
    assert own_numbers({"cafe": {"8001": 2, "8002": 2}}) == {}              # gelijkstand: niets verbergen


async def master_sub(cafe, kam):
    """Zoals op Trefpunt: Cafe (8001) registreert ook elke toegang van Kammerstraat (8002)."""
    cafe.add_log(T0 - 300, name="Eliot", vto="8001")
    kam.add_log(T0 - 200, name="Aardig", vto="8002")
    cafe.add_log(T0 - 200, name="Aardig", vto="8002")
    cafe.add_log(T0 - 100, name="Jan", vto="8001")
    kam.add_log(T0 - 50, name="Refu", vto="8002")
    cafe.add_log(T0 - 50, name="Refu", vto="8002")   # recentste record op Cafe is een kopie


async def test_kopieen_van_onderstation_niet_dubbel(hass, devices, hass_ws_client, events):
    cafe, kam = devices
    await master_sub(cafe, kam)
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    r = await hist(c)
    assert r["total"] == 4                                                   # 2 Cafe + 2 Kammerstraat, geen kopieen
    assert [p["doors"] for p in r["people"] if p["name"] == "Aardig"] == [["Kammerstraat"]]
    assert (await hist(c, door_ids=["cafe"]))["total"] == 2
    await poll(hass, "cafe")   # Cafe laadde eerst en kende het nummer van Kammerstraat toen nog niet
    assert coord(hass, "cafe").last_unlock["name"] == "Jan"                  # niet de kopie van Refu
    assert [x["name"] for x in coord(hass, "cafe").recent] == ["Jan", "Eliot"]
    await c.send_json_auto_id({"type": f"{DOMAIN}/doors"})
    arch = (await c.receive_json())["result"]["archive"]
    assert arch["cafe"]["count"] == 2 and arch["kammerstraat"]["count"] == 2
    # nieuwe toegang aan Kammerstraat: een melding, niet twee
    kam.add_log(T0 + 10, name="Spencer", vto="8002")
    cafe.add_log(T0 + 10, name="Spencer", vto="8002")
    await poll(hass, "cafe")
    await poll(hass, "kammerstraat")
    assert [(e["door"], e["name"]) for e in events] == [("Kammerstraat", "Spencer")]
    log = (await hass.services.async_call(DOMAIN, "list_log", {}, blocking=True, return_response=True))["log"]
    assert [(e["deur"], e["naam"]) for e in log].count(("Cafe", "Spencer")) == 0
    raw = (await hass.services.async_call(DOMAIN, "list_log", {"raw": True}, blocking=True, return_response=True))["log"]
    assert ("Cafe", "Spencer") in [(e["deur"], e["naam"]) for e in raw]


async def test_bestaand_archief_wordt_opgeschoond(hass, devices, hass_ws_client):
    """Archief van v0.3.0/0.3.1 (zonder toestelnummer) met kopieen, ook van records die al uit de
    buffer van Cafe verdwenen zijn: na de upgrade verborgen, eigen oude rijen blijven zichtbaar."""
    import sqlite3
    from custom_components.btechnics_vto.const import ARCHIVE_FILE
    cafe, kam = devices
    path = hass.config.path(ARCHIVE_FILE)
    c0 = sqlite3.connect(path)
    c0.executescript("""
        CREATE TABLE access (id INTEGER PRIMARY KEY AUTOINCREMENT, door_id TEXT NOT NULL, door TEXT NOT NULL,
            ts INTEGER NOT NULL, name TEXT NOT NULL, card TEXT NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE sync_state (door_id TEXT PRIMARY KEY, buf_len INTEGER NOT NULL);
    """)
    old = [  # (door_id, door, ts, name): de eerste twee staan niet meer in de buffer van Cafe
        ("cafe", "Cafe", T0 - 900, "OudEigen"), ("cafe", "Cafe", T0 - 800, "OudKopie"),
        ("kammerstraat", "Kammerstraat", T0 - 800, "OudKopie"),
    ]
    c0.executemany("INSERT INTO access (door_id, door, ts, name, card, method, status) VALUES (?, ?, ?, ?, '', '0', '1')", old)
    kam.add_log(T0 - 800, name="OudKopie", vto="8002")
    await master_sub(cafe, kam)
    buf = {"cafe": [], "kammerstraat": []}
    for did, dev in (("cafe", cafe), ("kammerstraat", kam)):
        for r in dev.log:
            buf[did].append((did, "Cafe" if did == "cafe" else "Kammerstraat", r["CreateTime"], r["UserID"]))
    c0.executemany("INSERT INTO access (door_id, door, ts, name, card, method, status) VALUES (?, ?, ?, ?, '', '0', '1')",
                   buf["cafe"] + [b for b in buf["kammerstraat"] if b[3] != "OudKopie"])
    c0.executemany("INSERT INTO sync_state VALUES (?, ?)", [("cafe", len(cafe.log)), ("kammerstraat", len(kam.log))])
    c0.commit()
    c0.close()
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    r = await hist(c)
    got = sorted((x["door"], x["name"]) for x in r["rows"])
    assert got == sorted([("Cafe", "OudEigen"), ("Cafe", "Eliot"), ("Cafe", "Jan"),
                          ("Kammerstraat", "OudKopie"), ("Kammerstraat", "Aardig"), ("Kammerstraat", "Refu")])
    assert len(rows(hass)) == 9                                             # niets gewist, enkel verborgen


# ---------------------------------------------------------------- klok van het toestel

async def test_klok_toestel_uur_achter_wordt_omgerekend(hass, devices, events, hass_ws_client):
    """Zoals op Trefpunt: toestel op UTC+1 zonder zomertijd; CreateTime is de kloktijd van het toestel."""
    cafe, _ = devices
    cafe.wall_offset = 3600
    cafe.add_log(cafe.wall(T0), name="Merel")                  # echte toegang om 14:00 Brussel
    await setup_two_entries(hass)
    assert coord(hass, "cafe").last_unlock["time"] == "2026-09-25T14:00:00+02:00"
    c = await hass_ws_client(hass)
    assert (await hist(c))["rows"][0]["ts"] == T0
    cafe.add_log(cafe.wall(T0 + 60), name="Jan")
    await poll(hass, "cafe")
    assert [(e["name"], e["time"]) for e in events] == [("Jan", "2026-09-25T14:01:00+02:00")]


async def test_bestaand_archief_krijgt_juiste_tijd(hass, devices, hass_ws_client):
    import sqlite3
    from custom_components.btechnics_vto.const import ARCHIVE_FILE
    cafe, _ = devices
    cafe.wall_offset = 3600
    cafe.add_log(cafe.wall(T0), name="Merel")
    c0 = sqlite3.connect(hass.config.path(ARCHIVE_FILE))
    c0.executescript("""
        CREATE TABLE access (id INTEGER PRIMARY KEY AUTOINCREMENT, door_id TEXT NOT NULL, door TEXT NOT NULL,
            ts INTEGER NOT NULL, name TEXT NOT NULL, card TEXT NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL,
            vto TEXT NOT NULL DEFAULT '');
        CREATE TABLE sync_state (door_id TEXT PRIMARY KEY, buf_len INTEGER NOT NULL);
    """)
    c0.execute("INSERT INTO access (door_id, door, ts, name, card, method, status) VALUES ('cafe', 'Cafe', ?, 'Oud', '', '0', '1')",
               (cafe.wall(T0 - 86400 * 30),))
    c0.execute("INSERT INTO access (door_id, door, ts, name, card, method, status) VALUES ('cafe', 'Cafe', ?, 'Merel', '', '0', '1')",
               (cafe.wall(T0),))
    c0.execute("INSERT INTO sync_state VALUES ('cafe', 1)")
    c0.commit()
    c0.close()
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    assert [(x["name"], x["ts"]) for x in (await hist(c))["rows"]] == [("Merel", T0), ("Oud", T0 - 86400 * 30)]


async def test_klok_juist_zetten_op_afstand(hass, devices, events, hass_ws_client):
    cafe, _ = devices
    cafe.wall_offset = 3600
    cafe.add_log(cafe.wall(T0 - 600), name="Voor")
    await setup_two_entries(hass)
    res = await hass.services.async_call(DOMAIN, "sync_clock", {"doors": ["Cafe"]}, blocking=True, return_response=True)
    r = res["toestellen"][0]
    assert r["deur"] == "Cafe" and r["na"]["locales"]["DSTEnable"] is True
    assert r["na"]["ntp"]["Enable"] is True and r["na"]["ntp"]["Address"] == "be.pool.ntp.org"
    assert r["na"]["ntp"]["TimeZone"] == 1                                   # tijdzone zelf ongewijzigd
    assert r["na"]["locales"]["DSTStart"]["Month"] == 3 and r["na"]["locales"]["DSTEnd"]["Month"] == 10
    assert ("set_config", "Locales") in cafe.calls and ("set_config", "NTP") in cafe.calls
    cafe.add_log(cafe.wall(T0), name="Na")                     # klok staat nu op zomertijd
    await poll(hass, "cafe")
    assert [(e["name"], e["time"]) for e in events] == [("Na", "2026-09-25T14:00:00+02:00")]
    rec = coord(hass, "cafe").recent
    assert [(x["name"], x["time"]) for x in rec] == [("Na", "2026-09-25T14:00:00+02:00"), ("Voor", "2026-09-25T13:50:00+02:00")]
    c = await hass_ws_client(hass)
    assert [(x["name"], x["ts"]) for x in (await hist(c))["rows"]] == [("Na", T0), ("Voor", T0 - 600)]


async def test_klok_zetten_enkel_voor_beheerders(hass, devices, hass_read_only_user):
    import pytest
    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized
    await setup_two_entries(hass)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "sync_clock", {"doors": ["Cafe"]}, blocking=True,
                                       context=Context(user_id=hass_read_only_user.id))
    assert not any(c[0] == "set_config" for c in devices[0].calls)


async def test_logboek_na_klokwijziging_blijft_juist(hass, devices):
    """Records van voor de klokwijziging staan nog in de buffer: list_log toont ze met hun echte tijd."""
    cafe, _ = devices
    cafe.wall_offset = 3600
    cafe.add_log(cafe.wall(T0 - 600), name="Voor")
    await setup_two_entries(hass)
    await hass.services.async_call(DOMAIN, "sync_clock", {"doors": ["Cafe"]}, blocking=True, return_response=True)
    cafe.add_log(cafe.wall(T0), name="Na")
    await poll(hass, "cafe")
    log = (await hass.services.async_call(DOMAIN, "list_log", {}, blocking=True, return_response=True))["log"]
    got = [(e["naam"], e["tijd"]) for e in log if e["deur"] == "Cafe"]
    assert got == [("Na", "2026-09-25T14:00:00+02:00"), ("Voor", "2026-09-25T13:50:00+02:00")]


async def test_methodes_binnenpost_en_klavier(hass, devices, hass_ws_client):
    """Methode 4 met het nummer van de binnenpost; de ingetypte cijfers van methode 20 nergens zichtbaar."""
    import sqlite3
    cafe, _ = devices
    cafe.add_log(T0 - 60, name="", method=4)
    cafe.log[-1]["RoomNumber"] = "9901"
    cafe.add_log(T0, name="", method=20, status=0)
    cafe.log[-1]["RoomNumber"] = "900123"
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    rows_ = (await hist(c))["rows"]
    assert [x["method"] for x in rows_] == ["ongeldige invoer klavier", "binnenpost 9901"]
    assert [x["method"] for x in coord(hass, "cafe").recent] == ["ongeldige invoer klavier", "binnenpost 9901"]
    con = sqlite3.connect(archive(hass)._path)
    dump = "\n".join(con.iterdump())
    con.close()
    assert "900123" not in dump and "9901" in dump


async def test_binnenpost_nummer_aangevuld_in_bestaand_archief(hass, devices, hass_ws_client):
    import sqlite3
    from custom_components.btechnics_vto.const import ARCHIVE_FILE
    cafe, _ = devices
    cafe.add_log(T0, name="", method=4)
    cafe.log[-1]["RoomNumber"] = "9902"
    c0 = sqlite3.connect(hass.config.path(ARCHIVE_FILE))
    c0.executescript("""
        CREATE TABLE access (id INTEGER PRIMARY KEY AUTOINCREMENT, door_id TEXT NOT NULL, door TEXT NOT NULL,
            ts INTEGER NOT NULL, name TEXT NOT NULL, card TEXT NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL,
            vto TEXT NOT NULL DEFAULT '', t INTEGER);
        CREATE TABLE sync_state (door_id TEXT PRIMARY KEY, buf_len INTEGER NOT NULL);
    """)
    c0.execute("INSERT INTO access (door_id, door, ts, name, card, method, status) VALUES ('cafe', 'Cafe', ?, '', '', '4', '1')", (T0,))
    c0.execute("INSERT INTO sync_state VALUES ('cafe', 1)")
    c0.commit()
    c0.close()
    await setup_two_entries(hass)
    c = await hass_ws_client(hass)
    assert [x["method"] for x in (await hist(c))["rows"]] == ["binnenpost 9902"]
