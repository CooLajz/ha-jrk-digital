"""Offline startup, recovery and account reconfiguration."""
import asyncio
from datetime import UTC, date, datetime, timedelta
from threading import Event
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.jrk_digital import JRKCoordinator, NORMAL_INTERVAL, RETRY_INTERVAL
from custom_components.jrk_digital.api import LoginError, PortalError, PortalHTTPError
from custom_components.jrk_digital.calendar import WasteCalendar
from custom_components.jrk_digital.sensor import Reading, SyncDiagnostic
from custom_components.jrk_digital.config_flow import JRKConfigFlow


class TestCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = Mock(entry_id="example", data={"username": "user", "password": "test",
                                                    "places": {"a": "Místo A"}})
        self.hass = HomeAssistant("/tmp")
        self.coordinator = JRKCoordinator(self.hass, self.entry)
        self.coordinator.store = AsyncMock()
        self.coordinator.poll_store = AsyncMock()
        self.coordinator.poll_store.async_load.return_value = {}
        self.coordinator._import = Mock()
        self.saved = {"a": {"updated": (date.today() - timedelta(days=2)).isoformat(),
                            "currency": "CZK", "annual_fee": 125, "history": {}, "months": {},
                            "schedule": [{"day": "2099-01-01", "name": "Papír", "route": "1"}]}}

    async def restart(self, coordinator):
        new = JRKCoordinator(self.hass, self.entry)
        new.store = coordinator.store
        new.poll_store = coordinator.poll_store
        new.poll_store.async_load.return_value = new.poll_store.async_save.await_args.args[0]
        new._import = Mock()
        await new.async_restore()
        return new

    async def test_stale_boot_outage_and_recovery(self):
        c = self.coordinator
        c.store.async_load.return_value = {**self.saved, "removed": {}}
        await c.async_restore()
        self.assertEqual(c.data, self.saved)
        self.assertTrue(c.using_cache)
        with patch.object(c, "_fetch", side_effect=PortalError("Spojení přerušeno.")) as fetch:
            await c.async_refresh()
            await c.async_refresh()
            fetch.assert_called_once()
        c.store.async_save.assert_not_awaited()
        c = await self.restart(c)
        deadline = c.next_update
        with patch.object(c, "_fetch") as fetch:
            await c.async_refresh()
            fetch.assert_not_called()
        self.assertEqual(c.next_update, deadline)
        reading = Reading(c, "a", "year_fee", "Poplatek")
        self.assertTrue(reading.available)
        self.assertEqual(reading.native_value, 125)
        calendar = WasteCalendar(c, "a", True)
        self.assertTrue(calendar.available)
        self.assertEqual(next(calendar._events()).summary, "Papír")
        diagnostic = SyncDiagnostic(c, "a", "error", "Chyba")
        self.assertEqual(diagnostic.native_value, "Spojení přerušeno.")
        c.next_update = datetime.now(UTC) - RETRY_INTERVAL
        with patch.object(c, "_fetch", return_value=dict(self.saved["a"])) as fetch:
            await c.async_refresh()
            await c.async_refresh()
            fetch.assert_called_once()
        self.assertFalse(c.using_cache)
        self.assertEqual(diagnostic.native_value, "Bez chyby")
        c.store.async_load.return_value = c.snapshot
        restored = await self.restart(c)
        self.assertEqual(restored.next_update, c.next_update)
        self.assertEqual(restored.last_attempt, c.last_attempt)
        self.assertEqual(SyncDiagnostic(restored, "a", "last_success", "Stažení").native_value,
                         datetime.fromisoformat(c.snapshot["a"]["downloaded_at"]))
        self.assertGreater(c.next_update, datetime.now(UTC) + NORMAL_INTERVAL - timedelta(seconds=2))

    async def test_partial_download_and_server_deadline_survive_restart(self):
        self.entry.data["places"]["b"] = "Místo B"
        c = self.coordinator
        c.store.async_load.return_value = {}
        await c.async_restore()
        err = PortalHTTPError(Mock(status=429, headers={"Retry-After": "7200"}))
        with patch.object(c, "_fetch", side_effect=[self.saved["a"], err]):
            await c.async_refresh()
        self.assertEqual(set(c.data), {"a"})
        self.assertEqual(c.next_update, err.retry_at)
        c.store.async_load.return_value = c.data
        c = await self.restart(c)
        with patch.object(c, "_fetch") as fetch:
            await c.async_refresh()
            fetch.assert_not_called()
            c.next_update = datetime.now(UTC)
            fetch.return_value = dict(self.saved["a"])
            await c.async_refresh()
            self.assertEqual(fetch.call_args.args[0], "b")
            fetch.assert_called_once()
        self.assertEqual(set(c.data), {"a", "b"})

    async def test_concurrent_refresh_keeps_entity_state_and_downloads_once(self):
        c = self.coordinator
        c.store.async_load.return_value = self.saved
        await c.async_restore()
        reading = Reading(c, "a", "year_fee", "Poplatek")
        started, finish = asyncio.Event(), Event()

        def fetch(place, today):
            self.hass.loop.call_soon_threadsafe(started.set)
            if not finish.wait(timeout=5):
                raise AssertionError("Download was not released")
            return {**self.saved[place], "annual_fee": 250}

        with patch.object(c, "_fetch", side_effect=fetch) as load:
            first = asyncio.create_task(c.async_refresh())
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                second = asyncio.create_task(c.async_refresh())
                self.assertTrue(reading.available)
                self.assertEqual(reading.native_value, 125)
                interrupted = await self.restart(c)
                with patch.object(interrupted, "_fetch") as repeated:
                    await interrupted.async_refresh()
                    repeated.assert_not_called()
            finally:
                finish.set()
            await asyncio.gather(first, second)
            load.assert_called_once()
        self.assertEqual(reading.native_value, 250)

    async def test_auth_stops_even_after_restart(self):
        c = self.coordinator
        c.store.async_load.return_value = self.saved
        await c.async_restore()
        with patch.object(c, "_fetch", side_effect=LoginError("Přihlášení odmítnuto.")) as fetch:
            await c.async_refresh()
            await c.async_refresh()
            fetch.assert_called_once()
        self.entry.async_start_reauth.assert_called_once()
        c = await self.restart(c)
        self.assertIsNone(c.next_update)
        self.assertIsNone(c.update_interval)
        self.assertEqual(c.data, self.saved)
        with patch.object(c, "_fetch") as fetch:
            await c.async_refresh()
            fetch.assert_not_called()

    async def test_cold_failure_backoff_is_bounded(self):
        c = self.coordinator
        c.store.async_load.return_value = {}
        await c.async_restore()
        with patch.object(c, "_fetch", side_effect=PortalError("Výpadek")) as fetch:
            for minutes in (15, 30, 60, 120, 240, 480, 960, 1440, 1440):
                c.next_update = datetime.now(UTC)
                await c.async_refresh()
                self.assertAlmostEqual(c.update_interval.total_seconds(), minutes * 60, delta=1)
                await c.async_refresh()
            self.assertEqual(fetch.call_count, 9)
        c = await self.restart(c)
        self.assertTrue(SyncDiagnostic(c, "a", "error", "Chyba").available)
        self.assertFalse(WasteCalendar(c, "a", True).available)


