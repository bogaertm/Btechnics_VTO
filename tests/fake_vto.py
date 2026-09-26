"""Gesimuleerde Dahua VTO die zich gedraagt zoals de echte toestellen (vastgesteld sept 2026):
logboek = ringbuffer van max 1000 records, oudste eerst, RecNo positioneel (1..1000).
Codes-tabel heeft stabiele RecNo's met gaten."""
import threading
import time

from custom_components.btechnics_vto.api import VTOError

DEVICES = {}
CAP = 1000


class FakeDevice:
    def __init__(self, host):
        self.host = host
        self.log = []          # oudste eerst
        self.codes = []
        self.cards = []
        self.next_code_recno = 1
        self.session_active = False
        self.session_lock = threading.Lock()
        self.fail_add = False
        self.raise_after_add = None      # uitzondering NA het opslaan (bv. time-out op het antwoord)
        self.raise_after_update = None
        self.partial_next = None         # volgende logboekuitlezing onvolledig (enkel de eerste N)
        self.offline = False
        self.slow = 0.0
        self.calls = []
        # klok: kloktijd = UTC + wall_offset; CreateTime van nieuwe records is die kloktijd als epoch
        self.wall_offset = 0
        self.ntp = {"Enable": False, "Address": "time.windows.com", "TimeZone": 1, "TimeZoneDesc": "Brussels"}
        self.locales = {"DSTEnable": False, "DSTStart": {"Year": 2024, "Month": 3, "Week": -1, "Day": 0, "Hour": 2, "Minute": 0},
                        "DSTEnd": {"Year": 2024, "Month": 10, "Week": -1, "Day": 0, "Hour": 3, "Minute": 0}}
        self.summer_offset = 7200   # wat de klok wordt als zomertijd aangaat (september: UTC+2)

    def wall(self, true_utc):
        """CreateTime zoals het toestel die bewaart voor een toegang op het echte tijdstip true_utc."""
        return int(true_utc + self.wall_offset)

    def add_log(self, t, name="Jan", method=0, status=1, card="", vto=None):
        rec = {"CreateTime": t, "UserID": name, "CardName": "", "Method": method, "Status": status, "CardNo": card}
        if vto is not None:
            rec["VTONumber"] = vto
        self.log.append(rec)
        if len(self.log) > CAP:
            del self.log[: len(self.log) - CAP]
        for i, r in enumerate(self.log):
            r["RecNo"] = i + 1   # positioneel, zoals op het echte toestel

    def add_card_rec(self, name, cardno, doors=(0,)):
        """Badge zoals het echte toestel ze bewaart (september 2026 uitgelezen)."""
        self.next_card_recno = getattr(self, "next_card_recno", 1)
        rec = {"RecNo": self.next_card_recno, "CardName": name, "CardNo": cardno, "CardStatus": 0, "CardType": 0,
               "CitizenIDNo": "", "Doors": list(doors), "DynamicCheckCode": "", "FirstEnter": False, "Handicap": False,
               "IsValid": False, "Password": "", "PersonId": f"{self.next_card_recno:04d}", "RepeatEnterRouteTimeout": 0,
               "TimeSections": None, "UseTime": -1, "UserID": "9999", "UserName": name, "UserType": 0,
               "VTOPosition": "", "ValidDateEnd": "0000-00-00 00:00:00", "ValidDateStart": "0000-00-00 00:00:00"}
        self.next_card_recno += 1
        self.cards.append(rec)
        return rec["RecNo"]

    def add_code_rec(self, name, code):
        rec = {"RecNo": self.next_code_recno, "UserID": name, "CommonPassword": code, "VTONumber": "", "CreateTime": 0}
        self.next_code_recno += 1
        self.codes.append(rec)
        return rec["RecNo"]


