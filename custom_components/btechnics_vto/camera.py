"""Foto bij elke toegang.

Het toestel meldt een toegang meteen via DHIP (poort 5000, zie dhip.py). Op dat moment neemt
Home Assistant een foto van de camera van het toestel: via de HTTP-CGI (snapshot.cgi) als het
toestel die heeft, anders een beeld uit de RTSP-stroom met ffmpeg (bv. VTO4202F, firmware 4.600,
waar de CGI uitgeschakeld is). Foto's worden verkleind bewaard in de configmap en na
PHOTO_KEEP_DAYS dagen gewist.

Bewaartermijn: de Belgische camerawet laat camerabeelden maximaal een maand bewaren, tenzij ze
nodig zijn als bewijs (zie besafe.be, het register van de beeldverwerkingsactiviteiten).
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

_LOGGER = logging.getLogger(__name__)

PHOTO_DIR = "btechnics_vto_fotos"
PHOTO_KEEP_DAYS = 30
PHOTO_WIDTH = 800          # verkleind: ongeveer 50 kB per foto
MATCH_WINDOW = 45          # seconden tussen de gebeurtenis en de rij in het logboek
MIN_INTERVAL = 3           # hoogstens een foto per 3 s per deur

SCHEMA = """
CREATE TABLE IF NOT EXISTS photo (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    door_id TEXT    NOT NULL,
    t       INTEGER NOT NULL,
    file    TEXT    NOT NULL,
    info    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_photo_door_t ON photo (door_id, t);
"""


def shrink(jpeg: bytes, width: int = PHOTO_WIDTH) -> bytes:
    """Verkleinen en opnieuw comprimeren; lukt dat niet, dan het origineel bewaren."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(jpeg))
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)))
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=75, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return jpeg


class PhotoStore:
    """Foto's op schijf en een tabel in het toegangsarchief (zelfde databank)."""

    def __init__(self, db_path: str, base_dir: str):
        self._db = db_path
        self.base = Path(base_dir)
        self._lock = threading.Lock()
        with self._lock, closing(self._conn()) as c, c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self._db, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def save(self, door_id: str, t: int, jpeg: bytes, info: str = "") -> int:
        d = datetime.fromtimestamp(t, timezone.utc)
        rel = f"{door_id}/{d:%Y/%m/%d}/{d:%H%M%S}_{int(time.time() * 1000) % 1000:03d}.jpg"
        p = self.base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(jpeg)
        with self._lock, closing(self._conn()) as c, c:
            return c.execute("INSERT INTO photo (door_id, t, file, info) VALUES (?, ?, ?, ?)",
                             (door_id, int(t), rel, info)).lastrowid

    def path(self, photo_id: int) -> Path | None:
        with closing(self._conn()) as c:
            r = c.execute("SELECT file FROM photo WHERE id = ?", (int(photo_id),)).fetchone()
        if r is None:
            return None
        p = (self.base / r["file"]).resolve()
        return p if p.is_file() and self.base.resolve() in p.parents else None

    def match(self, rows: list) -> None:
        """Per rij (door_id, ts) de dichtstbijzijnde foto van die deur binnen MATCH_WINDOW zoeken.
        Elke foto hoort bij hoogstens een rij: de rij die er in tijd het dichtst bij ligt."""
        if not rows:
            return
        lo = min(r["ts"] for r in rows) - MATCH_WINDOW
        hi = max(r["ts"] for r in rows) + MATCH_WINDOW
        doors = sorted({r["door_id"] for r in rows})
        with closing(self._conn()) as c:
            photos = c.execute(
                f"SELECT id, door_id, t FROM photo WHERE t BETWEEN ? AND ? AND door_id IN ({','.join('?' * len(doors))})",
                [lo, hi, *doors],
            ).fetchall()
        pairs = sorted(
            ((abs(p["t"] - r["ts"]), i, p["id"]) for i, r in enumerate(rows) for p in photos
             if p["door_id"] == r["door_id"] and abs(p["t"] - r["ts"]) <= MATCH_WINDOW),
        )
        used_rows, used_photos = set(), set()
        for _, i, pid in pairs:
            if i in used_rows or pid in used_photos:
                continue
            rows[i]["photo"] = pid
            used_rows.add(i)
            used_photos.add(pid)

    def latest(self, door_id: str, since: int):
        with closing(self._conn()) as c:
            r = c.execute("SELECT id, t FROM photo WHERE door_id = ? AND t >= ? ORDER BY t DESC LIMIT 1",
                          (door_id, int(since))).fetchone()
        return dict(r) if r else None

    def prune(self, now: float | None = None) -> int:
        cutoff = int((now or time.time()) - PHOTO_KEEP_DAYS * 86400)
        with self._lock, closing(self._conn()) as c, c:
            old = c.execute("SELECT id, file FROM photo WHERE t < ?", (cutoff,)).fetchall()
            for r in old:
                try:
                    (self.base / r["file"]).unlink()
                except FileNotFoundError:
                    pass
            c.execute("DELETE FROM photo WHERE t < ?", (cutoff,))
        # lege mappen opruimen
        for root, dirs, files in os.walk(self.base, topdown=False):
            if root != str(self.base) and not dirs and not files:
                try:
                    os.rmdir(root)
                except OSError:
                    pass
        return len(old)

    def stats(self) -> dict:
        with closing(self._conn()) as c:
            r = c.execute("SELECT COUNT(*) n, MIN(t) first FROM photo").fetchone()
        size = sum(f.stat().st_size for f in self.base.rglob("*.jpg")) if self.base.exists() else 0
        return {"count": r["n"], "first": r["first"], "bytes": size, "keep_days": PHOTO_KEEP_DAYS}