class TestPlaces(unittest.IsolatedAsyncioTestCase):
    async def test_change_selected_places(self):
        entry = Mock(data={"username": "user", "password": "test", "places": {"a": "Místo A"}})
        other = Mock(data={"places": {"c": "Místo C"}})
        flow = JRKConfigFlow()
        flow.context = {"source": config_entries.SOURCE_RECONFIGURE}
        flow.hass = HomeAssistant("/tmp")
        with (patch.object(flow, "_get_reconfigure_entry", return_value=entry),
              patch.object(flow, "_async_current_entries", return_value=[entry, other]),
              patch("custom_components.jrk_digital.config_flow.CitizenPortal.subjects",
                    return_value={"a": "Místo A", "b": "Místo B", "c": "Místo C"}),
              patch.object(flow, "async_show_form", side_effect=lambda **kwargs: kwargs),
              patch.object(flow, "async_update_reload_and_abort") as save):
            form = await flow.async_step_reconfigure()
            field, selector = next(iter(form["data_schema"].schema.items()))
            self.assertEqual(field.default(), ["a"])
            self.assertEqual([item["value"] for item in selector.config["options"]], ["a", "b"])
            for invalid in ([], ["c"]):
                self.assertTrue((await flow.async_step_places({"places": invalid}))["errors"])
            await flow.async_step_places({"places": ["b"]})
            save.assert_called_once_with(entry, data_updates={"places": {"b": "Místo B"}})