class FakeClient:
    def __init__(self, host, https, username, password):
        self.base = ("https://" if https else "http://") + host
        self.user, self._pw = username, password
        self.dev = DEVICES[host]
        self.logged_in = False

    def login(self):
        if self.dev.offline:
            raise OSError("toestel onbereikbaar")
        with self.dev.session_lock:
            if self.dev.session_active:
                raise VTOError("sessie al actief: gelijktijdige login (race)")
            self.dev.session_active = True
        self.logged_in = True
        return self

    def logout(self):
        self.logged_in = False
        self.dev.session_active = False

    def _need(self):
        if not self.logged_in:
            raise VTOError("niet ingelogd")
        if self.dev.slow:
            time.sleep(self.dev.slow)

    def reboot(self):
        self._need()
        self.dev.reboots = getattr(self.dev, "reboots", 0) + 1

    def open_door(self, channel=0, short_number="HA"):
        self._need()
        if getattr(self.dev, "refuse_open", False):
            raise VTOError("deur openen geweigerd: {'code': 268959743}")
        self.dev.opened = getattr(self.dev, "opened", 0) + 1

    def info(self):
        self._need()
        return {"type": "VTO4202", "version": "4.600", "serial": self.dev.host}

    def clock(self):
        from datetime import datetime, timedelta, timezone
        wall = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=self.dev.wall_offset)
        return {"time": {"time": wall.strftime("%Y-%m-%d %H:%M:%S")}, "ntp": {"table": dict(self.dev.ntp)},
                "locales": {"table": dict(self.dev.locales)}}

    def get_config(self, name):
        self._need()
        return dict({"NTP": self.dev.ntp, "Locales": self.dev.locales}[name])

    def set_config(self, name, table):
        self._need()
        self.dev.calls.append(("set_config", name))
        if name == "NTP":
            self.dev.ntp = dict(table)
        else:
            self.dev.locales = dict(table)
        if self.dev.locales.get("DSTEnable") and self.dev.ntp.get("Enable"):
            self.dev.wall_offset = self.dev.summer_offset

    def unlocks(self, count=200):
        self._need()
        if self.dev.partial_next is not None:
            n, self.dev.partial_next = self.dev.partial_next, None
            count = min(count, n)
        return [dict(r) for r in self.dev.log[:count]]

    def codes(self):
        self._need()
        return [dict(r) for r in self.dev.codes]

    def cards(self):
        self._need()
        return [dict(r) for r in self.dev.cards]

    def add_code(self, name, code, base=None):
        self._need()
        if self.dev.fail_add:
            raise VTOError("insert mislukt")
        self.dev.calls.append(("add", name, code))
        recno = self.dev.add_code_rec(name, code)
        if base:
            rec = self.dev.codes[-1]
            rec.update({k: v for k, v in base.items() if k != "RecNo"})
            rec["UserID"], rec["CommonPassword"] = name, code
        if self.dev.raise_after_add:
            exc, self.dev.raise_after_add = self.dev.raise_after_add, None
            raise exc
        return recno

    def update_code(self, recno, name, code, base=None):
        self._need()
        self.dev.calls.append(("update", recno, name, code))
        for r in self.dev.codes:
            if r["RecNo"] == recno:
                if base is not None:
                    r.update({k: v for k, v in base.items() if k != "RecNo"})
                r["UserID"], r["CommonPassword"] = name, code
                if self.dev.raise_after_update:
                    exc, self.dev.raise_after_update = self.dev.raise_after_update, None
                    raise exc
                return
        raise VTOError("update mislukt")

    def remove_code(self, recno):
        self._need()
        self.dev.calls.append(("remove", recno))
        self.dev.codes = [r for r in self.dev.codes if r["RecNo"] != recno]
        if getattr(self.dev, "raise_after_remove", None):
            exc, self.dev.raise_after_remove = self.dev.raise_after_remove, None
            raise exc
        if getattr(self.dev, "offline_after_remove", False):
            self.dev.offline_after_remove, self.dev.offline = False, True
            raise OSError("verbinding weg")

    def camera_probe(self):
        return {"foto_kanaal_1": {"ok": True, "bytes": 1234, "grootte": [1280, 720], "ms": 50}}

    def add_card(self, record):
        self._need()
        if self.dev.fail_add:
            raise VTOError("insert mislukt")
        self.dev.calls.append(("add_card", record.get("CardNo")))
        if "RecNo" in record:
            raise VTOError("RecNo hoort niet in een nieuw record")
        self.dev.next_card_recno = getattr(self.dev, "next_card_recno", 1)
        rec = dict(record, RecNo=self.dev.next_card_recno)
        self.dev.next_card_recno += 1
        self.dev.cards.append(rec)
        if self.dev.raise_after_add:
            exc, self.dev.raise_after_add = self.dev.raise_after_add, None
            raise exc
        return rec["RecNo"]

    def update_card(self, recno, record):
        self._need()
        self.dev.calls.append(("update_card", recno))
        for i, r in enumerate(self.dev.cards):
            if r["RecNo"] == recno:
                self.dev.cards[i] = dict(record, RecNo=recno)
                return
        raise VTOError("update mislukt")

    def remove_card(self, recno):
        self._need()
        self.dev.calls.append(("remove_card", recno))
        self.dev.cards = [r for r in self.dev.cards if r["RecNo"] != recno]
        if getattr(self.dev, "raise_after_remove", None):
            exc, self.dev.raise_after_remove = self.dev.raise_after_remove, None
            raise exc
        if getattr(self.dev, "offline_after_remove", False):
            self.dev.offline_after_remove, self.dev.offline = False, True
            raise OSError("verbinding weg")
