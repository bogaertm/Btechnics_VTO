"""Foto bij elke toegang: opslag, koppeling aan het logboek, beveiliging en bewaartermijn."""
import threading
import time
from datetime import datetime, timezone

import pytest

from custom_components.btechnics_vto import camera
from custom_components.btechnics_vto.const import DOMAIN

from .test_integration import devices, setup_two_entries  # noqa: F401

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200 + b"\xff\xd9"


def store(tmp_path):
    return camera.PhotoStore(str(tmp_path / "a.db"), str(tmp_path / "fotos"))


def test_opslaan_koppelen_en_opruimen(tmp_path):
    s = store(tmp_path)
    now = int(time.time())
    p1 = s.save("cafe", now - 100, JPEG)
    p2 = s.save("cafe", now - 5, JPEG)
    s.save("kammerstraat", now - 5, JPEG)
    rows = [{"door_id": "cafe", "ts": now - 2}, {"door_id": "cafe", "ts": now - 98}, {"door_id": "cafe", "ts": now - 500}]
    s.match(rows)
    assert rows[0]["photo"] == p2 and rows[1]["photo"] == p1 and "photo" not in rows[2]
    # een foto hoort bij hoogstens een rij
    rows = [{"door_id": "cafe", "ts": now - 4}, {"door_id": "cafe", "ts": now - 6}]
    s.match(rows)
    assert [r.get("photo") for r in rows].count(p2) == 1
    assert s.path(p1).read_bytes() == JPEG and s.path(999) is None
    old = s.save("cafe", now - (camera.PHOTO_KEEP_DAYS + 1) * 86400, JPEG)
    old_path = s.path(old)
    assert s.prune() == 1 and not old_path.exists() and s.path(p1) is not None


def test_pad_blijft_in_fotomap(tmp_path):
    s = store(tmp_path)
    pid = s.save("cafe", int(time.time()), JPEG)
    with s._conn() as c:
        c.execute("UPDATE photo SET file = '../../etc/passwd' WHERE id = ?", (pid,))
    assert s.path(pid) is None


def test_verkleinen():
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (1280, 720), (200, 100, 50)).save(buf, "JPEG")
    small = camera.shrink(buf.getvalue())
    assert Image.open(io.BytesIO(small)).size == (800, 450)
    assert camera.shrink(b"geen jpeg") == b"geen jpeg"


async def test_toegang_maakt_foto_en_koppelt_ze(hass, devices, hass_client, hass_ws_client, hass_read_only_access_token,
                                               aiohttp_client, monkeypatch):
    import custom_components.btechnics_vto as integ
    listeners = []

    class FakeListener:
        def __init__(self, name, host, user, pw, on_event, on_state):
            self.name, self.on_event, self.on_state = name, on_event, on_state
            listeners.append(self)

        def start(self):
            self.on_state(True)

        def stop(self):
            pass

    async def fake_grab(self):
        return JPEG

    monkeypatch.setattr(integ, "EVENTS_ENABLED", True)
    monkeypatch.setattr(integ, "EventListener", FakeListener)
    monkeypatch.setattr(camera.DoorCamera, "grab", fake_grab)
    cafe, _ = devices
    from homeassistant.setup import async_setup_component
    assert await async_setup_component(hass, "http", {})
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    assert len(listeners) == 2
    listeners.sort(key=lambda x: x.name != "cafe")      # entries starten in willekeurige volgorde
    now = int(time.time())
    cafe.add_log(now, name="Eliot", method=0, status=1, vto="8001")
    # gebeurtenis komt binnen op de luisterthread
    t = threading.Thread(target=listeners[0].on_event, args=({"Code": "AccessControl", "Data": {"UserID": "Eliot", "Method": 0}},))
    t.start()
    t.join()
    for _ in range(200):
        await hass.async_block_till_done()
        if hass.data[integ.PHOTO_KEY].latest("cafe", now - 60):
            break
        await __import__("asyncio").sleep(0.05)
    ph = hass.data[integ.PHOTO_KEY].latest("cafe", now - 60)
    assert ph is not None
    # andere gebeurtenissen maken geen foto
    listeners[0].on_event({"Code": "CallNoAnswered"})
    c = await hass_ws_client(hass)
    await c.send_json_auto_id({"type": f"{DOMAIN}/history", "person": "Eliot", "days": 2})
    r = (await c.receive_json())["result"]
    assert r["rows"][0]["photo"] == ph["id"]
    await c.send_json_auto_id({"type": f"{DOMAIN}/doors"})
    d = (await c.receive_json())["result"]["doors"][0]
    assert d["last_unlock"]["photo"] == ph["id"] and d["events"]["ok"] is True
    # foto ophalen: beheerder wel, gewone gebruiker niet
    client = await hass_client()
    resp = await client.get(f"/api/btechnics_vto/foto/{ph['id']}")
    assert resp.status == 200 and (await resp.read()) == JPEG
    resp = await client.get("/api/btechnics_vto/foto/99999")
    assert resp.status == 404
    ro = await hass_client(hass_read_only_access_token)
    resp = await ro.get(f"/api/btechnics_vto/foto/{ph['id']}")
    assert resp.status == 403
