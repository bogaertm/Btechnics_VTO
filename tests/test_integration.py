"""Volledige tests van de Btechnics VTO integratie tegen een echte Home Assistant core."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.btechnics_vto.const import DOMAIN, EVENT_UNLOCK, STORAGE_KEY

from . import fake_vto
from .fake_vto import FakeClient, FakeDevice

T0 = int(datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc).timestamp())  # 14:00 Brussel


def door(did, name, host):
    return {"id": did, "name": name, "host": host, "https": False, "username": "admin", "password": "x", "info": {}}


@pytest.fixture
def devices():
    fake_vto.DEVICES.clear()
    cafe = FakeDevice("10.0.0.1")
    kam = FakeDevice("10.0.0.2")
    fake_vto.DEVICES.update({"10.0.0.1": cafe, "10.0.0.2": kam})
    cafe.add_code_rec("Adriaan", "936100")
    kam.add_code_rec("Aardig", "900124")
    return cafe, kam


@pytest.fixture
def events(hass):
    got = []

    @callback
    def _on(e):
        got.append(e.data)

    hass.bus.async_listen(EVENT_UNLOCK, _on)
    return got


@pytest.fixture
def logbook_calls(hass):
    calls = []

    async def _log(call):
        calls.append(dict(call.data))

    hass.services.async_register("logbook", "log", _log)
    return calls


async def setup_two_entries(hass: HomeAssistant):
    await hass.config.async_set_time_zone("Europe/Brussels")
    e1 = MockConfigEntry(domain=DOMAIN, title="Btechnics VTO", data={"doors": [door("cafe", "Cafe", "10.0.0.1")]})
    e2 = MockConfigEntry(domain=DOMAIN, title="Btechnics VTO", data={"doors": [door("kammerstraat", "Kammerstraat", "10.0.0.2")]})
    e1.add_to_hass(hass)
    e2.add_to_hass(hass)
    with patch("custom_components.btechnics_vto.VTOClient", FakeClient):
        # de component zet bij eerste setup automatisch alle entries van het domein op
        assert await hass.config_entries.async_setup(e1.entry_id)
        await hass.async_block_till_done()
    from homeassistant.config_entries import ConfigEntryState
    assert e1.state is ConfigEntryState.LOADED and e2.state is ConfigEntryState.LOADED
    return e1, e2


def coord(hass, did):
    for d in hass.data[DOMAIN].values():
        if did in d["coords"]:
            return d["coords"][did]
    raise KeyError(did)


async def poll(hass, did):
    await coord(hass, did).async_refresh()
    await hass.async_block_till_done()


async def restart(hass, entries):
    for e in entries:
        assert await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data
    with patch("custom_components.btechnics_vto.VTOClient", FakeClient):
        for e in entries:
            assert await hass.config_entries.async_setup(e.entry_id)
        await hass.async_block_till_done()


def fill(dev, n, start=T0 - 10_000_000, step=600):
    for i in range(n):
        dev.add_log(start + i * step, name=f"Oud{i}")


# ---------------------------------------------------------------- tijdzone

async def test_tijdzone_zomer_en_winter(hass, devices):
    cafe, _ = devices
    cafe.add_log(int(datetime(2026, 9, 25, 12, 43, 44, tzinfo=timezone.utc).timestamp()), name="Eliot")
    await setup_two_entries(hass)
    st = hass.states.get("sensor.vto_cafe_laatste_unlock")
    assert st.state == "Eliot"
    assert st.attributes["time"] == "2026-09-25T14:43:44+02:00"
    cafe.add_log(int(datetime(2026, 12, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()), name="Winter")
    await poll(hass, "cafe")
    st = hass.states.get("sensor.vto_cafe_laatste_unlock")
    assert st.state == "Winter"
    assert st.attributes["time"] == "2026-12-01T13:00:00+01:00"


# ---------------------------------------------------------------- kernbug: volle ringbuffer

async def test_eerste_start_stille_baseline(hass, devices, events):
    cafe, _ = devices
    fill(cafe, 1000)
    await setup_two_entries(hass)
    assert events == []
    st = hass.states.get("sensor.vto_cafe_laatste_unlock")
    assert st.state == "Oud999"
    assert len(st.attributes["recent"]) == 50
    assert st.attributes["recent"][0]["name"] == "Oud999"   # recentste eerst
    assert st.attributes["recent"][-1]["name"] == "Oud950"


async def test_nieuwe_toegang_bij_volle_buffer_wordt_gezien(hass, devices, events, logbook_calls):
    cafe, _ = devices
    fill(cafe, 1000)
    await setup_two_entries(hass)
    assert cafe.log[-1]["RecNo"] == 1000
    cafe.add_log(T0, name="Eliot")
    assert cafe.log[-1]["RecNo"] == 1000     # zoals op het echte toestel: RecNo blijft 1000
    assert len(cafe.log) == 1000
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["Eliot"]
    assert hass.states.get("sensor.vto_cafe_laatste_unlock").state == "Eliot"
    assert len(logbook_calls) == 1
    assert logbook_calls[0]["entity_id"] == "sensor.vto_cafe_laatste_unlock"
    assert "Eliot via code (geopend) op 25/09/2026 14:00:00" in logbook_calls[0]["message"]
    # nog eens pollen zonder nieuwe records: niets dubbel
    await poll(hass, "cafe")
    assert len(events) == 1 and len(logbook_calls) == 1


async def test_meerdere_nieuwe_toegangen_zelfde_seconde_en_volgorde(hass, devices, events):
    cafe, _ = devices
    fill(cafe, 1000)
    await setup_two_entries(hass)
    cafe.add_log(T0, name="A")
    cafe.add_log(T0, name="B", status=0)
    cafe.add_log(T0 + 5, name="C", method=1, card="0CAF6BF1")
    await poll(hass, "cafe")
    assert [(e["name"], e["opened"], e["method"]) for e in events] == [("A", True, "code"), ("B", False, "code"), ("C", True, "badge")]
    # een extra record op exact hetzelfde tijdstip als het laatste verwerkte
    cafe.add_log(T0 + 5, name="D")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["A", "B", "C", "D"]


async def test_herstart_niets_dubbel_niets_verloren(hass, devices, events):
    cafe, kam = devices
    fill(cafe, 1000)
    fill(kam, 300)
    entries = await setup_two_entries(hass)
    cafe.add_log(T0, name="VoorHerstart")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["VoorHerstart"]
    await restart(hass, entries)
    assert [e["name"] for e in events] == ["VoorHerstart"]          # niet opnieuw gemeld
    # sensor toont na herstart meteen de laatste toegang, niet "onbekend"
    assert hass.states.get("sensor.vto_cafe_laatste_unlock").state == "VoorHerstart"
    # toegangen terwijl HA uit stond
    await restart_with_offline_events(hass, entries, cafe, kam)
    assert [e["name"] for e in events] == ["VoorHerstart", "TijdensDown1", "TijdensDown2", "KamDown"]


async def restart_with_offline_events(hass, entries, cafe, kam):
    for e in entries:
        assert await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()
    cafe.add_log(T0 + 60, name="TijdensDown1")
    cafe.add_log(T0 + 120, name="TijdensDown2")
    kam.add_log(T0 + 130, name="KamDown")
    with patch("custom_components.btechnics_vto.VTOClient", FakeClient):
        for e in entries:
            assert await hass.config_entries.async_setup(e.entry_id)
        await hass.async_block_till_done()


async def test_twee_entries_overschrijven_elkaars_register_niet(hass, devices, events, hass_storage):
    cafe, kam = devices
    fill(cafe, 10)
    fill(kam, 10)
    await setup_two_entries(hass)
    cafe.add_log(T0, name="X")
    await poll(hass, "cafe")
    kam.add_log(T0 + 1, name="Y")
    await poll(hass, "kammerstraat")
    await hass.async_block_till_done()
    data = hass_storage[STORAGE_KEY]["data"]
    assert set(data["log_state"]) == {"cafe", "kammerstraat"}
    assert data["log_state"]["cafe"]["t"] == T0
    assert data["log_state"]["kammerstraat"]["t"] == T0 + 1
    assert set(data["protected"]) == {"cafe", "kammerstraat"}
    assert coord(hass, "cafe").registry is coord(hass, "kammerstraat").registry


async def test_upgrade_van_oud_recno_formaat(hass, devices, events, hass_storage):
    cafe, kam = devices
    fill(cafe, 1000)
    fill(kam, 1000)
    hass_storage[STORAGE_KEY] = {"version": 1, "minor_version": 1, "key": STORAGE_KEY, "data": {
        "managed": {}, "protected": {"cafe": [1], "kammerstraat": [1]}, "log_state": {"cafe": 1000, "kammerstraat": 1000}}}
    await setup_two_entries(hass)
    assert events == []   # stille baseline, geen 1000 valse meldingen
    cafe.add_log(T0, name="NaUpgrade")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["NaUpgrade"]
    assert hass_storage[STORAGE_KEY]["data"]["protected"]["cafe"] == [1]   # bestaande bescherming blijft


async def test_buffer_gewist_en_klok_teruggezet(hass, devices, events):
    from custom_components.btechnics_vto.const import LOG_LOST_POLLS
    cafe, _ = devices
    cafe.add_log(T0, name="A")
    await setup_two_entries(hass)
    cafe.log.clear()                          # fabrieksreset van het toestel, klok nog niet juist
    cafe.add_log(T0 - 3600, name="NaReset")
    for _ in range(LOG_LOST_POLLS):
        await poll(hass, "cafe")
    assert events == []                       # nooit valse meldingen
    assert hass.states.get("sensor.vto_cafe_laatste_unlock").state == "NaReset"   # na nieuwe baseline
    cafe.add_log(T0 - 3500, name="Daarna")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["Daarna"]


async def test_buffer_gewist_klok_juist(hass, devices, events):
    cafe, _ = devices
    cafe.add_log(T0, name="A")
    await setup_two_entries(hass)
    cafe.log.clear()
    cafe.add_log(T0 + 60, name="NaWissen")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["NaWissen"]


async def test_lege_buffer_en_onbekende_methode(hass, devices, events):
    cafe, _ = devices
    await setup_two_entries(hass)
    st = hass.states.get("sensor.vto_cafe_laatste_unlock")
    assert st.state == "unknown"
    assert st.attributes["recent"] == []
    cafe.add_log(T0, name="?", method=37, status=0)
    await poll(hass, "cafe")
    st = hass.states.get("sensor.vto_cafe_laatste_unlock")
    assert st.attributes["method"] == "onbekend (37)"
    assert st.attributes["opened"] is False
    assert st.attributes["icon"] == "mdi:door-closed-lock"


async def test_toestel_onbereikbaar_en_terug(hass, devices, events):
    cafe, _ = devices
    fill(cafe, 5)
    await setup_two_entries(hass)
    c = coord(hass, "cafe")
    real = FakeClient.unlocks

    def boom(self, count=200):
        raise OSError("timeout")

    with patch.object(FakeClient, "unlocks", boom):
        await poll(hass, "cafe")
    assert c.last_update_success is False
    assert hass.states.get("sensor.vto_cafe_laatste_unlock").state == "unavailable"
    with patch.object(FakeClient, "unlocks", lambda self, count=200: (_ for _ in ()).throw(ValueError("geen json"))):
        await poll(hass, "cafe")
    assert c.last_update_success is False
    cafe.add_log(T0, name="Terug")
    await poll(hass, "cafe")
    assert c.last_update_success is True
    assert [e["name"] for e in events] == ["Terug"]
    assert FakeClient.unlocks is real
    assert cafe.session_active is False   # altijd uitgelogd, ook na fouten


# ---------------------------------------------------------------- codes

async def call(hass, service, data, response=False):
    return await hass.services.async_call(DOMAIN, service, data, blocking=True, return_response=response)


async def test_codes_toevoegen_wijzigen_verwijderen(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    res = await call(hass, "add_code", {"name": " Jan Peeters ", "code": "123456", "doors": ["Cafe", "kammerstraat"]}, True)
    cid = res["id"]
    assert set(res["doors"]) == {"cafe", "kammerstraat"}
    assert any(r["CommonPassword"] == "123456" and r["UserID"] == "Jan Peeters" for r in cafe.codes)
    assert any(r["CommonPassword"] == "123456" for r in kam.codes)
    assert hass.states.get("sensor.vto_cafe_codes").state == "2"

    with pytest.raises(HomeAssistantError, match="bestaat al"):
        await call(hass, "add_code", {"name": "Dubbel", "code": "123456", "doors": ["Cafe"]}, True)
    with pytest.raises(HomeAssistantError, match="bestaat al"):
        await call(hass, "add_code", {"name": "Dubbel", "code": "936100", "doors": ["Cafe"]}, True)

    await call(hass, "update_code", {"id": cid, "name": "Jan P", "code": "654321", "doors": ["Cafe"]}, True)
    assert any(r["CommonPassword"] == "654321" and r["UserID"] == "Jan P" for r in cafe.codes)
    assert not any(r["CommonPassword"] in ("123456", "654321") for r in kam.codes)
    lst = await call(hass, "list_codes", {}, True)
    row = next(r for r in lst["codes"] if r["code"] == "654321")
    assert row["managed"] is True and row["id"] == cid and list(row["doors"]) == ["Cafe"]

    await call(hass, "remove_code", {"id": cid})
    assert not any(r["CommonPassword"] == "654321" for r in cafe.codes)
    assert cid not in coord(hass, "cafe").registry.managed
    # bestaande (niet-beheerde) codes zijn onaantastbaar
    assert any(r["UserID"] == "Adriaan" for r in cafe.codes)
    with pytest.raises(HomeAssistantError, match="onbekende id"):
        await call(hass, "remove_code", {"id": "bestaatniet"})


async def test_veiligheidscontrole_wijzigt_geen_vreemde_code(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    res = await call(hass, "add_code", {"name": "Tijdelijk", "code": "111111", "doors": ["Cafe"]}, True)
    recno = res["doors"]["cafe"]
    # iemand past via de webinterface van het toestel dat record aan naar een andere persoon
    rec = next(r for r in cafe.codes if r["RecNo"] == recno)
    rec["UserID"], rec["CommonPassword"] = "Iemand Anders", "222222"
    cafe.calls.clear()
    with pytest.raises(HomeAssistantError, match="veiligheidscontrole"):
        await call(hass, "update_code", {"id": res["id"], "code": "333333"}, True)
    # het record is niet meer van de integratie: de claim erop wordt losgelaten, zodat er
    # later ook nooit meer iets naar geschreven kan worden
    assert res["id"] not in coord(hass, "cafe").registry.managed
    with pytest.raises(HomeAssistantError, match="onbekende id"):
        await call(hass, "remove_code", {"id": res["id"]})
    assert cafe.calls == []   # niets geschreven of verwijderd
    assert (rec["UserID"], rec["CommonPassword"]) == ("Iemand Anders", "222222")

    # zelfde controle rechtstreeks bij verwijderen
    res2 = await call(hass, "add_code", {"name": "Tijdelijk2", "code": "121299", "doors": ["Cafe"]}, True)
    rec2 = next(r for r in cafe.codes if r["RecNo"] == res2["doors"]["cafe"])
    rec2["CommonPassword"] = "999000"
    cafe.calls.clear()
    with pytest.raises(HomeAssistantError, match="veiligheidscontrole"):
        await call(hass, "remove_code", {"id": res2["id"]})
    assert cafe.calls == [] and rec2 in cafe.codes
    assert res2["id"] not in coord(hass, "cafe").registry.managed


async def test_toevoegen_alles_of_niets(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    kam.fail_add = True
    with pytest.raises(HomeAssistantError, match="insert mislukt"):
        await call(hass, "add_code", {"name": "Half", "code": "444444", "doors": ["Cafe", "Kammerstraat"]}, True)
    assert not any(r["CommonPassword"] == "444444" for r in cafe.codes)   # teruggedraaid
    assert coord(hass, "cafe").registry.managed == {}


async def test_ongeldige_invoer(hass, devices):
    await setup_two_entries(hass)
    with pytest.raises(Exception):
        await call(hass, "add_code", {"name": "X", "code": "12", "doors": ["Cafe"]}, True)
    with pytest.raises(HomeAssistantError, match="onbekende deur"):
        await call(hass, "add_code", {"name": "X", "code": "123456", "doors": ["Nergens"]}, True)
    with pytest.raises(HomeAssistantError, match="naam"):
        await call(hass, "add_code", {"name": "   ", "code": "123456", "doors": ["Cafe"]}, True)


# ---------------------------------------------------------------- gelijktijdigheid, services, attributen

async def test_gelijktijdige_poll_en_service_geen_sessie_race(hass, devices):
    cafe, kam = devices
    fill(cafe, 50)
    await setup_two_entries(hass)
    cafe.slow = 0.02
    c = coord(hass, "cafe")
    results = await asyncio.gather(
        c.async_refresh(),
        call(hass, "list_log", {"count": 1200}, True),
        call(hass, "add_code", {"name": "Race", "code": "555555", "doors": ["Cafe"]}, True),
        c.async_refresh(),
        return_exceptions=True,
    )
    assert not [r for r in results if isinstance(r, Exception)], results
    assert c.last_update_success is True
    log = results[1]["log"]
    assert len(log) == 50 and log[0]["tijd"] >= log[-1]["tijd"]
    assert all(set(e) == {"tijd", "deur", "naam", "methode", "geopend", "kaart"} for e in log)


async def test_list_log_tijdzone_en_beide_deuren(hass, devices):
    cafe, kam = devices
    cafe.add_log(int(datetime(2026, 9, 25, 12, 43, 44, tzinfo=timezone.utc).timestamp()), name="Eliot")
    kam.add_log(int(datetime(2026, 9, 25, 11, 12, 46, tzinfo=timezone.utc).timestamp()), name="Aardig")
    await setup_two_entries(hass)
    log = (await call(hass, "list_log", {}, True))["log"]
    assert [(e["deur"], e["naam"], e["tijd"]) for e in log] == [
        ("Cafe", "Eliot", "2026-09-25T14:43:44+02:00"),
        ("Kammerstraat", "Aardig", "2026-09-25T13:12:46+02:00"),
    ]


async def test_list_log_ruwe_velden(hass, devices):
    cafe, _ = devices
    cafe.add_log(int(datetime(2026, 9, 25, 12, 43, 44, tzinfo=timezone.utc).timestamp()), name="Eliot")
    await setup_two_entries(hass)
    log = (await call(hass, "list_log", {"raw": True}, True))["log"]
    e = next(x for x in log if x["naam"] == "Eliot")
    assert e["ruw"]["UserID"] == "Eliot" and "RecNo" in e["ruw"] and "CreateTime" in e["ruw"]
    log = (await call(hass, "list_log", {}, True))["log"]
    assert all("ruw" not in x for x in log)


async def test_klok_van_de_toestellen(hass, devices):
    await setup_two_entries(hass)
    r = (await call(hass, "device_time", {}, True))["toestellen"]
    assert [x["deur"] for x in r] == ["Cafe", "Kammerstraat"]
    assert len(r[0]["time"]["time"]) == 19 and r[0]["locales"]["table"]["DSTEnable"] is False and "home_assistant" in r[0]


async def test_attributen_niet_in_databank(hass, devices):
    from custom_components.btechnics_vto.sensor import CountSensor, LastUnlockSensor
    assert "lijst" in CountSensor._unrecorded_attributes
    assert {"recent", "card"} <= LastUnlockSensor._unrecorded_attributes
    await setup_two_entries(hass)
    st = hass.states.get("sensor.vto_cafe_codes")
    assert st.attributes["lijst"] == [{"naam": "Adriaan", "code": "936100"}]


async def test_unload_ruimt_services_op(hass, devices):
    e1, e2 = await setup_two_entries(hass)
    assert hass.services.has_service(DOMAIN, "list_log")
    assert await hass.config_entries.async_unload(e1.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "list_log")          # andere entry draait nog
    log = await call(hass, "list_log", {}, True)
    assert log == {"log": []}
    assert await hass.config_entries.async_unload(e2.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "list_log")


# ---------------------------------------------------------------- scenario's uit de onafhankelijke review

async def test_record_met_toekomstig_tijdstip_blokkeert_niets(hass, devices, events):
    cafe, _ = devices
    fill(cafe, 10)
    await setup_two_entries(hass)
    cafe.add_log(T0 + 3 * 365 * 86400, name="Bogus")   # klok fout vlak na opstart van het toestel
    await poll(hass, "cafe")
    cafe.add_log(T0 + 10, name="A")
    cafe.add_log(T0 + 20, name="B")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["Bogus", "A", "B"]
    assert hass.states.get("sensor.vto_cafe_laatste_unlock").state == "B"
    assert hass.states.get("sensor.vto_cafe_laatste_unlock").attributes["recent"][0]["name"] == "B"


async def test_klok_enkele_seconden_teruggezet(hass, devices, events):
    cafe, _ = devices
    cafe.add_log(T0, name="A")
    await setup_two_entries(hass)
    cafe.add_log(T0 - 20, name="NaNtpCorrectie")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["NaNtpCorrectie"]


async def test_identieke_records_over_twee_polls(hass, devices, events):
    cafe, _ = devices
    fill(cafe, 5)
    await setup_two_entries(hass)
    cafe.add_log(T0, name="?", status=0)
    await poll(hass, "cafe")
    cafe.add_log(T0, name="?", status=0)     # zelfde foute code, zelfde seconde, volgende poll
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["?", "?"]


async def test_meer_dan_1000_toegangen_terwijl_ha_uit_stond(hass, devices, events):
    cafe, kam = devices
    fill(cafe, 1000)
    entries = await setup_two_entries(hass)
    for e in entries:
        assert await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()
    for i in range(1000):
        cafe.add_log(T0 + i, name=f"N{i}")
    with patch("custom_components.btechnics_vto.VTOClient", FakeClient):
        for e in entries:
            assert await hass.config_entries.async_setup(e.entry_id)
        await hass.async_block_till_done()
    assert len(events) == 1000 and events[0]["name"] == "N0" and events[-1]["name"] == "N999"
    cafe.add_log(T0 + 5000, name="Daarna")
    await poll(hass, "cafe")
    assert events[-1]["name"] == "Daarna" and len(events) == 1001


async def test_event_bevat_geen_kaartnummer(hass, devices, events):
    cafe, _ = devices
    await setup_two_entries(hass)
    cafe.add_log(T0, name="Jan Hoozee", method=1, card="ECBA62F1")
    await poll(hass, "cafe")
    assert "card" not in events[0] and events[0]["method"] == "badge"


async def test_meldingen_wachten_tot_ha_gestart_is(hass, devices, events, logbook_calls):
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState
    cafe, _ = devices
    await setup_two_entries(hass)
    hass.set_state(CoreState.starting)
    cafe.add_log(T0, name="TijdensOpstart")
    await poll(hass, "cafe")
    assert events == [] and logbook_calls == []
    assert hass.states.get("sensor.vto_cafe_laatste_unlock").state == "TijdensOpstart"
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert [e["name"] for e in events] == ["TijdensOpstart"] and len(logbook_calls) == 1


async def test_list_log_sorteert_correct_rond_wintertijd(hass, devices):
    cafe, _ = devices
    early = int(datetime(2026, 10, 25, 0, 10, tzinfo=timezone.utc).timestamp())   # 02:10+02:00
    late = int(datetime(2026, 10, 25, 1, 5, tzinfo=timezone.utc).timestamp())     # 02:05+01:00
    cafe.add_log(early, name="Eerst")
    cafe.add_log(late, name="Later")
    await setup_two_entries(hass)
    log = (await call(hass, "list_log", {}, True))["log"]
    assert [(e["naam"], e["tijd"]) for e in log] == [("Later", "2026-10-25T02:05:00+01:00"), ("Eerst", "2026-10-25T02:10:00+02:00")]


async def test_timeout_na_opslaan_bij_toevoegen_wordt_teruggedraaid(hass, devices):
    import http.client
    cafe, kam = devices
    await setup_two_entries(hass)
    kam.raise_after_add = http.client.IncompleteRead(b"")     # geen OSError en toch opgeslagen
    with pytest.raises(HomeAssistantError):
        await call(hass, "add_code", {"name": "Tijdelijk", "code": "777777", "doors": ["Cafe", "Kammerstraat"]}, True)
    assert not any(r["CommonPassword"] == "777777" for r in cafe.codes)
    assert not any(r["CommonPassword"] == "777777" for r in kam.codes)
    assert coord(hass, "cafe").registry.managed == {}
    # opnieuw proberen lukt gewoon (geen spookcode achtergebleven)
    await call(hass, "add_code", {"name": "Tijdelijk", "code": "777777", "doors": ["Cafe", "Kammerstraat"]}, True)


async def test_bestaande_code_op_tweede_deur_wordt_niet_verwijderd(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    kam.add_code_rec("Iemand", "888888")        # buiten de integratie toegevoegd, cache weet het nog niet
    with pytest.raises(HomeAssistantError, match="bestaat al"):
        await call(hass, "add_code", {"name": "Nieuw", "code": "888888", "doors": ["Cafe", "Kammerstraat"]}, True)
    assert any(r["UserID"] == "Iemand" and r["CommonPassword"] == "888888" for r in kam.codes)   # onaangeroerd
    assert not any(r["CommonPassword"] == "888888" for r in cafe.codes)                       # teruggedraaid


async def test_timeout_na_opslaan_bij_wijzigen(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    cid = (await call(hass, "add_code", {"name": "Piet", "code": "121212", "doors": ["Cafe"]}, True))["id"]
    cafe.raise_after_update = OSError("timeout")
    with pytest.raises(HomeAssistantError):
        await call(hass, "update_code", {"id": cid, "code": "343434"}, True)
    reg = coord(hass, "cafe").registry
    assert reg.managed[cid]["code"] == "343434"          # register volgt het toestel
    await call(hass, "update_code", {"id": cid, "code": "565656"}, True)
    assert any(r["CommonPassword"] == "565656" for r in cafe.codes)
    await call(hass, "remove_code", {"id": cid})
    assert not any(r["UserID"] == "Piet" for r in cafe.codes) and reg.managed == {}


async def test_gelijktijdig_wijzigen_en_verwijderen_blijft_consistent(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    cid = (await call(hass, "add_code", {"name": "Duo", "code": "909090", "doors": ["Cafe", "Kammerstraat"]}, True))["id"]
    cafe.slow = kam.slow = 0.01
    res = await asyncio.gather(
        call(hass, "update_code", {"id": cid, "code": "919191"}, True),
        call(hass, "remove_code", {"id": cid}),
        return_exceptions=True,
    )
    reg = coord(hass, "cafe").registry
    for m in reg.managed.values():
        for did, recno in m["doors"].items():
            dev = cafe if did == "cafe" else kam
            rec = next(r for r in dev.codes if r["RecNo"] == recno)
            assert (rec["UserID"], rec["CommonPassword"]) == (m["name"], m["code"])
    managed_codes = {m["code"] for m in reg.managed.values()}
    for dev in (cafe, kam):
        for r in dev.codes:
            if r["UserID"] == "Duo":
                assert r["CommonPassword"] in managed_codes, (res, dev.codes)


async def test_herladen_gebruikt_zelfde_register(hass, devices):
    entries = await setup_two_entries(hass)
    reg = coord(hass, "cafe").registry
    await restart(hass, entries)
    assert coord(hass, "cafe").registry is reg is coord(hass, "kammerstraat").registry


# ---------------------------------------------------------------- scenario's uit de tweede review

async def test_onvolledige_uitlezing_geeft_geen_vloed(hass, devices, events):
    cafe, _ = devices
    fill(cafe, 1000)
    await setup_two_entries(hass)
    before = hass.states.get("sensor.vto_cafe_laatste_unlock").state
    cafe.partial_next = 300                   # toestel bezet: slechts een deel van de buffer
    await poll(hass, "cafe")
    assert events == []
    assert hass.states.get("sensor.vto_cafe_laatste_unlock").state == before   # geen oude toegang getoond
    await poll(hass, "cafe")                  # volgende volledige uitlezing
    assert events == []
    cafe.add_log(T0, name="Echt")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["Echt"]


async def test_onvolledige_eerste_uitlezing_geeft_geen_vloed(hass, devices, events):
    cafe, _ = devices
    fill(cafe, 1000)
    cafe.partial_next = 300                   # baseline op een onvolledige uitlezing
    await setup_two_entries(hass)
    await poll(hass, "cafe")                  # volledig: 700 "nieuwe" records binnen 30 s kan niet
    assert events == []
    cafe.add_log(T0, name="Echt")
    await poll(hass, "cafe")
    assert [e["name"] for e in events] == ["Echt"]


async def test_update_neemt_geen_handmatige_code_over(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    cid = (await call(hass, "add_code", {"name": "Jan", "code": "1234", "doors": ["Cafe"]}, True))["id"]
    hand = kam.add_code_rec("Jan", "5678")    # met de hand aangemaakt op Kammerstraat
    with pytest.raises(HomeAssistantError, match="bestaat al"):
        await call(hass, "update_code", {"id": cid, "code": "5678", "doors": ["Cafe", "Kammerstraat"]}, True)
    reg = coord(hass, "cafe").registry
    assert "kammerstraat" not in reg.managed[cid]["doors"]
    kam.calls.clear()
    await call(hass, "remove_code", {"id": cid})
    assert kam.calls == []                    # handmatige code nooit aangeraakt
    assert any(r["RecNo"] == hand and r["CommonPassword"] == "5678" for r in kam.codes)


async def test_terugdraaien_met_onbereikbaar_toestel_wordt_gemeld(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    notes = []

    async def _note(call):
        notes.append(call.data["message"])

    hass.services.async_register("persistent_notification", "create", _note)
    kam.raise_after_add = OSError("timeout")
    orig_add = FakeClient.add_code

    def add_then_offline(self, name, code):
        try:
            return orig_add(self, name, code)
        finally:
            if self.dev is kam:
                kam.offline = True            # verbinding weg net na het opslaan

    with patch.object(FakeClient, "add_code", add_then_offline):
        with pytest.raises(HomeAssistantError, match="controleer manueel"):
            await call(hass, "add_code", {"name": "Wifi", "code": "246810", "doors": ["Cafe", "Kammerstraat"]}, True)
    await hass.async_block_till_done()
    assert not any(r["CommonPassword"] == "246810" for r in cafe.codes)   # cafe teruggedraaid
    assert notes and "Kammerstraat" in notes[0]
    kam.offline = False


async def test_losgelaten_code_wordt_gemeld(hass, devices):
    cafe, _ = devices
    await setup_two_entries(hass)
    notes = []

    async def _note(call):
        notes.append(call.data["message"])

    hass.services.async_register("persistent_notification", "create", _note)
    res = await call(hass, "add_code", {"name": "Gast", "code": "135791", "doors": ["Cafe"]}, True)
    rec = next(r for r in cafe.codes if r["RecNo"] == res["doors"]["cafe"])
    rec["UserID"] = "Andere Gast"
    with pytest.raises(HomeAssistantError):
        await call(hass, "remove_code", {"id": res["id"]})
    await hass.async_block_till_done()
    assert notes and "Andere Gast" in notes[0] and "NIET verwijderd" in notes[0]


async def test_geannuleerde_aanroep_loopt_toch_af(hass, devices):
    cafe, kam = devices
    await setup_two_entries(hass)
    cafe.slow = kam.slow = 0.05
    task = hass.async_create_task(call(hass, "add_code", {"name": "Annul", "code": "112233", "doors": ["Cafe", "Kammerstraat"]}, True))
    await asyncio.sleep(0.08)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await hass.async_block_till_done()
    reg = coord(hass, "cafe").registry
    on_dev = {d for d, dev in (("cafe", cafe), ("kammerstraat", kam)) if any(r["CommonPassword"] == "112233" for r in dev.codes)}
    registered = {d for m in reg.managed.values() if m["code"] == "112233" for d in m["doors"]}
    assert on_dev == registered == {"cafe", "kammerstraat"}


async def test_twee_deuren_in_een_entry(hass, devices, events):
    cafe, kam = devices
    await hass.config.async_set_time_zone("Europe/Brussels")
    e = MockConfigEntry(domain=DOMAIN, data={"doors": [door("cafe", "Cafe", "10.0.0.1"), door("kammerstraat", "Kammerstraat", "10.0.0.2")]})
    e.add_to_hass(hass)
    with patch("custom_components.btechnics_vto.VTOClient", FakeClient):
        assert await hass.config_entries.async_setup(e.entry_id)
        await hass.async_block_till_done()
    cafe.add_log(T0, name="C")
    kam.add_log(T0, name="K")
    await poll(hass, "cafe")
    await poll(hass, "kammerstraat")
    assert sorted(ev["name"] for ev in events) == ["C", "K"]
    res = await call(hass, "add_code", {"name": "Beide", "code": "998877", "doors": ["Cafe", "Kammerstraat"]}, True)
    assert set(res["doors"]) == {"cafe", "kammerstraat"}
    assert hass.states.get("sensor.vto_kammerstraat_codes").state == "2"


async def test_identieke_records_in_kleine_buffer(hass, devices, events):
    cafe, _ = devices
    await setup_two_entries(hass)                  # lege buffer
    cafe.add_log(T0, name="?", status=0)
    await poll(hass, "cafe")
    cafe.add_log(T0, name="?", status=0)
    await poll(hass, "cafe")
    cafe.add_log(T0, name="?", status=0)
    await poll(hass, "cafe")
    assert len(events) == 3


async def test_doorloopunt_van_v020_zonder_lengte(hass, devices, events, hass_storage):
    from custom_components.btechnics_vto.const import STORAGE_KEY
    cafe, _ = devices
    fill(cafe, 30)
    tail = [[str(r["CreateTime"]), "", r["UserID"], "0", "1"] for r in cafe.log[-10:]]
    hass_storage[STORAGE_KEY] = {"version": 1, "minor_version": 1, "key": STORAGE_KEY, "data": {
        "managed": {}, "protected": {}, "log_state": {"cafe": {"tail": tail, "t": cafe.log[-1]["CreateTime"]}}}}
    cafe.add_log(T0, name="NaUpgrade")
    await setup_two_entries(hass)
    assert [e["name"] for e in events] == ["NaUpgrade"]
    assert hass_storage[STORAGE_KEY]["data"]["log_state"]["cafe"]["len"] == 31


async def test_codes_en_logboek_enkel_voor_beheerders(hass, devices, hass_read_only_user):
    import pytest
    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized
    await setup_two_entries(hass)
    ctx = Context(user_id=hass_read_only_user.id)
    for svc, data in (("list_codes", {}), ("list_log", {}), ("device_time", {}),
                      ("add_code", {"name": "X", "code": "123456", "doors": ["Cafe"]}),
                      ("remove_code", {"id": "x"}), ("update_code", {"id": "x"})):
        with pytest.raises(Unauthorized):
            await hass.services.async_call(DOMAIN, svc, data, blocking=True, context=ctx,
                                           return_response=svc in ("list_codes", "list_log", "device_time"))
    raw = (await call(hass, "list_codes", {"raw": True}, True))["ruw"]
    assert raw["Cafe"]["codes"][0]["CommonPassword"] == "936100" and "badges" in raw["Cafe"]
