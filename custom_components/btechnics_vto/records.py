"""Gedeelde functies voor logrecords van de VTO (gebruikt door coordinator en archief).

Het logboek van het toestel is een ringbuffer van max 1000 records in volgorde van registratie.
RecNo is positioneel en dus geen identiteit: records worden herkend op inhoud en positie.
"""
from .const import METHODS


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


def method_label(m) -> str:
    try:
        return METHODS.get(int(m), f"onbekend ({m})")
    except (TypeError, ValueError):
        return f"onbekend ({m})"
