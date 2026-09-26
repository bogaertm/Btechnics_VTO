"""De dashboardkaarten worden automatisch geserveerd en geladen, zonder manuele resource."""
from homeassistant.setup import async_setup_component

from .test_integration import devices, setup_two_entries  # noqa: F401


async def test_kaarten_automatisch_beschikbaar(hass, devices, hass_client):
    assert await async_setup_component(hass, "frontend", {})
    await setup_two_entries(hass)
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
    urls = list(hass.data[DATA_EXTRA_MODULE_URL].urls)
    ours = [u for u in urls if u.startswith("/btechnics_vto_static/btechnics-vto-cards.js?v=")]
    assert len(ours) == 1, urls
    client = await hass_client()
    r = await client.get(ours[0])
    assert r.status == 200
    body = await r.text()
    for card in ("btechnics-vto-overzicht", "btechnics-vto-toegang", "btechnics-vto-codes"):
        assert f'"{card}"' in body and "window.customElements.define(name" in body
    # geen emoji in de kaarten
    import re
    assert not re.search("[\U0001F300-\U0001FAFF☀-➿]", body)


async def test_kaarten_ook_als_dashboardresource(hass, devices):
    """Een oude kopie van de pagina (service worker) mag de kaarten niet laten wegvallen."""
    assert await async_setup_component(hass, "frontend", {})
    await setup_two_entries(hass)
    await hass.async_block_till_done()
    res = hass.data["lovelace"].resources
    mine = [r for r in res.async_items() if r["url"].startswith("/btechnics_vto_static/btechnics-vto-cards.js")]
    assert len(mine) == 1 and mine[0]["type"] == "module" and "?v=" in mine[0]["url"]
    # tweede keer opstarten: geen dubbele resource
    from custom_components.btechnics_vto import _async_ensure_resource
    await _async_ensure_resource(hass, "9.9.9")
    mine = [r for r in res.async_items() if r["url"].startswith("/btechnics_vto_static/")]
    assert [r["url"] for r in mine] == ["/btechnics_vto_static/btechnics-vto-cards.js?v=9.9.9"]
