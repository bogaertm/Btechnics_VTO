"""Toegangsarchief: bewaart elke toegang van alle deuren tot ARCHIVE_KEEP_DAYS lang.

De toestellen zelf houden maar ~1000 records bij (enkele weken tot maanden). Dit archief
(SQLite-bestand in de Home Assistant configmap) maakt een jaaroverzicht mogelijk.

Synchronisatie is zelfherstellend en gebruikt dezelfde positie-logica als de coordinator:
de laatste records in het archief worden in de buffer van het toestel opgezocht en alles wat
daarna staat wordt toegevoegd. Een lege archieftabel neemt de volledige buffer over (backfill),
een onvolledige uitlezing voegt niets verkeerds toe en de volgende volledige uitlezing vult aan.

Alle methodes zijn blokkerend en moeten in de executor draaien.
"""
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, tzinfo

from .const import ARCHIVE_KEEP_DAYS, LOG_LOST_POLLS, LOG_TAIL
from .records import method_label, new_since, own_numbers, rec_key, rec_room, rec_vto

SCHEMA = """
CREATE TABLE IF NOT EXISTS access (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    door_id TEXT    NOT NULL,
    door    TEXT    NOT NULL,
    ts      INTEGER NOT NULL,
    name    TEXT    NOT NULL,
    card    TEXT    NOT NULL,
    method  TEXT    NOT NULL,
    status  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_access_door_id ON access (door_id, id);
CREATE INDEX IF NOT EXISTS ix_access_ts ON access (ts);
CREATE INDEX IF NOT EXISTS ix_access_name ON access (name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS sync_state (
    door_id TEXT PRIMARY KEY,
    buf_len INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS door_vto (
    door_id TEXT PRIMARY KEY,
    vto     TEXT NOT NULL
);
"""

# Een rij is een kopie van een ander toestel als haar toestelnummer (vto) gekend is en niet het
# eigen nummer van haar deur is. Kopieen blijven bewaard maar worden nergens getoond of geteld.
# Tijdstip: ts is de ruwe CreateTime van het toestel (de kloktijd van het toestel, gecodeerd als
# epoch) en dient enkel om records in de buffer terug te vinden. t is het echte tijdstip (UTC epoch),
# omgerekend met de klokinstellingen van het toestel. Rijen van voor v0.3.5 krijgen t bij de upgrade.
TS = "COALESCE(t, ts)"

OWN = ("NOT EXISTS (SELECT 1 FROM door_vto d WHERE d.door_id = access.door_id "
       "AND access.vto <> '' AND d.vto <> access.vto)")

# Rij zonder naam (het toestel bewaart dan een lege naam of "?"): een label volgens de methode, zodat
# een opening via de binnenpost niet als onbekende code verschijnt. Zelfde regels als records.who().
NAME = ("(CASE WHEN name NOT IN ('', '?') THEN name "
        "WHEN method = '4' AND room = 'HA' THEN 'Op afstand' "
        "WHEN method = '4' THEN TRIM('Binnenpost ' || COALESCE(room, '')) "
        "WHEN method = '5' THEN 'Exitknop' "
        "WHEN method = '20' THEN 'Ongeldige invoer' "
        "WHEN method IN ('1', '2', '3') THEN 'Onbekende badge' "
        "WHEN method = '0' AND status <> '1' THEN 'Foute code' "
        "ELSE 'Onbekende code' END)")

MAX_ROWS = 50000


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _merge_people(raw) -> list:
    """Zelfde persoon met andere hoofdletters samenvoegen; de meest gebruikte schrijfwijze tonen."""
    merged = {}
    for p in raw:
        m = merged.setdefault(p["name"].casefold(), {"spellings": {}, "count": 0, "opened": 0, "last": 0, "doors": set()})
        m["spellings"][p["name"]] = m["spellings"].get(p["name"], 0) + p["n"]
        m["count"] += p["n"]
        m["opened"] += p["o"] or 0
        m["last"] = max(m["last"], p["last"] or 0)
        m["doors"] |= set((p["doors"] or "").split(",")) - {""}
    out = [
        {"name": max(m["spellings"].items(), key=lambda x: (x[1], x[0]))[0], "count": m["count"], "opened": m["opened"],
         "refused": m["count"] - m["opened"], "last": m["last"], "doors": sorted(m["doors"])}
        for m in merged.values()
    ]
    out.sort(key=lambda p: (-p["count"], p["name"].casefold()))
    return out[:1000]


