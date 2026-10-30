"""Config flow: deuren één voor één toevoegen, telkens met logintest."""
import re

import voluptuous as vol
from homeassistant import config_entries

from .api import VTOClient, VTOError
from .const import CONF_DOORS, CONF_HOST, CONF_HTTPS, CONF_PASSWORD, CONF_USERNAME, DOMAIN

DOOR_SCHEMA = vol.Schema({
    vol.Required("name"): str,
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_HTTPS, default=False): bool,
    vol.Required(CONF_USERNAME, default="admin"): str,
    vol.Required(CONF_PASSWORD): str,
})


def _door_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _test(client: VTOClient) -> dict:
    client.login()
    try:
        return client.info()
    finally:
        client.logout()


class BtechnicsVTOConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._doors = []

    def _taken(self):
        """Deur-ids en IP-adressen die al gebruikt worden (in alle bestaande entries en in deze flow)."""
        doors = [d for e in self._async_current_entries(include_ignore=False) for d in e.data.get(CONF_DOORS, [])] + self._doors
        return {d["id"] for d in doors}, {str(d[CONF_HOST]).strip().lower() for d in doors}

    async def async_step_user(self, user_input=None):
        errors = {}
        placeholders = {"reason": ""}
        if user_input is not None:
            user_input = {**user_input, "name": user_input["name"].strip(), CONF_HOST: user_input[CONF_HOST].strip()}
            ids, hosts = self._taken()
            if not user_input["name"] or not _door_id(user_input["name"]):
                errors["name"] = "invalid_name"
            elif _door_id(user_input["name"]) in ids:
                errors["name"] = "name_exists"
            elif user_input[CONF_HOST].lower() in hosts:
                errors[CONF_HOST] = "host_exists"
        if user_input is not None and not errors:
            client = VTOClient(user_input[CONF_HOST], user_input[CONF_HTTPS], user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            try:
                info = await self.hass.async_add_executor_job(_test, client)
            except Exception as e:  # noqa: BLE001
                errors["base"] = "cannot_connect"
                placeholders["reason"] = f"{type(e).__name__}: {e}"[:300]
            else:
                d = dict(user_input)
                d["id"] = _door_id(user_input["name"])
                d["info"] = info
                self._doors.append(d)
                return await self.async_step_more()
        schema = DOOR_SCHEMA
        if user_input is not None:
            user_input = {k: v for k, v in user_input.items() if k != CONF_PASSWORD}
            schema = self.add_suggested_values_to_schema(DOOR_SCHEMA, user_input)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors, description_placeholders=placeholders)

    async def async_step_more(self, user_input=None):
        if user_input is not None:
            if user_input["add_another"]:
                return await self.async_step_user()
            return self.async_create_entry(title=", ".join(d["name"] for d in self._doors), data={CONF_DOORS: self._doors})
        names = ", ".join(d["name"] for d in self._doors)
        return self.async_show_form(
            step_id="more",
            data_schema=vol.Schema({vol.Required("add_another", default=False): bool}),
            description_placeholders={"doors": names},
        )
