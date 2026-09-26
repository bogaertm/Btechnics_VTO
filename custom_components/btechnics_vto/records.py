"""Gedeelde functies voor logrecords van de VTO (gebruikt door coordinator en archief).

Het logboek van het toestel is een ringbuffer van max 1000 records in volgorde van registratie.
RecNo is positioneel en dus geen identiteit: records worden herkend op inhoud en positie.
"""
from datetime import datetime, timezone

from .const import METHOD_INDOOR, METHODS


def rec_time(r: dict) -> int:
    try:
        return int(r.get("CreateTime") or 0)
    except (TypeError, ValueError):
        return 0


def rec_no(r: dict) -> int:
    try:
        return int(r.get("RecNo"))
    except (TypeError, ValueError):
        return 0


def rec_vto(r: dict) -> str:
    """Nummer van het toestel dat de toegang registreerde (VTONumber, bv. "8001").

    Een hoofdtoestel bewaart ook een kopie van de toegangen van zijn onderstations (vastgesteld
    op Cafe 8001 met Kammerstraat 8002, september 2026). Leeg bij firmware zonder dit veld."""
    return str(r.get("VTONumber") or "").strip()


def own_numbers(counts: dict) -> dict:
    """Eigen toestelnummer per deur, uit {deur: {nummer: aantal}}.

    Een deur die maar een nummer kent, is een los toestel of onderstation: dat nummer is van haar.
    Die nummers vallen weg bij de andere deuren (daar zijn het kopieen); van wat overblijft is het
    meest voorkomende nummer het eigen nummer. Zonder gegevens of bij gelijkstand: geen eigen nummer
    (dan wordt niets verborgen)."""
    singles = {d: next(iter(c)) for d, c in counts.items() if len(c) == 1}
    out = dict(singles)
    for d, c in counts.items():
        if d in singles:
            continue
        taken = {v for od, v in singles.items() if od != d}
        rest = {v: n for v, n in c.items() if v not in taken}
        top = sorted(rest.items(), key=lambda x: -x[1])
        if top and (len(top) == 1 or top[0][1] > top[1][1]):   # bij gelijkstand geen keuze maken
            out[d] = top[0][0]
    return out


def rec_key(r: dict) -> list:
    """Inhoud van een logrecord zonder RecNo (dat is positioneel en dus geen identiteit)."""
    return [
        str(rec_time(r)),
        str(r.get("CardNo") or ""),
        str(r.get("CardName") or r.get("UserID") or ""),
        str(r.get("Method")),
        str(r.get("Status")),
    ]


def device_order(recs) -> list:
    """Records in volgorde van registratie op het toestel (RecNo oplopend, bij gelijke RecNo de
    volgorde van ontvangst). Bewust NIET op tijdstip: een verkeerd klokje mag de volgorde niet bepalen."""
    recs = [r for r in recs if isinstance(r, dict)]
    return [r for _, r in sorted(enumerate(recs), key=lambda x: (rec_no(x[1]), x[0]))]


def new_since(recs: list, keys: list, state: dict):
    """Geeft de nieuwe records sinds het bewaarde doorloopunt, of None als dat onbepaalbaar is.

    state = {"tail": [sleutels van de laatst verwerkte records], "t": tijdstip laatste record,
             "len": lengte van de buffer toen (optioneel)}.
    """
    tail = state["tail"]
    if not tail:
        return recs  # buffer was leeg bij het vorige doorloopunt: alles is nieuw
    n = len(tail)
    # 1) Exacte positie: zolang er niets uit de buffer geschoven is, staat de vorige reeks nog op
    #    precies dezelfde plaats. Dat maakt ook identieke records in een kleine buffer eenduidig.
    prev_len = state.get("len")
    if isinstance(prev_len, int) and n <= prev_len <= len(keys) and keys[prev_len - n:prev_len] == tail:
        return recs[prev_len:]
    # 2) Zoeken: de buffer is opgeschoven, dus de reeks staat nu eerder (recentste voorkomen).
    for i in range(len(keys) - n, -1, -1):
        if keys[i:i + n] == tail:
            return recs[i + n:]
    # 3) De vorige reeks is helemaal uit de buffer geschoven (of de buffer werd gewist).
    newer = [r for r in recs if rec_time(r) > state["t"]]
    return newer or None


def rec_room(r: dict) -> str:
    """Nummer van de binnenpost die de deur opende (enkel bij methode binnenpost, anders leeg)."""
    try:
        if int(r.get("Method")) != METHOD_INDOOR:
            return ""
    except (TypeError, ValueError):
        return ""
    return str(r.get("RoomNumber") or "").strip()


def who(name, method, opened: bool, room: str = "") -> str:
    """Naam voor een toegang; zonder naam een label volgens de methode (zelfde regels als het archief)."""
    name = (name or "").strip()
    if name and name != "?":
        return name
    try:
        m = int(method)
    except (TypeError, ValueError):
        m = None
    if m == METHOD_INDOOR:
        return f"Binnenpost {room}".strip()
    if m == 5:
        return "Exitknop"
    if m == 20:
        return "Ongeldige invoer"
    if m in (1, 2, 3):
        return "Onbekende badge"
    if m == 0 and not opened:
        return "Foute code"
    return "Onbekende code"


def method_label(m, room: str = "") -> str:
    try:
        label = METHODS.get(int(m), f"onbekend ({m})")
        if int(m) == METHOD_INDOOR and room:
            label = f"{label} {room}"
        return label
    except (TypeError, ValueError):
        return f"onbekend ({m})"


def clock_mode(clock: dict, utc_now: datetime, local_offset: int):
    """Hoe de CreateTime van dit toestel omgerekend moet worden.

    Het toestel bewaart CreateTime als zijn eigen kloktijd, gecodeerd alsof het UTC is (vastgesteld
    september 2026: toegang om 19:10:51 Brussel, klok van het toestel 18:10:51, CreateTime 18:10:51Z).
    Geeft {"zone": True} als het toestel zomertijd volgt en op dezelfde tijd staat als Home Assistant
    (dan is CreateTime de lokale tijd van Home Assistant), anders {"offset": s} met het vaste verschil
    tussen de klok van het toestel en UTC, afgerond op een kwartier. None als de klok onleesbaar is."""
    try:
        wall = datetime.strptime(str(clock["time"]["time"]).strip(), "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return None
    now = utc_now.astimezone(timezone.utc).replace(tzinfo=None)
    offset = int(round((wall - now).total_seconds() / 900.0)) * 900
    dst = bool(((clock.get("locales") or {}).get("table") or {}).get("DSTEnable"))
    if dst and offset == local_offset:
        return {"zone": True}
    return {"offset": offset}
