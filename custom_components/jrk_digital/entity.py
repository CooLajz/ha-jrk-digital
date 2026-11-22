"""Entity identity belongs to the JRK collection point."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class PlaceEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, place, key, name):
        super().__init__(coordinator)
        self.place = place
        self._attr_unique_id = f"{place}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, place)}, manufacturer="JRK",
                                            model="Svozové místo", name=coordinator.entry.data["places"][place])

    @property
    def available(self):
        return self.place in (self.coordinator.data or {})

    @property
    def data(self):
        return self.coordinator.data[self.place]
