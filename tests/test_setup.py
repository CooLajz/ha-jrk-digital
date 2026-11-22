"""Offline startup, recovery and account reconfiguration."""
from datetime import date, timedelta
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.jrk_digital import JRKCoordinator, NORMAL_INTERVAL, RETRY_INTERVAL
from custom_components.jrk_digital.api import LoginError, PortalError
from custom_components.jrk_digital.config_flow import JRKConfigFlow


class TestCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = Mock(entry_id="example", data={"username": "user", "password": "test",
                                                    "places": {"a": "Místo A"}})
        self.hass = HomeAssistant("/tmp")
        self.coordinator = JRKCoordinator(self.hass, self.entry)
        self.coordinator.store = AsyncMock()
        self.coordinator._import = Mock()
        self.saved = {"a": {"updated": (date.today() - timedelta(days=2)).isoformat()}}

    async def test_stale_boot_outage_and_recovery(self):
        c = self.coordinator
        c.store.async_load.return_value = {**self.saved, "removed": {}}
        self.assertTrue(await c.async_restore())
        self.assertEqual(c.data, self.saved)
        self.assertTrue(c.using_cache)
        with patch.object(c, "_fetch", side_effect=PortalError):
            self.assertEqual(await c._async_update_data(), self.saved)
        c.store.async_save.assert_not_awaited()
        self.assertEqual(c.update_interval, RETRY_INTERVAL)
        fresh = {"a": {"updated": date.today().isoformat()}}
        with patch.object(c, "_fetch", return_value=fresh):
            for expected_revision in (1, 2):
                self.assertEqual(await c._async_update_data(), fresh)
                self.assertEqual(c.revision, expected_revision)
        self.assertFalse(c.using_cache)
        self.assertEqual(c.update_interval, NORMAL_INTERVAL)
        c.store.async_save.assert_awaited_with(fresh)

    async def test_new_place_and_invalid_login(self):
        self.entry.data["places"]["b"] = "Místo B"
        c = self.coordinator
        c.store.async_load.return_value = self.saved
        self.assertFalse(await c.async_restore())
        with patch.object(c, "_fetch", side_effect=PortalError), self.assertRaises(UpdateFailed):
            await c._async_update_data()
        with patch.object(c, "_fetch", side_effect=LoginError), self.assertRaises(ConfigEntryAuthFailed):
            await c._async_update_data()


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
