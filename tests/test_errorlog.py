"""Foutenlog van 60 dagen."""
import logging
import time

from custom_components.btechnics_vto.const import DOMAIN
from custom_components.btechnics_vto.errorlog import ErrorLog

from .test_integration import devices, setup_two_entries  # noqa: F401


def test_foutenlog_telt_op_en_ruimt_op(tmp_path):
    log = ErrorLog(str(tmp_path / "f.db"), "custom_components.testdomein")
    log.attach()
    lg = logging.getLogger("custom_components.testdomein.x")
    for _ in range(3):
        lg.warning("Toestel %s niet bereikbaar", "Cafe")
    lg.info("dit komt er niet in")
    try:
        raise ValueError("kapot")
    except ValueError:
        lg.exception("Fout met details")
    assert log.flush_to_db() == 4
    r = log.query()
    msgs = {x["message"]: x for x in r["rows"]}
    assert msgs["Toestel Cafe niet bereikbaar"]["count"] == 3 and "dit komt er niet in" not in msgs
    assert "ValueError: kapot" in msgs["Fout met details"]["details"] and r["totals"] == {"WARNING": 3, "ERROR": 1}
    # ouder dan 60 dagen: weg bij het volgende wegschrijven
    import sqlite3
    c = sqlite3.connect(log.path)
    c.execute("UPDATE fout SET laatste = ? WHERE niveau = 'ERROR'", (time.time() - 61 * 86400,))
    c.commit(); c.close()
    lg.warning("nieuw")
    log.flush_to_db()
    assert {x["message"] for x in log.query()["rows"]} == {"Toestel Cafe niet bereikbaar", "nieuw"}
    log.detach()


async def test_foutenlog_websocket_enkel_beheerders(hass, devices, hass_ws_client, hass_read_only_access_token):
    await setup_two_entries(hass)
    logging.getLogger("custom_components.btechnics_vto.test").error("Proefmelding foutenlog")
    c = await hass_ws_client(hass)
    await c.send_json_auto_id({"type": f"{DOMAIN}/errors"})
    r = await c.receive_json()
    assert r["success"] and any(x["message"] == "Proefmelding foutenlog" for x in r["result"]["rows"]) and r["result"]["keep_days"] == 60
    ro = await hass_ws_client(hass, hass_read_only_access_token)
    await ro.send_json_auto_id({"type": f"{DOMAIN}/errors"})
    assert not (await ro.receive_json())["success"]
    hass.data[f"{DOMAIN}_errorlog"].detach()
