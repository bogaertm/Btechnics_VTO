"""Beheer van alle codes en badges: opnemen, blokkeren, deblokkeren, uit dienst, herstellen."""
from datetime import timedelta

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.util import dt as dt_util

from custom_components.btechnics_vto.const import DOMAIN
from custom_components.btechnics_vto.manage import MANAGER_KEY

from .test_integration import call, coord, devices, setup_two_entries  # noqa: F401


def reg(hass):
    return coord(hass, "cafe").registry


def entry(hass, name, kind="code"):
    hits = [(cid, m) for cid, m in reg(hass).managed.items() if m["name"] == name and m["kind"] == kind]
    assert len(hits) == 1, hits
    return hits[0]


def on(dev, name, code=None):
    return [r for r in dev.codes if r["UserID"] == name and (code is None or r["CommonPassword"] == code)]


async def test_bestaande_codes_en_badges_worden_opgenomen(hass, devices):
    cafe, kam = devices
    cafe.add_code_rec("Refu", "112233")
    kam.add_code_rec("Refu", "112233")
    cafe.add_card_rec("Roijin", "AB12CD34", doors=(0, 1))
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, m = entry(hass, "Refu")
    assert set(m["doors"]) == {"cafe", "kammerstraat"} and m["status"] == "active" and m["source"] == "toestel"
    _, b = entry(hass, "Roijin", "badge")
    assert b["card"] == "AB12CD34" and list(b["doors"]) == ["cafe"]
    assert entry(hass, "Adriaan")[1]["doors"] == {"cafe": 1}


async def test_blokkeren_en_deblokkeren_over_alle_deuren(hass, devices):
    cafe, kam = devices
    cafe.add_code_rec("Refu", "112233")
    kam.add_code_rec("Refu", "112233")
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Refu")
    await call(hass, "block", {"id": cid})
    assert on(cafe, "Refu") == [] and on(kam, "Refu") == []            # werkt nergens meer
    m = reg(hass).managed[cid]
    assert m["status"] == "blocked" and m["doors"] == {} and set(m["stored"]) == {"cafe", "kammerstraat"}
    assert m["until"] is None
    await coord(hass, "cafe").async_refresh_codes()                     # gelijkzetten mag niets terugzetten
    assert reg(hass).managed[cid]["status"] == "blocked"
    await call(hass, "unblock", {"id": cid})
    assert len(on(cafe, "Refu", "112233")) == 1 and len(on(kam, "Refu", "112233")) == 1
    m = reg(hass).managed[cid]
    assert m["status"] == "active" and set(m["doors"]) == {"cafe", "kammerstraat"} and m["stored"] == {}
    actions = [a["action"] for a in reg(hass).audit]
    assert actions[-2:] == ["geblokkeerd", "gedeblokkeerd"]


