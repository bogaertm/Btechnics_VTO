"""DHIP-protocol (realtime gebeurtenissen) met een nagebootst toestel."""
import json
import struct

from custom_components.btechnics_vto import dhip


def frame(obj, session=0, rid=0):
    body = json.dumps(obj).encode()
    return dhip.MAGIC + struct.pack("<IIIIII", session, rid, len(body), 0, len(body), 0) + body


class FakeSock:
    """Antwoordt zoals een VTO: eerste login met realm, tweede login ok, attach ok, daarna een gebeurtenis."""

    def __init__(self, password_ok=True):
        self.sent, self.out, self.password_ok = [], b"", password_ok

    def settimeout(self, t):
        pass

    def sendall(self, data):
        assert data[:8] == dhip.MAGIC
        ln = struct.unpack("<I", data[16:20])[0]
        msg = json.loads(data[32:32 + ln])
        self.sent.append(msg)
        m, rid = msg["method"], msg["id"]
        if m == "global.login" and not msg["params"]["password"]:
            self.out += frame({"id": rid, "result": False, "session": 777,
                               "params": {"realm": "Login to 7D08", "random": "12345", "encryption": "Default"}})
        elif m == "global.login":
            exp = dhip._md5("admin:12345:" + dhip._md5("admin:Login to 7D08:geheim"))
            ok = self.password_ok and msg["params"]["password"] == exp and msg["session"] == 777
            self.out += frame({"id": rid, "result": ok, "session": 777, "params": {"keepAliveInterval": 60}})
        elif m == "eventManager.attach":
            # een gebeurtenis die voor het antwoord binnenkomt, mag niet verloren gaan
            self.out += frame({"method": "client.notifyEventStream", "params": {"eventList": [{"Code": "Invite"}]}})
            self.out += frame({"id": rid, "result": True})
            self.out += frame({"method": "client.notifyEventStream", "params": {"eventList": [
                {"Code": "AccessControl", "Action": "Pulse", "Data": {"UserID": "Eliot", "Method": 0, "Status": 1}}]}})

    def recv(self, n):
        chunk, self.out = self.out[:7], self.out[7:]      # in kleine stukjes, zoals over het netwerk
        return chunk

    def close(self):
        pass


def client(sock):
    c = dhip.DhipClient("10.0.0.1", "admin", "geheim")
    c.sock = sock
    return c


def test_login_attach_en_gebeurtenis(monkeypatch):
    sock = FakeSock()
    monkeypatch.setattr(dhip.socket, "create_connection", lambda *a, **k: sock)
    c = dhip.DhipClient("10.0.0.1", "admin", "geheim").connect()
    assert c.session == 777 and c.keepalive == 60
    c.attach(("AccessControl",))
    assert [e["Code"] for e in dhip.events_of(c.pending.pop(0))] == ["Invite"]
    import time
    msg = c._read(time.monotonic() + 1)
    ev = dhip.events_of(msg)[0]
    assert ev["Code"] == "AccessControl" and ev["Data"]["UserID"] == "Eliot"
    assert [m["method"] for m in sock.sent] == ["global.login", "global.login", "eventManager.attach"]


def test_fout_wachtwoord(monkeypatch):
    import pytest
    sock = FakeSock(password_ok=False)
    monkeypatch.setattr(dhip.socket, "create_connection", lambda *a, **k: sock)
    with pytest.raises(dhip.DhipError):
        dhip.DhipClient("10.0.0.1", "admin", "geheim").connect()


def test_probe_geeft_nooit_een_uitzondering(monkeypatch):
    def boom(*a, **k):
        raise OSError("onbereikbaar")
    monkeypatch.setattr(dhip.socket, "create_connection", boom)
    assert dhip.probe("10.0.0.1", "a", "b", 0.1)["ok"] is False