class AccessArchive:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._checked = set()   # deuren waarvan de oude rijen deze sessie al nagekeken zijn
        self._timed = set()     # deuren waarvan de rijen zonder echt tijdstip al aangevuld zijn
        self._lost = {}         # aantal opeenvolgende uitlezingen die niet aansluiten, per deur
        with self._lock, closing(self._conn()) as c, c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            # archief van v0.3.0/0.3.1: kolom met het toestelnummer toevoegen
            cols = {r["name"] for r in c.execute("PRAGMA table_info(access)")}
            if "vto" not in cols:
                c.execute("ALTER TABLE access ADD COLUMN vto TEXT NOT NULL DEFAULT ''")
            # archief van v0.3.0 tot 0.3.4: kolom met het echte tijdstip toevoegen
            if "t" not in cols:
                c.execute("ALTER TABLE access ADD COLUMN t INTEGER")
            # archief tot 0.3.6: nummer van de binnenpost bij openen via de binnenpost
            if "room" not in cols:
                c.execute("ALTER TABLE access ADD COLUMN room TEXT NOT NULL DEFAULT ''")

    def _conn(self):
        c = sqlite3.connect(self._path, timeout=30)
        c.row_factory = sqlite3.Row
        # hoofdletterongevoelig vergelijken met volledige Unicode (NOCASE/LIKE van SQLite kent enkel ASCII)
        c.create_function("cf", 1, lambda v: v.casefold() if isinstance(v, str) else v, deterministic=True)
        return c

    # ---------------------------------------------------------------- schrijven

    def sync(self, door_id: str, door_name: str, recs: list, to_utc=None) -> int:
        """recs = volledige buffer van het toestel in toestelvolgorde. Geeft het aantal nieuwe rijen.

        to_utc zet de ruwe CreateTime om naar het echte tijdstip (None: klok van het toestel onbekend,
        dan blijft t leeg tot ze wel gekend is)."""
        with self._lock, closing(self._conn()) as c, c:
            last = c.execute(
                "SELECT ts, card, name, method, status FROM access WHERE door_id = ? ORDER BY id DESC LIMIT ?",
                (door_id, LOG_TAIL),
            ).fetchall()
            tail = [[str(r["ts"]), r["card"], r["name"], r["method"], r["status"]] for r in reversed(last)]
            max_ts = c.execute("SELECT MAX(ts) FROM access WHERE door_id = ?", (door_id,)).fetchone()[0] or 0
            row = c.execute("SELECT buf_len FROM sync_state WHERE door_id = ?", (door_id,)).fetchone()
            keys = [rec_key(r) for r in recs]
            state = {"tail": tail, "t": int(max_ts)}
            if row is not None:
                state["len"] = row["buf_len"]
            new = new_since(recs, keys, state)
            if new is None and recs:
                # Sluit niet aan. Meestal een onvolledige uitlezing (volgende keer wel), maar na een
                # fabrieksreset of gewiste buffer blijft dat zo: dan, net als de coordinator, na
                # LOG_LOST_POLLS keer de buffer overnemen, zonder wat al in het archief staat.
                self._lost[door_id] = self._lost.get(door_id, 0) + 1
                if self._lost[door_id] >= LOG_LOST_POLLS:
                    self._lost[door_id] = 0
                    have = {
                        (str(r["ts"]), r["card"], r["name"], r["method"], r["status"])
                        for r in c.execute(
                            "SELECT ts, card, name, method, status FROM access WHERE door_id = ? AND ts BETWEEN ? AND ?",
                            (door_id, min(int(k[0]) for k in keys), max(int(k[0]) for k in keys)),
                        )
                    }
                    new = [r for r, k in zip(recs, keys) if tuple(k) not in have]
                    row = None   # buflengte opnieuw vastleggen
            else:
                self._lost[door_id] = 0
            if new is not None and (row is None or row["buf_len"] != len(recs)):
                # enkel bij een aansluitende uitlezing (een onvolledige mag de lengte niet verzetten)
                # en enkel als er iets veranderde: niet elke 30 s naar de schijf/SD-kaart schrijven
                c.execute(
                    "INSERT INTO sync_state (door_id, buf_len) VALUES (?, ?) "
                    "ON CONFLICT(door_id) DO UPDATE SET buf_len = excluded.buf_len",
                    (door_id, len(recs)),
                )
            if new:
                c.executemany(
                    "INSERT INTO access (door_id, door, ts, name, card, method, status, vto, t, room) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(door_id, door_name, int(k[0]), k[2], k[1], k[3], k[4], rec_vto(r), to_utc(int(k[0])) if to_utc else None, rec_room(r))
                     for r, k in ((r, rec_key(r)) for r in new)],
                )
            if to_utc is not None and door_id not in self._timed:
                self._fill_time(c, door_id, to_utc)
                self._timed.add(door_id)
            # toestelnummers enkel bijwerken als er iets bijkwam (en een keer per deur na het opstarten)
            if new or door_id not in self._checked:
                self._fill_legacy(c, door_id, recs)
                self._fill_room(c, door_id, recs)
                self._update_own(c)
                if new is not None:
                    self._checked.add(door_id)
            return len(new or [])

    def _fill_time(self, c, door_id, to_utc):
        """Rijen zonder echt tijdstip (van voor v0.3.5, of toen de klok van het toestel onbekend was)."""
        todo = c.execute("SELECT id, ts FROM access WHERE door_id = ? AND t IS NULL", (door_id,)).fetchall()
        if todo:
            c.executemany("UPDATE access SET t = ? WHERE id = ?", [(to_utc(r["ts"]), r["id"]) for r in todo])

    def true_times(self, door_id: str) -> dict:
        """Echt tijdstip per record (sleutel = inhoud zoals rec_key), om de buffer van het toestel te tonen."""
        with closing(self._conn()) as c:
            return {
                (str(r["ts"]), r["card"], r["name"], r["method"], r["status"]): r["t"]
                for r in c.execute(
                    f"SELECT ts, card, name, method, status, {TS} t FROM access WHERE door_id = ?", (door_id,)
                )
            }

    def recent(self, door_id: str, limit: int) -> list:
        """Laatst geregistreerde eigen toegangen van een deur (volgorde van het toestel, niet op tijdstip:
        een verkeerd klokje mag niet bepalen wat de laatste toegang is), met het echte tijdstip."""
        with closing(self._conn()) as c:
            rows = c.execute(
                f"SELECT door_id, door, {TS} ts, {NAME} name, card, method, status, room FROM access "
                f"WHERE door_id = ? AND {OWN} ORDER BY id DESC LIMIT ?",
                (door_id, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def _fill_room(self, c, door_id, recs):
        """Rijen van voor v0.3.7 hebben het nummer van de binnenpost nog niet: aanvullen uit de buffer."""
        todo = [(rec_room(r), door_id, *rec_key(r)) for r in recs if rec_room(r)]
        if not todo or c.execute(
            "SELECT 1 FROM access WHERE door_id = ? AND method = '4' AND room = '' LIMIT 1", (door_id,)
        ).fetchone() is None:
            return
        c.executemany(
            "UPDATE access SET room = ? WHERE door_id = ? AND room = '' AND ts = ? AND card = ? AND name = ? "
            "AND method = ? AND status = ?",
            [(v, d, int(k0), k1, k2, k3, k4) for v, d, k0, k1, k2, k3, k4 in todo],
        )

    def _fill_legacy(self, c, door_id, recs):
        """Rijen van voor v0.3.3 hebben nog geen toestelnummer: aanvullen uit de buffer van het toestel."""
        if c.execute("SELECT 1 FROM access WHERE door_id = ? AND vto = '' LIMIT 1", (door_id,)).fetchone() is None:
            return
        todo = [(rec_vto(r), door_id, *rec_key(r)) for r in recs if rec_vto(r)]
        c.executemany(
            "UPDATE access SET vto = ? WHERE door_id = ? AND vto = '' AND ts = ? AND card = ? AND name = ? "
            "AND method = ? AND status = ?",
            [(v, d, int(k0), k1, k2, k3, k4) for v, d, k0, k1, k2, k3, k4 in todo],
        )

    def _update_own(self, c):
        """Eigen toestelnummer per deur bijwerken, en oude rijen die niet meer in de buffer staan maar
        wel een tweeling hebben bij het toestel dat ze echt registreerde, als kopie markeren."""
        counts = {}
        for r in c.execute(
            "SELECT door_id, vto, COUNT(*) n FROM access WHERE vto <> '' AND door_id NOT LIKE '%~verwijderd~%' "
            "GROUP BY door_id, vto"
        ):
            counts.setdefault(r["door_id"], {})[r["vto"]] = r["n"]
        own = own_numbers(counts)
        cur = {r["door_id"]: r["vto"] for r in c.execute("SELECT door_id, vto FROM door_vto")}
        changed = {d: v for d, v in own.items() if cur.get(d) != v}
        if changed:
            c.executemany(
                "INSERT INTO door_vto (door_id, vto) VALUES (?, ?) ON CONFLICT(door_id) DO UPDATE SET vto = excluded.vto",
                list(changed.items()),
            )
        if c.execute("SELECT 1 FROM access WHERE vto = '' LIMIT 1").fetchone() is not None:
            c.execute(
                "UPDATE access SET vto = (SELECT b.vto FROM access b JOIN door_vto d ON d.door_id = b.door_id AND d.vto = b.vto "
                "  WHERE b.door_id <> access.door_id AND b.ts = access.ts AND b.name = access.name "
                "  AND b.method = access.method AND b.status = access.status LIMIT 1) "
                "WHERE vto = '' AND EXISTS (SELECT 1 FROM access b JOIN door_vto d ON d.door_id = b.door_id AND d.vto = b.vto "
                "  WHERE b.door_id <> access.door_id AND b.ts = access.ts AND b.name = access.name "
                "  AND b.method = access.method AND b.status = access.status "
                "  AND b.vto <> COALESCE((SELECT vto FROM door_vto WHERE door_id = access.door_id), ''))"
            )

    def own_vto(self, door_id: str) -> str | None:
        with closing(self._conn()) as c:
            r = c.execute("SELECT vto FROM door_vto WHERE door_id = ?", (door_id,)).fetchone()
        return r["vto"] if r else None

    def detach_door(self, door_id: str, now: float | None = None) -> None:
        """Deur verwijderd: historiek bewaren maar loskoppelen van het deur-id, zodat een nieuwe
        deur met dezelfde naam met een lege lei begint (volledige backfill, geen valse meldingen)."""
        with self._lock, closing(self._conn()) as c, c:
            new_id = f"{door_id}~verwijderd~{int(now or time.time())}"
            c.execute("UPDATE access SET door_id = ? WHERE door_id = ?", (new_id, door_id))
            c.execute("UPDATE door_vto SET door_id = ? WHERE door_id = ?", (new_id, door_id))
            c.execute("DELETE FROM sync_state WHERE door_id = ?", (door_id,))

    def prune(self, now: float | None = None) -> int:
        cutoff = int((now or time.time()) - ARCHIVE_KEEP_DAYS * 86400)
        with self._lock, closing(self._conn()) as c, c:
            # enkel rijen met een echt tijdstip: een rij zonder t (klok nog niet gelezen) wacht
            return c.execute("DELETE FROM access WHERE t IS NOT NULL AND t < ?", (cutoff,)).rowcount

    # ---------------------------------------------------------------- lezen

    def query(self, tz: tzinfo, door_ids=None, search=None, person=None, status="all", start=None, end=None,
              limit=200, offset=0, max_id=None) -> dict:
        """tz = tijdzone van Home Assistant, voor de indeling per maand (niet die van de container)."""
        where, args = [OWN], []
        if door_ids:
            where.append(f"door_id IN ({','.join('?' * len(door_ids))})")
            args += list(door_ids)
        if person is not None:
            where.append(f"cf({NAME}) = ?")
            args.append(person.casefold())
        if search:
            where.append(f"(cf({NAME}) LIKE ? ESCAPE '\\' OR cf(card) LIKE ? ESCAPE '\\')")
            term = _like(search.strip().casefold())
            args += [term, term]
        if status == "opened":
            where.append("status = '1'")
        elif status == "refused":
            where.append("status <> '1'")
        if start is not None:
            where.append(f"{TS} >= ?")
            args.append(int(start))
        if end is not None:
            where.append(f"{TS} < ?")
            args.append(int(end))
        limit = max(0, min(int(limit), MAX_ROWS))
        with closing(self._conn()) as c:
            # momentopname: volgende pagina's (en de CSV) zien dezelfde set, ook als er intussen
            # nieuwe toegangen bijkomen; anders schuiven rijen op en komen ze dubbel voor
            if max_id is None:
                max_id = c.execute("SELECT COALESCE(MAX(id), 0) FROM access").fetchone()[0]
            where.append("id <= ?")
            args.append(int(max_id))
            w = "WHERE " + " AND ".join(where)
            tot = c.execute(
                f"SELECT COUNT(*) n, SUM(status = '1') o FROM access {w}", args
            ).fetchone()
            rows = c.execute(
                f"SELECT id, door_id, door, {TS} ts, {NAME} name, card, method, status, room FROM access {w} "
                f"ORDER BY {TS} DESC, id DESC LIMIT ? OFFSET ?",
                args + [limit, max(0, int(offset))],
            ).fetchall()
            raw_people = c.execute(
                f"SELECT {NAME} name, COUNT(*) n, SUM(status = '1') o, MAX({TS}) last, GROUP_CONCAT(DISTINCT door) doors "
                f"FROM access {w} GROUP BY {NAME}",
                args,
            ).fetchall()
            months = {}
            for ts, st in c.execute(f"SELECT {TS}, status FROM access {w}", args):
                m = datetime.fromtimestamp(ts, tz).strftime("%Y-%m")
                b = months.setdefault(m, [0, 0])
                b[0] += 1
                b[1] += st == "1"
        total, opened = tot["n"] or 0, tot["o"] or 0
        return {
            "max_id": int(max_id),
            "total": total,
            "opened": opened,
            "refused": total - opened,
            "rows": [
                {
                    "id": r["id"], "door_id": r["door_id"], "door": r["door"], "ts": r["ts"],
                    "name": r["name"] or "?", "card": r["card"], "method": method_label(r["method"], r["room"]),
                    "opened": r["status"] == "1",
                }
                for r in rows
            ],
            "people": _merge_people(raw_people),
            "months": [{"month": m, "count": n, "opened": o} for m, (n, o) in sorted(months.items())],
        }

    def unknown_cards(self, since: int, limit: int = 20) -> list:
        """Badges die sinds `since` aan een lezer werden aangeboden en geweigerd (methode 1, 2 of 3),
        nieuwste eerst. Om een nieuwe badge te registreren: badge voor de lezer houden en kiezen."""
        with closing(self._conn()) as c:
            rows = c.execute(
                f"SELECT UPPER(card) card, GROUP_CONCAT(DISTINCT door) doors, MAX({TS}) last, COUNT(*) n FROM access "
                f"WHERE card <> '' AND status <> '1' AND method IN ('1', '2', '3') AND {OWN} AND {TS} >= ? "
                "GROUP BY UPPER(card) ORDER BY last DESC LIMIT ?",
                (int(since), int(limit)),
            ).fetchall()
        return [{"card": r["card"], "doors": sorted((r["doors"] or "").split(",")), "last": r["last"], "count": r["n"]} for r in rows]

    def counts_since(self, start: int) -> dict:
        with closing(self._conn()) as c:
            rows = c.execute(
                f"SELECT door_id, COUNT(*) n, SUM(status = '1') o FROM access WHERE {TS} >= ? AND {OWN} GROUP BY door_id", (int(start),)
            ).fetchall()
        return {r["door_id"]: {"opened": r["o"] or 0, "refused": r["n"] - (r["o"] or 0)} for r in rows}

    def stats(self) -> dict:
        with closing(self._conn()) as c:
            rows = c.execute(
                f"SELECT door_id, COUNT(*) n, MIN({TS}) first, MAX({TS}) last FROM access WHERE {OWN} GROUP BY door_id"
            ).fetchall()
        return {r["door_id"]: {"count": r["n"], "first": r["first"], "last": r["last"]} for r in rows}