async def test_blokkeren_tot_tijdstip_wordt_automatisch_opgeheven(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    until = dt_util.now() + timedelta(hours=2)
    await call(hass, "block", {"id": cid, "until": until.replace(tzinfo=None)})   # lokale tijd
    m = reg(hass).managed[cid]
    assert m["status"] == "blocked" and abs(dt_util.parse_datetime(m["until"]) - until) < timedelta(seconds=1)
    assert "tot " in reg(hass).audit[-1]["detail"]
    mgr = hass.data[MANAGER_KEY]
    await mgr._tick()
    assert reg(hass).managed[cid]["status"] == "blocked"                   # nog niet
    m["until"] = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    await mgr._tick()
    assert reg(hass).managed[cid]["status"] == "active" and len(on(cafe, "Adriaan", "936100")) == 1
    assert reg(hass).audit[-1]["action"] == "automatisch gedeblokkeerd" and reg(hass).audit[-1]["user"] == "planner"
    with pytest.raises(HomeAssistantError, match="verleden"):
        await call(hass, "block", {"id": cid, "until": dt_util.now().replace(tzinfo=None) - timedelta(minutes=5)})


async def test_uit_dienst_herstellen_en_definitief(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    with pytest.raises(HomeAssistantError, match="enkel wat uit dienst"):
        await call(hass, "forget", {"id": cid})
    await call(hass, "retire", {"id": cid})
    assert on(cafe, "Adriaan") == [] and reg(hass).managed[cid]["status"] == "retired"
    await call(hass, "restore", {"id": cid})
    assert len(on(cafe, "Adriaan", "936100")) == 1 and reg(hass).managed[cid]["status"] == "active"
    await call(hass, "retire", {"id": cid})
    await call(hass, "forget", {"id": cid})
    assert cid not in reg(hass).managed and on(cafe, "Adriaan") == []
    assert [a["action"] for a in reg(hass).audit][-4:] == ["uit dienst", "hersteld", "uit dienst", "definitief verwijderd"]


async def test_badge_blokkeren_zet_exact_hetzelfde_record_terug(hass, devices):
    cafe, _ = devices
    cafe.add_card_rec("Roijin", "AB12CD34", doors=(0, 1))
    before = {k: v for k, v in cafe.cards[0].items() if k != "RecNo"}
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Roijin", "badge")
    await call(hass, "block", {"id": cid})
    assert cafe.cards == []
    await call(hass, "unblock", {"id": cid})
    assert len(cafe.cards) == 1 and {k: v for k, v in cafe.cards[0].items() if k != "RecNo"} == before
    await call(hass, "rename_badge", {"id": cid, "name": "Roijin B"})
    assert cafe.cards[0]["CardName"] == "Roijin B" and cafe.cards[0]["UserName"] == "Roijin B"
    assert cafe.cards[0]["Doors"] == [0, 1]
    assert reg(hass).managed[cid]["name"] == "Roijin B"


async def test_veiligheidscontrole_bij_manueel_gewijzigd_record(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    notes = []

    async def _note(c):
        notes.append(c.data["message"])

    hass.services.async_register("persistent_notification", "create", _note)
    cafe.codes[0]["UserID"] = "Iemand anders"      # op het toestel gewijzigd, register weet het nog niet
    with pytest.raises(HomeAssistantError, match="veiligheidscontrole"):
        await call(hass, "block", {"id": cid})
    await hass.async_block_till_done()
    assert len(cafe.codes) == 1 and cafe.codes[0]["UserID"] == "Iemand anders"   # niets verwijderd
    assert notes


async def test_deblokkeren_als_code_intussen_bij_iemand_anders_staat(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    await call(hass, "block", {"id": cid})
    cafe.add_code_rec("Bert", "936100")
    with pytest.raises(HomeAssistantError, match="Bert"):
        await call(hass, "unblock", {"id": cid})
    m = reg(hass).managed[cid]
    assert m["status"] == "blocked" and "cafe" in m["stored"] and on(cafe, "Adriaan") == []
    # naam of code van een geblokkeerde code aanpassen, dan deblokkeren
    await call(hass, "update_code", {"id": cid, "code": "936101"})
    await call(hass, "unblock", {"id": cid})
    assert len(on(cafe, "Adriaan", "936101")) == 1 and len(on(cafe, "Bert", "936100")) == 1


async def test_timeout_na_verwijderen_telt_als_geblokkeerd(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    cafe.raise_after_remove = OSError("timeout")
    await call(hass, "block", {"id": cid})
    m = reg(hass).managed[cid]
    assert m["status"] == "blocked" and m["stored"]["cafe"]["CommonPassword"] == "936100" and on(cafe, "Adriaan") == []


async def test_handmatig_verwijderd_verdwijnt_uit_de_lijst(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    cafe.codes.clear()
    await coord(hass, "cafe").async_refresh_codes()
    assert cid not in reg(hass).managed


async def test_bestaande_code_aanpassen_en_verwijderen(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Aardig")
    await call(hass, "update_code", {"id": cid, "name": "Aardig Jan", "doors": ["Cafe", "Kammerstraat"]})
    assert len(on(cafe, "Aardig Jan", "900124")) == 1 and len(on(kam, "Aardig Jan", "900124")) == 1
    audit = reg(hass).audit[-1]
    assert audit["action"] == "aangepast" and "naam Aardig naar Aardig Jan" in audit["detail"]
    await call(hass, "remove_code", {"id": cid})
    assert on(cafe, "Aardig Jan") == [] and on(kam, "Aardig Jan") == []


async def test_beheer_enkel_voor_beheerders(hass, devices, hass_read_only_user):
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    ctx = Context(user_id=hass_read_only_user.id)
    for svc, data in (("block", {"id": cid}), ("unblock", {"id": cid}), ("retire", {"id": cid}),
                      ("restore", {"id": cid}), ("forget", {"id": cid}), ("rename_badge", {"id": cid, "name": "x"})):
        with pytest.raises(Unauthorized):
            await hass.services.async_call(DOMAIN, svc, data, blocking=True, context=ctx)
    assert reg(hass).managed[cid]["status"] == "active"


async def test_websocket_beheer(hass, devices, hass_ws_client, hass_read_only_access_token):
    cafe, _ = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    c = await hass_ws_client(hass)

    async def ws(**kw):
        await c.send_json_auto_id(kw)
        return await c.receive_json()

    r = (await ws(type=f"{DOMAIN}/manage/list"))["result"]
    assert [d["name"] for d in r["doors"]] == ["Cafe", "Kammerstraat"]
    adr = next(e for e in r["entries"] if e["name"] == "Adriaan")
    assert adr["status"] == "active" and adr["doors"] == [{"id": "cafe", "name": "Cafe"}] and adr["secret"] == "936100"
    until = (dt_util.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    res = await ws(type=f"{DOMAIN}/manage/action", action="block", entry=adr["id"], until=until)
    assert res["success"], res
    r = (await ws(type=f"{DOMAIN}/manage/list"))["result"]
    adr = next(e for e in r["entries"] if e["name"] == "Adriaan")
    assert adr["status"] == "blocked" and adr["stored"] == [{"id": "cafe", "name": "Cafe"}] and adr["until"]
    assert r["audit"][0]["action"] == "geblokkeerd" and r["audit"][0]["user"]
    res = await ws(type=f"{DOMAIN}/manage/action", action="add", name="Nieuw", code="12", doors=["Cafe"])
    assert not res["success"]                                             # code te kort: foutmelding, niets geschreven
    res = await ws(type=f"{DOMAIN}/manage/action", action="add", name="Nieuw", code="445566", doors=["cafe"])
    assert res["success"] and len(on(cafe, "Nieuw", "445566")) == 1
    ro = await hass_ws_client(hass, hass_read_only_access_token)
    await ro.send_json_auto_id({"type": f"{DOMAIN}/manage/list"})
    assert not (await ro.receive_json())["success"]


async def test_verbinding_weg_na_verwijderen_verliest_niets(hass, devices):
    """Onzeker of het verwijderen lukte en toestel onbereikbaar: de kopie blijft bewaard."""
    cafe, _ = devices
    cafe.codes[0]["VTONumber"] = "8001"
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    cafe.offline_after_remove = True
    with pytest.raises(HomeAssistantError):
        await call(hass, "block", {"id": cid})
    m = reg(hass).managed[cid]
    assert m["status"] == "blocked" and m["stored"]["cafe"]["CommonPassword"] == "936100"
    cafe.offline = False
    await coord(hass, "cafe").async_refresh_codes()          # record is weg: verwijzing valt, kopie blijft
    m = reg(hass).managed[cid]
    assert m["doors"] == {} and m["status"] == "blocked" and "cafe" in m["stored"]
    await call(hass, "unblock", {"id": cid})
    rec = on(cafe, "Adriaan", "936100")
    assert len(rec) == 1 and rec[0]["VTONumber"] == "8001"    # exact teruggezet, ook VTONumber


async def test_opgeslagen_zonder_status_verdwijnt_niet(hass, devices):
    """Crash tussen bewaren en status zetten: de ingang met kopie mag niet wegvallen."""
    cafe, _ = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, m = entry(hass, "Adriaan")
    m["stored"]["cafe"] = {"UserID": "Adriaan", "CommonPassword": "936100"}
    cafe.codes = [r for r in cafe.codes if r["UserID"] != "Adriaan"]
    await coord(hass, "cafe").async_refresh_codes()
    assert cid in reg(hass).managed and "cafe" in reg(hass).managed[cid]["stored"]


async def test_deur_verwijderen_bewaart_geblokkeerde_records(hass, devices):
    cafe, _ = devices
    e1, _ = await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    await call(hass, "block", {"id": cid})
    await hass.config_entries.async_remove(e1.entry_id)
    await hass.async_block_till_done()
    from custom_components.btechnics_vto import REG_KEY
    m = hass.data[REG_KEY].managed[cid]                       # gedeeld register blijft bestaan
    assert "cafe" in m["stored"] and m["status"] == "blocked"


async def test_code_van_geblokkeerde_persoon_niet_opnieuw_uitdelen(hass, devices):
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    await call(hass, "block", {"id": cid})
    with pytest.raises(HomeAssistantError, match="bewaard bij Adriaan"):
        await call(hass, "add_code", {"name": "Nieuw", "code": "936100", "doors": ["Cafe"]})


async def test_list_codes_met_badges_en_geblokkeerde(hass, devices):
    cafe, _ = devices
    cafe.add_card_rec("Roijin", "AB12CD34")
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    await call(hass, "block", {"id": cid})
    res = await call(hass, "list_codes", {"raw": True}, True)
    beheer = {e["name"] + "/" + e["kind"]: e for e in res["beheer"]}
    assert beheer["Roijin/badge"]["status"] == "active" and beheer["Adriaan/code"]["status"] == "blocked"
    assert beheer["Adriaan/code"]["stored"] == ["cafe"]


def badges(dev, card):
    return [r for r in dev.cards if r["CardNo"] == card]


async def test_nieuwe_badge_op_twee_deuren(hass, devices):
    cafe, kam = devices
    cafe.add_card_rec("Roijin", "AB12CD34")
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    res = await call(hass, "add_badge", {"name": "Nieuw", "card": "1a2b3c4d", "doors": ["Cafe", "kammerstraat"]}, True)
    assert set(res["doors"]) == {"cafe", "kammerstraat"}
    rec = badges(cafe, "1A2B3C4D")[0]
    assert rec["CardName"] == "Nieuw" and rec["UserName"] == "Nieuw" and rec["Doors"] == [0] and rec["UserID"] == "9999"
    assert rec["PersonId"] == "2" and rec["ValidDateEnd"] == "0000-00-00 00:00:00"   # zelfde velden als de bestaande badges
    assert len(badges(kam, "1A2B3C4D")) == 1
    cid, m = entry(hass, "Nieuw", "badge")
    assert m["status"] == "active" and set(m["doors"]) == {"cafe", "kammerstraat"} and m["source"] == "home assistant"
    assert reg(hass).audit[-1]["action"] == "toegevoegd"
    # dubbel nummer wordt geweigerd, ook met andere naam en kleine letters
    with pytest.raises(HomeAssistantError, match="al gekend bij Nieuw"):
        await call(hass, "add_badge", {"name": "Ander", "card": "1a2b3c4d", "doors": ["Cafe"]})
    import voluptuous as vol
    with pytest.raises(vol.Invalid):
        await call(hass, "add_badge", {"name": "X", "card": "ZZ", "doors": ["Cafe"]})
    # daarna gewoon beheerbaar: blokkeren en terug
    await call(hass, "block", {"id": cid})
    assert badges(cafe, "1A2B3C4D") == [] and badges(kam, "1A2B3C4D") == []
    await call(hass, "unblock", {"id": cid})
    assert badges(cafe, "1A2B3C4D")[0]["PersonId"] == "2" and len(badges(kam, "1A2B3C4D")) == 1


async def test_nieuwe_badge_alles_of_niets(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    kam.fail_add = True
    with pytest.raises(HomeAssistantError, match="niet toegevoegd"):
        await call(hass, "add_badge", {"name": "Nieuw", "card": "1A2B3C4D", "doors": ["Cafe", "Kammerstraat"]})
    assert badges(cafe, "1A2B3C4D") == [] and badges(kam, "1A2B3C4D") == []       # teruggedraaid op Cafe
    assert not [m for m in reg(hass).managed.values() if m["name"] == "Nieuw"]


async def test_onbekende_badges_uit_logboek(hass, devices, hass_ws_client):
    from custom_components.btechnics_vto.websocket import ARCHIVE_KEY
    cafe, _ = devices
    cafe.add_card_rec("Roijin", "AB12CD34")
    now = int(dt_util.utcnow().timestamp())
    cafe.add_log(now - 600, name="", method=1, status=0, card="ffee0011")     # onbekende badge geweigerd
    cafe.add_log(now - 300, name="Roijin", method=1, status=1, card="AB12CD34")
    cafe.add_log(now - 200, name="", method=1, status=0, card="AB12CD34")      # gekende badge geweigerd: niet tonen
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    assert hass.data.get(ARCHIVE_KEY) is not None
    c = await hass_ws_client(hass)
    await c.send_json_auto_id({"type": f"{DOMAIN}/manage/list"})
    r = (await c.receive_json())["result"]
    assert [u["card"] for u in r["unknown_cards"]] == ["FFEE0011"] and r["unknown_cards"][0]["doors"] == ["Cafe"]
    await c.send_json_auto_id({"type": f"{DOMAIN}/manage/action", "action": "add_badge", "name": "Nieuw", "card": "FFEE0011", "doors": ["cafe"]})
    assert (await c.receive_json())["success"]
    await c.send_json_auto_id({"type": f"{DOMAIN}/manage/list"})
    r = (await c.receive_json())["result"]
    assert r["unknown_cards"] == [] and any(e["name"] == "Nieuw" and e["kind"] == "badge" for e in r["entries"])


async def test_geen_geheimen_voor_gewone_gebruikers(hass, devices, hass_ws_client, hass_read_only_access_token):
    """Attributen en het deurenoverzicht zijn voor iedereen: nooit codes of badgenummers."""
    from datetime import datetime, timezone
    cafe, _ = devices
    cafe.add_card_rec("Roijin", "AB12CD34")
    cafe.add_log(int(datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc).timestamp()), name="Roijin", method=1, card="AB12CD34")
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    for eid in ("sensor.vto_cafe_codes", "sensor.vto_cafe_badges", "sensor.vto_cafe_laatste_unlock"):
        st = hass.states.get(eid)
        if st is not None:
            txt = str(st.attributes)
            assert "936100" not in txt and "AB12CD34" not in txt, eid
    ro = await hass_ws_client(hass, hass_read_only_access_token)
    await ro.send_json_auto_id({"type": f"{DOMAIN}/doors"})
    r = await ro.receive_json()
    assert r["success"] and "AB12CD34" not in str(r["result"]) and "936100" not in str(r["result"])
    assert r["result"]["doors"][0]["last_unlock"]["name"] == "Roijin"


async def test_leesfout_toestel_geen_lege_tabel(hass, devices):
    """Een halve uitlezing mag nooit doorgaan voor een lege tabel (anders verdwijnen ingangen)."""
    from custom_components.btechnics_vto.api import VTOClient, VTOError
    c = VTOClient.__new__(VTOClient)
    answers = {"RecordFinder.factory.create": {"result": 7}, "RecordFinder.startFind": {"result": True},
               "RecordFinder.doFind": {"result": False, "error": {"code": 268632080}}, "RecordFinder.destroy": {"result": True}}
    c.call = lambda m, p=None, o=None: answers[m]
    with pytest.raises(VTOError):
        c.find("AccessControlCommonPassword")


async def test_bewaarde_code_niet_naar_bestaande_code(hass, devices):
    cafe, _ = devices
    cafe.add_code_rec("Bert", "555555")
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    await call(hass, "block", {"id": cid})
    with pytest.raises(HomeAssistantError, match="al"):
        await call(hass, "update_code", {"id": cid, "code": "555555"})
    assert reg(hass).managed[cid]["code"] == "936100"


async def test_camera_probe_enkel_beheerders(hass, devices, hass_read_only_user, monkeypatch):
    from homeassistant.core import Context
    from custom_components.btechnics_vto import camera, dhip

    async def no_rtsp(hass, url, timeout=12):
        raise TimeoutError("geen netwerk in de test")
    monkeypatch.setattr(camera, "rtsp_frame", no_rtsp)
    monkeypatch.setattr(camera, "rtsp_describe", lambda *a: {"test": True})
    monkeypatch.setattr(dhip, "probe", lambda *a, **k: {"ok": False, "fout": "test"})
    await setup_two_entries(hass)
    r = await call(hass, "camera_probe", {}, True)
    assert [x["deur"] for x in r["toestellen"]] == ["Cafe", "Kammerstraat"] and r["toestellen"][0]["foto_kanaal_1"]["ok"]
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "camera_probe", {}, blocking=True, return_response=True,
                                       context=Context(user_id=hass_read_only_user.id))


# ---------------------------------------------------------------- geldigheid (v0.6.0)

async def test_code_met_begin_later_staat_pas_dan_op_het_toestel(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    start = dt_util.now() + timedelta(hours=3)
    end = start + timedelta(days=2)
    res = await call(hass, "add_code", {"name": "Gast", "code": "246810", "doors": ["Cafe", "Kammerstraat"],
                                        "valid_from": start.replace(tzinfo=None), "valid_until": end.replace(tzinfo=None)}, True)
    cid = res["id"]
    assert res["scheduled"] and on(cafe, "Gast") == [] and on(kam, "Gast") == []   # nog nergens actief
    m = reg(hass).managed[cid]
    assert m["status"] == "blocked" and m["until"] == m["valid_from"] and set(m["stored"]) == {"cafe", "kammerstraat"}
    await coord(hass, "cafe").async_refresh_codes()                                  # gelijkzetten laat de planning staan
    assert cid in reg(hass).managed
    mgr = hass.data[MANAGER_KEY]
    await mgr._tick()
    assert on(cafe, "Gast") == []
    m["until"] = m["valid_from"] = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    await mgr._tick()
    m = reg(hass).managed[cid]
    assert m["status"] == "active" and len(on(cafe, "Gast", "246810")) == 1 and len(on(kam, "Gast", "246810")) == 1
    assert m["valid_from"] is None and m["valid_until"] is not None
    assert reg(hass).audit[-1]["action"] == "begin geldigheid"
    # einde: automatisch uit dienst, bewaard en herstelbaar
    m["valid_until"] = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    await mgr._tick()
    m = reg(hass).managed[cid]
    assert m["status"] == "retired" and on(cafe, "Gast") == [] and set(m["stored"]) == {"cafe", "kammerstraat"}
    assert reg(hass).audit[-1]["user"] == "planner" and "einde geldigheid" in reg(hass).audit[-1]["detail"]
    await call(hass, "restore", {"id": cid})
    m = reg(hass).managed[cid]
    assert m["status"] == "active" and m["valid_until"] is None and len(on(cafe, "Gast")) == 1
    await mgr._tick()
    assert reg(hass).managed[cid]["status"] == "active"                              # niet meteen weer uit dienst


async def test_geldigheid_van_bestaande_code(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    cid, _ = entry(hass, "Adriaan")
    end = dt_util.now() + timedelta(days=1)
    await call(hass, "set_validity", {"id": cid, "valid_until": end.replace(tzinfo=None)}, True)
    m = reg(hass).managed[cid]
    assert m["status"] == "active" and m["valid_until"] and len(on(cafe, "Adriaan")) == 1
    assert reg(hass).audit[-1]["action"] == "geldigheid ingesteld" and "geldig tot" in reg(hass).audit[-1]["detail"]
    # begin later: van het toestel tot het begin
    start = dt_util.now() + timedelta(hours=2)
    await call(hass, "set_validity", {"id": cid, "valid_from": start.replace(tzinfo=None), "valid_until": end.replace(tzinfo=None)}, True)
    m = reg(hass).managed[cid]
    assert m["status"] == "blocked" and m["until"] == m["valid_from"] and on(cafe, "Adriaan") == []
    # begin wissen: meteen terug actief
    await call(hass, "set_validity", {"id": cid, "valid_until": end.replace(tzinfo=None)}, True)
    m = reg(hass).managed[cid]
    assert m["status"] == "active" and m["valid_from"] is None and len(on(cafe, "Adriaan")) == 1
    with pytest.raises(HomeAssistantError, match="verleden"):
        await call(hass, "set_validity", {"id": cid, "valid_until": (dt_util.now() - timedelta(minutes=1)).replace(tzinfo=None)}, True)
    with pytest.raises(HomeAssistantError, match="na het begin"):
        await call(hass, "set_validity", {"id": cid, "valid_from": end.replace(tzinfo=None), "valid_until": start.replace(tzinfo=None)}, True)


async def test_code_moet_6_tot_8_cijfers(hass, devices):
    import voluptuous as vol
    await setup_two_entries(hass)
    for bad in ("1234", "12345", "123456789", "12a456"):
        with pytest.raises(vol.Invalid):
            await call(hass, "add_code", {"name": "X", "code": bad, "doors": ["Cafe"]}, True)
    assert (await call(hass, "add_code", {"name": "X", "code": "12345678", "doors": ["Cafe"]}, True))["id"]


async def test_deur_openen_op_afstand(hass, devices, hass_read_only_user):
    cafe, kam = devices
    await setup_two_entries(hass)
    res = await call(hass, "open_door", {"door": "Kammerstraat"}, True)
    assert res == {"door": "Kammerstraat", "opened": True}
    assert getattr(kam, "opened", 0) == 1 and getattr(cafe, "opened", 0) == 0
    a = reg(hass).audit[-1]
    assert a["action"] == "deur geopend op afstand" and a["kind"] == "deur" and a["doors"] == ["Kammerstraat"]
    kam.refuse_open = True
    with pytest.raises(HomeAssistantError, match="geweigerd"):
        await call(hass, "open_door", {"door": "Kammerstraat"}, True)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "open_door", {"door": "Cafe"}, blocking=True,
                                       context=Context(user_id=hass_read_only_user.id), return_response=True)
    assert getattr(cafe, "opened", 0) == 0
