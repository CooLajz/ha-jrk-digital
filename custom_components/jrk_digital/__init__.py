"""JRK Digital citizen portal for Home Assistant."""
from datetime import date, datetime, time, timedelta
import logging

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CitizenPortal, LoginError, PortalError
from .card import async_register_card
from .const import DOMAIN, PLATFORMS, PORTAL_ZONE
from .data import MATERIAL_LABELS, fetch_place, monthly_totals

_LOGGER = logging.getLogger(__name__)
NORMAL_INTERVAL = timedelta(days=1)
RETRY_INTERVAL = timedelta(minutes=15)


class JRKCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=NORMAL_INTERVAL)
        self.entry = entry
        self.client = CitizenPortal(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.snapshot = {}
        self.using_cache = False
        self.revision = 0

    def _fetch(self, today):
        profile = self.client.get("/user/user-info")
        country = profile["municipality"]["countryCode"]
        currency = {"CZ": "CZK", "SK": "EUR"}.get(str(country).upper())
        if currency is None:
            currency = {"cs": "CZK", "sk": "EUR"}.get(profile["municipality"]["countryCulture"][:2])
        places = {}
        for place in self.entry.data["places"]:
            previous = self.snapshot.get(place, {})
            places[place] = fetch_place(self.client, place, previous, today)
            places[place]["currency"] = currency
        return places

    async def async_restore(self):
        """Expose the selected places immediately when a complete snapshot exists."""
        saved = await self.store.async_load() or {}
        selected = self.entry.data["places"]
        self.snapshot = {key: saved[key] for key in selected if key in saved}
        if self.snapshot.keys() != selected.keys():
            return False
        today = datetime.now(PORTAL_ZONE).date().isoformat()
        self.using_cache = any(data["updated"] != today for data in self.snapshot.values())
        self._import(self.snapshot)
        self.async_set_updated_data(self.snapshot)
        return True

    async def _async_update_data(self):
        today = datetime.now(PORTAL_ZONE).date()
        try:
            fresh = await self.hass.async_add_executor_job(self._fetch, today)
        except LoginError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except (PortalError, KeyError, TypeError, ValueError, OSError) as exc:
            if self.snapshot.keys() == self.entry.data["places"].keys():
                if not self.using_cache:
                    _LOGGER.warning("Portál JRK je nedostupný, používají se uložené údaje")
                self.using_cache = True
                self.update_interval = RETRY_INTERVAL
                return self.snapshot
            raise UpdateFailed("Načtení dat JRK se nezdařilo.") from exc
        self._import(fresh)
        await self.store.async_save(fresh)
        self.update_interval = NORMAL_INTERVAL
        self.using_cache = False
        self.revision += 1
        self.snapshot = fresh
        return fresh

    def _import(self, snapshot):
        for place, data in snapshot.items():
            totals = monthly_totals(data)
            labels = {"collections": "Počet svozů", "paid_collections": "Počet placených svozů",
                      "unpaid_collections": "Počet svozů bez poplatku",
                      "cost": "Cena placených svozů",
                      **{key: f"Počet svozů – {name}" for key, name in MATERIAL_LABELS.items()}}
            for key, label in labels.items():
                total = 0
                rows = []
                for month, values in sorted(totals.items()):
                    start = datetime.combine(date.fromisoformat(month + "-01"), time.min, PORTAL_ZONE)
                    # Recorder looks back one hour for the opening sum of a calendar period.
                    rows.append({"start": start - timedelta(hours=1), "state": total, "sum": total})
                    total = round(total + values[key], 6)
                    rows.append({"start": start, "state": total, "sum": total})
                async_add_external_statistics(self.hass, {
                    "statistic_id": f"{DOMAIN}:misto_{place}_{key}", "source": DOMAIN,
                    "name": f"{self.entry.data['places'][place]} – {label}", "has_sum": True,
                    "mean_type": StatisticMeanType.NONE, "unit_class": None,
                    "unit_of_measurement": data["currency"] if key == "cost" else "svoz",
                }, rows)


async def async_setup(hass, config):
    await async_register_card(hass)
    return True


async def async_setup_entry(hass, entry):
    coordinator = JRKCoordinator(hass, entry)
    if not await coordinator.async_restore():
        await coordinator.async_config_entry_first_refresh()
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if any(domain == DOMAIN and place not in entry.data["places"] for domain, place in device.identifiers):
            devices.async_remove_device(device.id)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if coordinator.using_cache:
        entry.async_create_background_task(hass, coordinator.async_request_refresh(), "jrk_refresh_cache")
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass, entry):
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
