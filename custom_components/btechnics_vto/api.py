"""Dahua VTO RPC2 client (sync, wordt via executor aangeroepen).

Alle methodes en tabelnamen komen uit de webinterface van de toestellen zelf
(RecordFinder / RecordUpdater), bevestigd op firmware 4.511 en 4.600.
"""
import hashlib
import json
import ssl
import urllib.request

from .const import TABLE_CARDS, TABLE_CODES, TABLE_LOG

def _make_ctx():
    """Dahua VTO's spreken TLS 1.2 met RSA key exchange (AES256-GCM-SHA384) en een
    zelfgetekend certificaat. Moderne OpenSSL weigert dat standaard, vandaar
    een eigen context: geen verificatie, ruime ciphers, security level 0."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except ssl.SSLError:
        ctx.set_ciphers("DEFAULT")
    return ctx


CTX = _make_ctx()


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest().upper()


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8, context=CTX) as r:
        return json.loads(r.read().decode())


class VTOError(Exception):
    pass


class VTOClient:
    def __init__(self, host: str, https: bool, username: str, password: str):
        self.base = ("https://" if https else "http://") + host
        self.user = username
        self._pw = password
        self.session = None
        self._id = 1

    # ---------- sessie ----------
    def login(self):
        url = self.base + "/RPC2_Login"
        r1 = _post(url, {"method": "global.login", "params": {"userName": self.user, "password": "", "clientType": "Web3.0"}, "id": 1})
        p = r1.get("params") or {}
        if "realm" not in p:
            raise VTOError(f"geen login challenge: {r1}")
        self.session = r1["session"]
        h1 = _md5(f"{self.user}:{p['realm']}:{self._pw}")
        h2 = _md5(f"{self.user}:{p['random']}:{h1}")
        r2 = _post(url, {"method": "global.login", "params": {"userName": self.user, "password": h2, "clientType": "Web3.0",
                          "authorityType": "Default", "passwordType": "Default"}, "id": 2, "session": self.session})
        if not r2.get("result"):
            raise VTOError(f"login mislukt: {r2.get('error')}")
        return self

    def logout(self):
        try:
            self.call("global.logout")
        except Exception:
            pass
        self.session = None

    def call(self, method, params=None, obj=None):
        self._id += 1
        body = {"method": method, "params": params, "id": self._id, "session": self.session}
        if obj is not None:
            body["object"] = obj
        return _post(self.base + "/RPC2", body)

    # ---------- lezen ----------
    def info(self):
        t = self.call("magicBox.getDeviceType").get("params", {}).get("type")
        v = self.call("magicBox.getSoftwareVersion").get("params", {}).get("version", {})
        s = self.call("magicBox.getSerialNo").get("params", {}).get("sn")
        return {"type": t, "version": v.get("Version") if isinstance(v, dict) else v, "serial": s}

    def find(self, table, count=1000):
        obj = self.call("RecordFinder.factory.create", {"name": table}).get("result")
        if not obj:
            raise VTOError(f"{table} kan niet gelezen worden")
        try:
            if not self.call("RecordFinder.startFind", {"condition": {}}, obj).get("result"):
                raise VTOError(f"{table} kan niet gelezen worden")
            recs = []
            while True:
                f = self.call("RecordFinder.doFind", {"count": 100}, obj)
                batch = (f.get("params") or {}).get("records") or []
                recs += batch
                if len(batch) < 100 or len(recs) >= count:
                    break
        finally:
            self.call("RecordFinder.destroy", None, obj)
        return recs

    def clock(self):
        """Alleen lezen: klok en tijdsinstellingen van het toestel (voor diagnose van tijdstippen)."""
        out = {}
        for key, method, params in (
            ("time", "global.getCurrentTime", None),
            ("ntp", "configManager.getConfig", {"name": "NTP"}),
            ("locales", "configManager.getConfig", {"name": "Locales"}),
        ):
            try:
                r = self.call(method, params)
                out[key] = r.get("params") if r.get("result") else {"fout": r.get("error")}
            except Exception as e:  # noqa: BLE001  diagnose: elke fout gewoon teruggeven
                out[key] = {"fout": str(e)}
        return out

    def get_config(self, name: str) -> dict:
        r = self.call("configManager.getConfig", {"name": name})
        if not r.get("result"):
            raise VTOError(f"getConfig {name} geweigerd: {r.get('error')}")
        return r["params"]["table"]

    def set_config(self, name: str, table: dict):
        r = self.call("configManager.setConfig", {"name": name, "table": table, "options": []})
        if not r.get("result"):
            raise VTOError(f"setConfig {name} geweigerd: {r.get('error')}")

    def codes(self):
        return self.find(TABLE_CODES, 100000)

    def cards(self):
        return self.find(TABLE_CARDS, 100000)

    def unlocks(self, count=200):
        return self.find(TABLE_LOG, count)

    # ---------- schrijven ----------
    # update_code en remove_code mogen ENKEL aangeroepen worden met een RecNo uit het
    # register van de integratie (codes die via de integratie zijn aangemaakt).
    # De integratie controleert dat vóór elke aanroep; de client zelf kent het register niet.

    def _updater(self, table):
        return self.call("RecordUpdater.factory.instance", {"name": table})["result"]

    def _insert(self, table: str, record: dict) -> int:
        obj = self._updater(table)
        try:
            r = self.call("RecordUpdater.insert", {"record": record}, obj)
        finally:
            self.call("RecordUpdater.destroy", None, obj)
        if not r.get("result"):
            raise VTOError(f"insert mislukt: {r.get('error')}")
        return int((r.get("params") or {}).get("recno", -1))

    def _update(self, table: str, recno: int, record: dict):
        obj = self._updater(table)
        try:
            r = self.call("RecordUpdater.update", {"recno": recno, "record": record}, obj)
        finally:
            self.call("RecordUpdater.destroy", None, obj)
        if not r.get("result"):
            raise VTOError(f"update mislukt: {r.get('error')}")

    def _remove(self, table: str, recno: int):
        obj = self._updater(table)
        try:
            r = self.call("RecordUpdater.remove", {"recno": recno}, obj)
        finally:
            self.call("RecordUpdater.destroy", None, obj)
        if not r.get("result"):
            raise VTOError(f"remove mislukt: {r.get('error')}")

    # base: het bestaande of bewaarde record, zodat velden als VTONumber behouden blijven.
    @staticmethod
    def _code_record(name: str, code: str, base: dict | None) -> dict:
        rec = {"VTONumber": "", "CreateTime": 0}
        rec.update({k: v for k, v in (base or {}).items() if k != "RecNo"})
        rec.update({"UserID": name, "CommonPassword": code})
        return rec

    def add_code(self, name: str, code: str, base: dict | None = None) -> int:
        return self._insert(TABLE_CODES, self._code_record(name, code, base))

    def update_code(self, recno: int, name: str, code: str, base: dict | None = None):
        self._update(TABLE_CODES, recno, self._code_record(name, code, base))

    def remove_code(self, recno: int):
        self._remove(TABLE_CODES, recno)

    # Badges: altijd het volledige record zoals het toestel het gaf (zonder RecNo), zodat
    # deuren, geldigheid en de overige instellingen van de badge behouden blijven.
    def add_card(self, record: dict) -> int:
        return self._insert(TABLE_CARDS, record)

    def update_card(self, recno: int, record: dict):
        self._update(TABLE_CARDS, recno, record)

    def remove_card(self, recno: int):
        self._remove(TABLE_CARDS, recno)
