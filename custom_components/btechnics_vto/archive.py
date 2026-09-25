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

from .const import ARCHIVE_KEEP_DAYS, LOG_TAIL
from .records import method_label, new_since, rec_key

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
"""

# Onbekende code: het toestel bewaart soms een lege naam, soms "?". Beide tonen als "?".
NAME = "(CASE WHEN name = '' THEN '?' ELSE name END)"

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
        with self._lock, closing(self._conn()) as c, c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self._path, timeout=30)
        c.row_factory = sqlite3.Row
        # hoofdletterongevoelig vergelijken met volledige Unicode (NOCASE/LIKE van SQLite kent enkel ASCII)
        c.create_function("cf", 1, lambda v: v.casefold() if isinstance(v, str) else v, deterministic=True)
        return c

    # ---------------------------------------------------------------- schrijven

    def sync(self, door_id: str, door_name: str, recs: list) -> int:
        """recs = volledige buffer van het toestel in toestelvolgorde. Geeft het aantal nieuwe rijen."""
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
            if new is not None and (row is None or row["buf_len"] != len(recs)):
                # enkel bij een aansluitende uitlezing (een onvolledige mag de lengte niet verzetten)
                # en enkel als er iets veranderde: niet elke 30 s naar de schijf/SD-kaart schrijven
                c.execute(
                    "INSERT INTO sync_state (door_id, buf_len) VALUES (?, ?) "
                    "ON CONFLICT(door_id) DO UPDATE SET buf_len = excluded.buf_len",
                    (door_id, len(recs)),
                )
            if not new:
                return 0
            c.executemany(
                "INSERT INTO access (door_id, door, ts, name, card, method, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(door_id, door_name, int(k[0]), k[2], k[1], k[3], k[4]) for k in map(rec_key, new)],
            )
            return len(new)

    def detach_door(self, door_id: str, now: float | None = None) -> None:
        """Deur verwijderd: historiek bewaren maar loskoppelen van het deur-id, zodat een nieuwe
        deur met dezelfde naam met een lege lei begint (volledige backfill, geen valse meldingen)."""
        with self._lock, closing(self._conn()) as c, c:
            c.execute("UPDATE access SET door_id = ? WHERE door_id = ?", (f"{door_id}~verwijderd~{int(now or time.time())}", door_id))
            c.execute("DELETE FROM sync_state WHERE door_id = ?", (door_id,))

    def prune(self, now: float | None = None) -> int:
        cutoff = int((now or time.time()) - ARCHIVE_KEEP_DAYS * 86400)
        with self._lock, closing(self._conn()) as c, c:
            return c.execute("DELETE FROM access WHERE ts < ?", (cutoff,)).rowcount

    # ---------------------------------------------------------------- lezen

    def query(self, tz: tzinfo, door_ids=None, search=None, person=None, status="all", start=None, end=None,
              limit=200, offset=0, max_id=None) -> dict:
        """tz = tijdzone van Home Assistant, voor de indeling per maand (niet die van de container)."""
        where, args = [], []
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
            where.append("ts >= ?")
            args.append(int(start))
        if end is not None:
            where.append("ts < ?")
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
                f"SELECT id, door_id, door, ts, {NAME} name, card, method, status FROM access {w} "
                "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                args + [limit, max(0, int(offset))],
            ).fetchall()
            raw_people = c.execute(
                f"SELECT {NAME} name, COUNT(*) n, SUM(status = '1') o, MAX(ts) last, GROUP_CONCAT(DISTINCT door) doors "
                f"FROM access {w} GROUP BY {NAME}",
                args,
            ).fetchall()
            months = {}
            for ts, st in c.execute(f"SELECT ts, status FROM access {w}", args):
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
                    "name": r["name"] or "?", "card": r["card"], "method": method_label(r["method"]),
                    "opened": r["status"] == "1",
                }
                for r in rows
            ],
            "people": _merge_people(raw_people),
            "months": [{"month": m, "count": n, "opened": o} for m, (n, o) in sorted(months.items())],
        }

    def counts_since(self, start: int) -> dict:
        with closing(self._conn()) as c:
            rows = c.execute(
                "SELECT door_id, COUNT(*) n, SUM(status = '1') o FROM access WHERE ts >= ? GROUP BY door_id", (int(start),)
            ).fetchall()
        return {r["door_id"]: {"opened": r["o"] or 0, "refused": r["n"] - (r["o"] or 0)} for r in rows}

    def stats(self) -> dict:
        with closing(self._conn()) as c:
            rows = c.execute(
                "SELECT door_id, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM access GROUP BY door_id"
            ).fetchall()
        return {r["door_id"]: {"count": r["n"], "first": r["first"], "last": r["last"]} for r in rows}
