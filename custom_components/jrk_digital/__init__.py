"""JRK Digital citizen portal for Home Assistant."""
from datetime import UTC, date, datetime, time, timedelta
import logging

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import CitizenPortal, LoginError, PortalError, PortalHTTPError
from .card import async_register_card
from .const import DOMAIN, PLATFORMS, PORTAL_ZONE
from .data import MATERIAL_LABELS, fetch_place, monthly_totals

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)
NORMAL_INTERVAL = timedelta(days=1)
RETRY_INTERVAL = timedelta(minutes=15)


def poll_store(hass, entry):
    return Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.poll")


class JRKCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=NORMAL_INTERVAL)
        self.entry = entry
        self.client = CitizenPortal(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.snapshot = {}
        self.using_cache = False
        self.revision = 0
        self.imported_totals = {}

        self.poll_store = poll_store(hass, entry)
        self.last_attempt = None
        self.next_update = None
        self.error = None
        self.failures = 0
        self.auth_required = False
        self.completed = False
        self.currency = None

    def _fetch(self, place, today):
        if self.currency is None:
            profile = self.client.get("/user/user-info")["municipality"]
            self.currency = {"CZ": "CZK", "SK": "EUR"}.get(str(profile.get("countryCode")).upper())
            if self.currency is None:
                self.currency = {"cs": "CZK", "sk": "EUR"}.get(profile["countryCulture"][:2])
        fresh = fetch_place(self.client, place, self.snapshot.get(place, {}), today)
        fresh["currency"] = self.currency
        return fresh

    def _schedule(self, deadline):
        self.next_update = deadline
        self.update_interval = (max(timedelta(seconds=1), deadline - datetime.now(UTC))
                                if deadline else None)

    async def _save_poll(self):
        await self.poll_store.async_save({
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
            "next_update": self.next_update.isoformat() if self.next_update else None,
            "error": self.error, "failures": self.failures, "auth_required": self.auth_required,
            "completed": self.completed,
        })

    async def async_restore(self):
        saved = await self.store.async_load() or {}
        self.snapshot = {key: saved[key] for key in self.entry.data["places"] if key in saved}
        poll = await self.poll_store.async_load() or {}
        self.last_attempt = datetime.fromisoformat(poll["last_attempt"]) if poll.get("last_attempt") else None
        self.failures = poll.get("failures", 0)
        self.error = poll.get("error")
        self.auth_required = poll.get("auth_required", False)
        self.completed = poll.get("completed", False)
        deadline = datetime.fromisoformat(poll["next_update"]) if poll.get("next_update") else None
        now = datetime.now(UTC)
        today = now.astimezone(PORTAL_ZONE).date()
        if self.auth_required:
            self._schedule(None)
            self.entry.async_start_reauth(self.hass)
        else:
            complete = self.snapshot.keys() == self.entry.data["places"].keys()
            if not poll and complete and all(row["updated"] == today.isoformat() for row in self.snapshot.values()):
                # A date-only snapshot cannot provide an exact download time.
                deadline = datetime.combine(today + timedelta(days=1), time.min, PORTAL_ZONE)
            if not complete and self.completed:
                deadline = None
            self._schedule(deadline or now)
        self.using_cache = bool(self.error or (
            not self.completed and any(row["updated"] != today.isoformat() for row in self.snapshot.values())
        ) or (self.snapshot and self.next_update and self.next_update <= now))
        self._import(self.snapshot)
        self.async_set_updated_data(self.snapshot)

    async def _async_update_data(self):
        """HA serializes all refresh paths; recheck the persisted deadline inside that lock."""
        now = datetime.now(UTC)
        if self.auth_required or (self.next_update and now < self.next_update):
            self._schedule(self.next_update)
            return self.snapshot

        due = []
        for place in self.entry.data["places"]:
            stamp = self.snapshot.get(place, {}).get("downloaded_at")
            if not stamp or datetime.fromisoformat(stamp) + NORMAL_INTERVAL <= now:
                due.append(place)
        if not due:
            latest = max(datetime.fromisoformat(row["downloaded_at"]) for row in self.snapshot.values())
            self._schedule(latest + NORMAL_INTERVAL)
            self.using_cache = False
            self.error = None
            self.completed = True
            self.failures = 0
            await self._save_poll()
            return self.snapshot

        self.completed = False
        self.last_attempt = now
        # Reserve a retry interval before network I/O, including across an interrupted start.
        self._schedule(now + RETRY_INTERVAL)
        await self._save_poll()
        try:
            for place in due:
                fresh = await self.hass.async_add_executor_job(self._fetch, place, now.astimezone(PORTAL_ZONE).date())
                fresh["downloaded_at"] = datetime.now(UTC).isoformat()
                self._import({place: fresh})
                snapshot = {**self.snapshot, place: fresh}
                await self.store.async_save(snapshot)
                self.snapshot = snapshot
                self.revision += 1
        except LoginError as exc:
            self.error = str(exc)
            self.auth_required = True
            self._schedule(None)
            self.entry.async_start_reauth(self.hass)
        except (PortalError, KeyError, TypeError, ValueError, OSError, AttributeError, ArithmeticError) as exc:
            self.failures += 1
            self.error = str(exc) if isinstance(exc, PortalError) else "Data JRK se nepodařilo načíst nebo uložit."
            delay = min(NORMAL_INTERVAL, RETRY_INTERVAL * 2 ** min(self.failures - 1, 7))
            deadline = datetime.now(UTC) + delay
            if isinstance(exc, PortalHTTPError) and exc.retry_at:
                deadline = max(deadline, exc.retry_at)
            self._schedule(deadline)
            if self.failures == 1:
                _LOGGER.warning("Aktualizace JRK se nezdařila: %s", self.error)
        else:
            self.completed = True
            self.failures = 0
            self.error = None
            self._schedule(datetime.now(UTC) + NORMAL_INTERVAL)
        self.using_cache = bool(self.error)
        await self._save_poll()
        return self.snapshot

    def _import(self, snapshot):
        for place, data in snapshot.items():
            totals = monthly_totals(data)
            # Replay on startup; within a running instance submit only changed series.
            previous, currency = self.imported_totals.get(place, ({}, None))
            labels = {"collections": "Počet svozů", "paid_collections": "Počet placených svozů",
                      "unpaid_collections": "Počet svozů bez poplatku",
                      "cost": "Cena placených svozů",
                      **{key: f"Počet svozů – {name}" for key, name in MATERIAL_LABELS.items()}}
            for key, label in labels.items():
                if (previous.keys() == totals.keys() and (key != "cost" or currency == data["currency"])
                        and all(previous[month][key] == values[key] for month, values in totals.items())):
                    continue
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
            self.imported_totals[place] = totals, data["currency"]


async def async_setup(hass, config):
    await async_register_card(hass)
    return True


async def async_setup_entry(hass, entry):
    coordinator = JRKCoordinator(hass, entry)
    await coordinator.async_restore()
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if any(domain == DOMAIN and place not in entry.data["places"] for domain, place in device.identifiers):
            devices.async_remove_device(device.id)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if coordinator.next_update and coordinator.next_update <= datetime.now(UTC):
        entry.async_create_background_task(hass, coordinator.async_request_refresh(), "jrk_refresh_cache")
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass, entry):
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
    await poll_store(hass, entry).async_remove()
