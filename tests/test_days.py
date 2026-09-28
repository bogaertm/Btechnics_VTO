"""Historiek per dag: tellingen in de tijdzone van Home Assistant, labels tellen niet als persoon."""
from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.btechnics_vto.archive import is_label


def test_is_label():
    assert is_label("Binnenpost 9902") and is_label("Foute code") and is_label("Op afstand")
    assert not is_label("Merel") and not is_label("Btechnics - Matthias")