def rtsp_url(client, subtype: int = 0) -> str:
    host = urlparse(client.base).hostname
    return (f"rtsp://{quote(client.user, safe='')}:{quote(client._pw, safe='')}@{host}:554"
            f"/cam/realmonitor?channel=1&subtype={subtype}")


async def rtsp_frame(hass, url: str, timeout: float = 12) -> bytes:
    """Een beeld uit de RTSP-stroom met ffmpeg, via TCP (betrouwbaarder dan UDP)."""
    binary = "ffmpeg"
    data = hass.data.get("ffmpeg")
    if data is not None and getattr(data, "binary", None):
        binary = data.binary
    proc = await asyncio.create_subprocess_exec(
        binary, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url,
        "-frames:v", "1", "-vf", f"scale='min({PHOTO_WIDTH},iw)':-2", "-q:v", "5", "-f", "image2", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError("ffmpeg: geen beeld binnen de tijd") from None
    if not out.startswith(b"\xff\xd8"):
        # wachtwoord nooit in een foutmelding
        raise RuntimeError("ffmpeg: " + err.decode(errors="replace").replace(url, "rtsp://...")[-200:].strip())
    return out


class DoorCamera:
    """Kiest per deur de werkende methode (CGI of RTSP) en onthoudt ze."""

    def __init__(self, hass, coordinator):
        self.hass = hass
        self.c = coordinator
        self.method = None
        self._last = 0.0
        self.last_error = None

    async def grab(self) -> bytes:
        order = [self.method] if self.method else ["cgi", "rtsp"]
        err = None
        for m in order + [x for x in ("cgi", "rtsp") if x not in order]:
            try:
                if m == "cgi":
                    img = await self.hass.async_add_executor_job(self.c.client.snapshot)
                    img = await self.hass.async_add_executor_job(shrink, img)
                else:
                    img = await rtsp_frame(self.hass, rtsp_url(self.c.client))
                self.method, self.last_error = m, None
                return img
            except Exception as e:  # noqa: BLE001  volgende methode proberen
                err = e
        self.last_error = str(err)[:200]
        raise err

    def may_shoot(self) -> bool:
        now = time.monotonic()
        if now - self._last < MIN_INTERVAL:
            return False
        self._last = now
        return True
