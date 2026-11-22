"""Scheduled collections and recorded visits, served from local history."""
from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent

from .const import PORTAL_ZONE
from .data import timestamp
from .entity import PlaceEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(WasteCalendar(entry.runtime_data, place, planned)
                       for place in entry.data["places"] for planned in (True, False))


class WasteCalendar(PlaceEntity, CalendarEntity):
    def __init__(self, coordinator, place, planned):
        self.planned = planned
        super().__init__(coordinator, place, "schedule" if planned else "history",
                         "Plán svozů" if planned else "Historie svozů")

    @property
    def extra_state_attributes(self):
        return {"datum_synchronizace": self.data["updated"], "ulozena_data": self.coordinator.using_cache,
                "revize_synchronizace": self.coordinator.revision}

    def _events(self):
        if self.planned:
            for row in self.data["schedule"]:
                day = date.fromisoformat(row["day"])
                yield CalendarEvent(start=day, end=day + timedelta(days=1), summary=row["name"],
                                    uid=f"{self.place}-{row['route']}-{day}")
            return
        charges = {k: v for month in self.data["months"].values() for k, v in month["charges"].items()}
        for key, row in self.data["history"].items():
            start = timestamp(row["date"])
            details = [f"{value:g} {unit}" for value, unit in ((row["kg"], "kg"), (row["litres"], "l"))
                       if value is not None]
            details.append(f"{charges[key]:g} {self.data['currency'] or ''}" if key in charges else "Cena neuvedena")
            yield CalendarEvent(start=start, end=start + timedelta(minutes=1), summary=row["name"],
                                description=" · ".join(details), uid=f"{self.place}-{key}")

    @property
    def event(self):
        now = datetime.now(PORTAL_ZONE)
        return min((e for e in self._events() if e.end_datetime_local > now),
                   key=lambda e: e.start_datetime_local, default=None)

    async def async_get_events(self, hass, start_date, end_date):
        return sorted((e for e in self._events() if e.start_datetime_local < end_date and e.end_datetime_local > start_date),
                      key=lambda e: e.start_datetime_local)
