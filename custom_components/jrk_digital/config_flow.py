"""Account login and collection point selection."""
import probatio as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector

from .api import CitizenPortal, LoginError, PortalError
from .const import DOMAIN


class JRKConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            account = user_input[CONF_USERNAME].strip()
            client = CitizenPortal(account, user_input[CONF_PASSWORD])
            try:
                self.places = await self.hass.async_add_executor_job(client.subjects)
            except LoginError:
                errors["base"] = "invalid_auth"
            except (PortalError, OSError, KeyError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                if not self.places:
                    return self.async_abort(reason="no_places")
                await self.async_set_unique_id(account.casefold())
                if self.source == config_entries.SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=user_input)
                self._abort_if_unique_id_configured()
                self.places = self._available_places(self.places)
                if not self.places:
                    return self.async_abort(reason="already_configured")
                self.credentials = {CONF_USERNAME: account, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                if len(self.places) == 1:
                    return await self.async_step_places({"places": list(self.places)})
                return await self.async_step_places()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }), errors=errors)

    def _available_places(self, places, current=None):
        taken = {place for entry in self._async_current_entries() if entry is not current
                 for place in entry.data["places"]}
        return {key: name for key, name in places.items() if key not in taken}

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        client = CitizenPortal(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        try:
            places = await self.hass.async_add_executor_job(client.subjects)
        except LoginError:
            return self.async_abort(reason="invalid_auth")
        except (PortalError, OSError, KeyError, ValueError):
            return self.async_abort(reason="cannot_connect")
        self.places = self._available_places(places, entry)
        if not self.places:
            return self.async_abort(reason="no_places")
        return await self.async_step_places(user_input)

    async def async_step_places(self, user_input=None):
        reconfigure = self.source == config_entries.SOURCE_RECONFIGURE
        entry = self._get_reconfigure_entry() if reconfigure else None
        self.places = self._available_places(self.places, entry)
        if user_input and user_input.get("places") and set(user_input["places"]) <= self.places.keys():
            chosen = {key: self.places[key] for key in user_input["places"]}
            if reconfigure:
                return self.async_update_reload_and_abort(entry, data_updates={"places": chosen})
            return self.async_create_entry(title="JRK Digital", data={**self.credentials, "places": chosen})
        options = [{"value": key, "label": name} for key, name in self.places.items()]
        return self.async_show_form(step_id="places", data_schema=vol.Schema({
            vol.Required("places", default=[key for key in self.places if not entry or key in entry.data["places"]]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, multiple=True))
        }), errors={"base": "select_places"} if user_input is not None else {})

    async def async_step_reauth(self, entry_data):
        return await self.async_step_user()
