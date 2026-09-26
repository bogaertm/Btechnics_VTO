"""Gebeurtenissen van een Dahua VTO in realtime, via het DHIP-protocol (TCP-poort 5000).

Zelfde protocol als de Dahua-apps (DMSS, SmartPSS) en het project DahuaVTO2MQTT:
elk bericht is een kop van 32 bytes ("DHIP" + sessie, volgnummer en lengte) gevolgd door JSON.
Werkt ook op firmware waar de HTTP-CGI uitgeschakeld is (VTO4202F, firmware 4.600).
Enkel lezen: login, eventManager.attach en keepAlive. Er wordt niets op het toestel gewijzigd.
"""
from __future__ import annotations

import hashlib
import json
import logging
import socket
import struct
import threading
import time

_LOGGER = logging.getLogger(__name__)

PORT = 5000
MAGIC = b"\x20\x00\x00\x00DHIP"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest().upper()


class DhipError(Exception):
    pass


class DhipClient:
    def __init__(self, host: str, username: str, password: str, port: int = PORT, timeout: float = 8):
        self.host, self.port, self.user, self._pw = host, port, username, password
        self.timeout = timeout
        self.sock = None
        self.session = 0
        self._id = 0
        self._buf = b""
        self.keepalive = 60
        self.pending = []      # berichten die binnenkwamen terwijl we op een antwoord wachtten

    # ---------- laag niveau ----------
    def _send(self, method: str, params=None) -> int:
        self._id += 1
        body = json.dumps({"id": self._id, "magic": "0x1234", "method": method, "params": params,
                           "session": self.session}).encode()
        head = MAGIC + struct.pack("<IIIIII", self.session, self._id, len(body), 0, len(body), 0)
        self.sock.sendall(head + body)
        return self._id

    def _read(self, deadline: float):
        """Een volledig bericht lezen (of None als de tijd om is)."""
        while True:
            if len(self._buf) >= 32:
                if self._buf[:8] != MAGIC:
                    raise DhipError("onverwachte gegevens van het toestel")
                ln = struct.unpack("<I", self._buf[16:20])[0]
                if len(self._buf) >= 32 + ln:
                    body, self._buf = self._buf[32:32 + ln], self._buf[32 + ln:]
                    try:
                        return json.loads(body.decode("utf-8", "replace").rstrip("\x00"))
                    except ValueError:
                        return {}
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(min(left, 1.0))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise DhipError("verbinding gesloten door het toestel")
            self._buf += chunk

    def _call(self, method: str, params=None, timeout: float | None = None) -> dict:
        rid = self._send(method, params)
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            msg = self._read(deadline)
            if msg is None:
                raise DhipError(f"geen antwoord op {method}")
            if msg.get("id") == rid:
                return msg
            self.pending.append(msg)

    # ---------- sessie ----------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        base = {"clientType": "", "ipAddr": "(null)", "loginType": "Direct", "userName": self.user}
        r1 = self._call("global.login", {**base, "password": ""})
        p = r1.get("params") or {}
        self.session = r1.get("session") or 0
        if "realm" not in p:
            raise DhipError(f"login geweigerd: {r1.get('error')}")
        h = _md5(f"{self.user}:{p['random']}:{_md5(self.user + ':' + p['realm'] + ':' + self._pw)}")
        r2 = self._call("global.login", {**base, "password": h, "authorityType": "Default", "passwordType": "Default"})
        if not r2.get("result"):
            raise DhipError(f"login mislukt: {r2.get('error')}")
        self.keepalive = int((r2.get("params") or {}).get("keepAliveInterval") or 60)
        return self

    def attach(self, codes=("AccessControl",)):
        r = self._call("eventManager.attach", {"codes": list(codes)})
        if not r.get("result"):
            raise DhipError(f"attach geweigerd: {r.get('error')}")

    def keep_alive(self):
        self._send("global.keepAlive", {"timeout": self.keepalive, "action": True})

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        finally:
            self.sock = None


def events_of(msg: dict) -> list:
    """De gebeurtenissen uit een client.notifyEventStream-bericht."""
    if not msg or msg.get("method") != "client.notifyEventStream":
        return []
    return (msg.get("params") or {}).get("eventList") or []


def probe(host: str, username: str, password: str, seconds: float = 3) -> dict:
    """Diagnose: verbinden, aanmelden en inschrijven op gebeurtenissen; enkel lezen."""
    t0 = time.monotonic()
    c = DhipClient(host, username, password, timeout=6)
    try:
        c.connect()
        c.attach(("AccessControl", "DoorStatus", "CallNoAnswered", "Invite"))
        got, deadline = [], time.monotonic() + seconds
        while time.monotonic() < deadline:
            msg = c.pending.pop(0) if c.pending else c._read(deadline)
            if msg is None:
                break
            got += [e.get("Code") for e in events_of(msg)]
        return {"ok": True, "keepalive": c.keepalive, "ms": int((time.monotonic() - t0) * 1000), "gebeurtenissen": got[:10]}
    except Exception as e:  # noqa: BLE001  diagnose
        return {"ok": False, "fout": str(e)[:200]}
    finally:
        c.close()


class EventListener(threading.Thread):
    """Houdt per deur een DHIP-verbinding open en roept on_event(event) op bij elke gebeurtenis.
    Herverbindt vanzelf (wacht 5 s, oplopend tot 5 min) als het toestel onbereikbaar is."""

    def __init__(self, name: str, host: str, username: str, password: str, on_event, on_state=None):
        super().__init__(name=f"btechnics_vto_events_{name}", daemon=True)
        self.host, self.user, self._pw = host, username, password
        self.on_event, self.on_state = on_event, on_state or (lambda ok, err=None: None)
        self._stop = threading.Event()
        self.client = None

    def stop(self):
        self._stop.set()
        c = self.client
        if c is not None:
            c.close()

    def run(self):
        wait = 5
        while not self._stop.is_set():
            c = self.client = DhipClient(self.host, self.user, self._pw)
            try:
                c.connect()
                c.attach(("AccessControl",))
                self.on_state(True)
                wait = 5
                next_ka = time.monotonic() + max(5, c.keepalive * 0.6)
                while not self._stop.is_set():
                    msg = c.pending.pop(0) if c.pending else c._read(min(next_ka, time.monotonic() + 1))
                    if msg is not None:
                        for ev in events_of(msg):
                            try:
                                self.on_event(ev)
                            except Exception:  # noqa: BLE001  een fout in de verwerking mag de verbinding niet stoppen
                                _LOGGER.exception("Verwerken van gebeurtenis mislukt")
                    if time.monotonic() >= next_ka:
                        c.keep_alive()
                        next_ka = time.monotonic() + max(5, c.keepalive * 0.6)
            except Exception as e:  # noqa: BLE001  toestel weg of herstart: opnieuw proberen
                if not self._stop.is_set():
                    self.on_state(False, str(e))
                    _LOGGER.debug("Gebeurtenissen %s onderbroken: %s", self.host, e)
            finally:
                c.close()
            self._stop.wait(wait)
            wait = min(wait * 2, 300)
