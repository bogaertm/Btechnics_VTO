"""Coordinator per deur: pollt logboek (30 s) en codes/badges (5 min), stuurt events.

Belangrijk over het logboek van de VTO (vastgesteld op de toestellen zelf, september 2026):
de tabel AccessControlCardRec is een ringbuffer van maximaal 1000 records in volgorde van
registratie (oudste eerst). RecNo is daarin GEEN uniek volgnummer: eens de buffer vol zit,
blijft het hoogste RecNo 1000 en schuiven de oudste records eruit.

Nieuwe toegangen worden daarom herkend op POSITIE in de buffer: we onthouden de laatste
records die we al verwerkt hebben (inhoud, zonder RecNo) en alles wat daarna in de buffer
staat is nieuw. Dat werkt ook als de klok van het toestel verspringt of als twee identieke
records in dezelfde seconde vallen. Enkel als die reeks helemaal uit de buffer verdwenen is
(meer dan ~990 toegangen sinds de vorige keer) vallen we terug op het tijdstip.
"""
import http.client
import logging
import threading
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import VTOClient, VTOError
from .const import (
    CODES_INTERVAL,
    DOMAIN,
    EVENT_UNLOCK,
    LOG_BURST_MAX,
    LOG_BURST_WINDOW,
    LOG_FETCH_COUNT,
    LOG_INTERVAL,
    LOG_LOST_POLLS,
    LOG_RECENT_COUNT,
    LOG_TAIL,
)
from .records import device_order, method_label, new_since, rec_key, rec_time

_LOGGER = logging.getLogger(__name__)

# Fouten die een aanroep naar de VTO kan opleveren (netwerk, TLS, afgebroken HTTP-antwoord,
# ongeldige JSON, onverwachte structuur van het antwoord).
API_ERRORS = (VTOError, OSError, http.client.HTTPException, ValueError, KeyError, TypeError, IndexError)


class DoorCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, door_id: str, door_name: str, client: VTOClient, registry, archive=None):
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {door_name}", update_interval=timedelta(seconds=LOG_INTERVAL)
        )
        self.door_id = door_id
        self.door_name = door_name
        self.client = client
        self.registry = registry
        self.archive = archive
        self.info = {}
        self.codes = []
        self.cards = []
        self.last_unlock = None
        self.recent = []
        self._last_codes_fetch = None
        self._pending = []
        self._lost_polls = 0
        self._last_ok = None   # monotone tijd van de laatste geslaagde, aansluitende uitlezing
        # Eén VTO-sessie tegelijk per toestel: de polling en service-aanroepen draaien in
        # verschillende threads en zouden anders elkaars sessie overschrijven of uitloggen.
        self._lock = threading.Lock()

    def _run(self, fn, *a):
        """Sync API-aanroep in executor, met login per beurt (VTO houdt sessies kort)."""
        with self._lock:
            self.client.login()
            try:
                return fn(*a)
            finally:
                self.client.logout()

    def _fetch(self, with_codes: bool):
        # Telkens de VOLLEDIGE ringbuffer (max 1000) ophalen: de tabel staat oudste eerst,
        # dus met minder records zouden de recentste toegangen nooit binnenkomen.
        out = {"unlocks": self.client.unlocks(LOG_FETCH_COUNT)}
        if with_codes:
            out["codes"] = self.client.codes()
            out["cards"] = self.client.cards()
            if not self.info:
                out["info"] = self.client.info()
        return out

    async def _async_update_data(self):
        now = dt_util.utcnow()
        with_codes = self._last_codes_fetch is None or (now - self._last_codes_fetch).total_seconds() >= CODES_INTERVAL
        try:
            data = await self.hass.async_add_executor_job(self._run, self._fetch, with_codes)
        except API_ERRORS as e:
            raise UpdateFailed(f"{self.door_name}: {e}") from e
        if with_codes:
            self.codes, self.cards = data["codes"], data["cards"]
            self.info = data.get("info") or self.info
            self._last_codes_fetch = now
        await self._process_unlocks(data["unlocks"])
        return {"codes": len(self.codes), "cards": len(self.cards), "last_unlock": self.last_unlock}

    async def _process_unlocks(self, raw):
        """Toont altijd het laatst geregistreerde record en meldt enkel echt nieuwe records.

        Het doorloopunt wordt persistent bewaard, zodat een herstart niets dubbel meldt en niets
        verliest. Bij de allereerste keer dat een deur gezien wordt (of na een upgrade van het oude
        RecNo-formaat) trekken we een stille baseline, anders kwam de volledige historiek van het
        toestel als "nieuw" binnen.
        """
        recs = device_order(raw)
        await self._archive(recs)
        keys = [rec_key(r) for r in recs]
        new_state = {"tail": keys[-LOG_TAIL:], "t": rec_time(recs[-1]) if recs else 0, "len": len(recs)}
        now = time.monotonic()

        state = self.registry.get_log_state(self.door_id)
        if state is None:
            self._show(recs)
            await self.registry.set_log_state(self.door_id, new_state)
            self._last_ok = now
            return

        new_recs = new_since(recs, keys, state)
        if new_recs is None:
            # Sluit niet aan: meestal een onvolledige uitlezing. Niets tonen, niets melden en het
            # doorloopunt NIET verzetten; de volgende volledige uitlezing sluit wel weer aan.
            self._lost_polls += 1
            if self._lost_polls < LOG_LOST_POLLS:
                if self._lost_polls == 1:
                    _LOGGER.warning("%s: logboek sluit niet aan op het doorloopunt (onvolledige uitlezing?), deze beurt overgeslagen", self.door_name)
                return
            _LOGGER.warning("%s: logboek sluit al %s keer niet aan (buffer gewist?), nieuwe baseline", self.door_name, self._lost_polls)
            self._lost_polls = 0
            self._show(recs)
            await self.registry.set_log_state(self.door_id, new_state)
            self._last_ok = now
            return

        self._lost_polls = 0
        self._show(recs)
        if len(new_recs) > LOG_BURST_MAX and self._last_ok is not None and now - self._last_ok < LOG_BURST_WINDOW:
            _LOGGER.warning(
                "%s: %s nieuwe records op korte tijd is onmogelijk, de vorige uitlezing was onvolledig; niet gemeld",
                self.door_name, len(new_recs),
            )
            new_recs = []
        if new_recs:
            self._announce_all([self._fmt(r) for r in new_recs])
        if new_state != state:
            await self.registry.set_log_state(self.door_id, new_state)
        self._last_ok = now

    async def _archive(self, recs):
        """Nieuwe records ook in het jaararchief bewaren. Een fout daar mag de deur nooit blokkeren."""
        if self.archive is None:
            return
        try:
            await self.hass.async_add_executor_job(self.archive.sync, self.door_id, self.door_name, recs)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("%s: toegangsarchief bijwerken mislukt", self.door_name)

    def _show(self, recs):
        self.recent = [self._fmt(r) for r in reversed(recs[-LOG_RECENT_COUNT:])]
        self.last_unlock = self._fmt(recs[-1]) if recs else None

    def _announce_all(self, unlocks):
        """Events en logboekregels pas versturen als Home Assistant volledig gestart is: bij het
        opstarten zijn het logboek en de automatiseringen mogelijk nog niet geladen."""
        if self.hass.state is CoreState.running:
            for u in unlocks:
                self._announce(u)
            return
        first = not self._pending
        self._pending.extend(unlocks)
        if first:
            @callback
            def _flush(_hass):
                pending, self._pending = self._pending, []
                for u in pending:
                    self._announce(u)

            async_at_started(self.hass, _flush)

    @callback
    def _announce(self, unlock):
        # Kaartnummer niet in het event: events worden in de databank bewaard.
        self.hass.bus.async_fire(EVENT_UNLOCK, {k: v for k, v in unlock.items() if k != "card"})
        self._log_to_logbook(unlock)

    def _log_to_logbook(self, unlock):
        """Echte logboekregel (via logbook.log), filterbaar per deur in het Home Assistant Logboek."""
        if not self.hass.services.has_service("logbook", "log"):
            return
        entity_id = er.async_get(self.hass).async_get_entity_id("sensor", DOMAIN, f"{self.door_id}_last_unlock")
        if not entity_id:
            return
        status = "geopend" if unlock["opened"] else "geweigerd"
        when = dt_util.parse_datetime(unlock["time"])
        when_txt = when.strftime("%d/%m/%Y %H:%M:%S") if when else unlock["time"]
        self.hass.async_create_task(
            self.hass.services.async_call(
                "logbook", "log",
                {
                    "name": f"VTO {unlock['door']}",
                    "message": f"{unlock['name']} via {unlock['method']} ({status}) op {when_txt}",
                    "entity_id": entity_id,
                    "domain": DOMAIN,
                },
                blocking=False,
            )
        )

    def _fmt(self, r):
        # CreateTime van het toestel is een Unix-epoch (UTC). Omzetten via de tijdzone die in
        # Home Assistant is ingesteld (Europe/Brussels), niet via de systeemtijdzone van de container.
        ts = rec_time(r)
        local_time = dt_util.as_local(dt_util.utc_from_timestamp(ts))
        return {
            "door_id": self.door_id, "door": self.door_name,
            "name": r.get("CardName") or r.get("UserID") or "?",
            "method": method_label(r.get("Method")),
            "opened": r.get("Status") == 1, "card": r.get("CardNo", ""),
            "time": local_time.isoformat(timespec="seconds"),
            "ts": ts,
        }

    async def async_refresh_codes(self):
        """Codes en badges meteen opnieuw inlezen (niet via de debouncer van async_request_refresh,
        die tot 10 s kan wachten: dan zou list_codes na een wijziging nog oude gegevens tonen)."""
        self._last_codes_fetch = None
        await self.async_refresh()
