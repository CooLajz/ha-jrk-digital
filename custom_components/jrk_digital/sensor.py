"""Current collection point readings."""
from datetime import date, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .const import PORTAL_ZONE
from .data import money, paid_collection_ids, timestamp
from .entity import PlaceEntity

NAMES = {"next": "Příští svoz", "last": "Poslední svoz", "last_cost": "Cena posledního placeného svozu",
         "month_cost": "Cena svozů tento měsíc", "year_fee": "Průběžný roční poplatek",
         "month_count": "Počet svozů tento měsíc", "month_paid": "Počet placených svozů tento měsíc"}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    existing = set()
    async_add_entities(SyncDiagnostic(coordinator, place, key, name)
                       for place in entry.data["places"] for key, name in DIAGNOSTICS.items())

    @callback
    def discover():
        new = []
        for place, data in coordinator.data.items():
            keys = dict(NAMES)
            keys.update({f"weight_{c}": f"{r['name']} za rok" for c, r in data["weights"].items()})
            for key, name in keys.items():
                if (place, key) not in existing:
                    existing.add((place, key))
                    new.append(Reading(coordinator, place, key, name))
        if new:
            async_add_entities(new)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


DIAGNOSTICS = {"last_attempt": "Poslední pokus o synchronizaci", "last_success": "Poslední úspěšné stažení",
               "next_update": "Příští pokus o synchronizaci", "error": "Chyba synchronizace"}


class SyncDiagnostic(PlaceEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, place, key, name):
        super().__init__(coordinator, place, key, name)
        self.key = key
        if key != "error":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        if self.key == "last_success":
            stamp = self.coordinator.snapshot.get(self.place, {}).get("downloaded_at")
            return datetime.fromisoformat(stamp) if stamp else None
        if self.key == "error":
            return self.coordinator.error or "Bez chyby"
        return getattr(self.coordinator, self.key)


class Reading(PlaceEntity, SensorEntity):
    def __init__(self, coordinator, place, key, name):
        super().__init__(coordinator, place, key, name)
        self.key = key
        if key == "next":
            self._attr_device_class = SensorDeviceClass.DATE
        elif key == "last":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key.startswith("weight_"):
            self._attr_device_class = SensorDeviceClass.WEIGHT
            self._attr_native_unit_of_measurement = "kg"
        elif key in ("last_cost", "month_cost", "year_fee"):
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = self.data["currency"]

    def _row(self):
        if self.key == "next":
            today = datetime.now(PORTAL_ZONE).date().isoformat()
            return next((r for r in self.data["schedule"] if r["day"] >= today), None)
        charges = {k: v for m in self.data["months"].values() for k, v in m["charges"].items()}
        rows = (({**row, "cost": charges.get(key)}) for key, row in self.data["history"].items()
                if not row["yard"] and (self.key != "last_cost" or charges.get(key, 0) > 0))
        return max(rows, key=lambda r: timestamp(r["date"]).timestamp(), default=None)

    def _paid_count(self, period):
        paid = paid_collection_ids(self.data)
        return sum(1 for key, row in self.data["history"].items()
                   if key in paid and not row["yard"] and timestamp(row["date"]).date().isoformat().startswith(period))

    @property
    def native_value(self):
        if self.key.startswith("weight_"):
            return self.data["weights"].get(self.key[7:], {}).get("kg")
        if self.key == "year_fee":
            return self.data["annual_fee"]
        if self.key.startswith("month_"):
            month = self.data["updated"][:7]
            charges = self.data["months"].get(month, {}).get("charges", {})
            if self.key == "month_count":
                return sum(timestamp(r["date"]).date().isoformat().startswith(month) and not r["yard"] for r in self.data["history"].values())
            if self.key == "month_paid":
                return self._paid_count(month)
            return money(charges.values())
        row = self._row()
        if row is None:
            return None
        if self.key == "next":
            return date.fromisoformat(row["day"])
        return row["cost"] if self.key == "last_cost" else timestamp(row["date"])

    @property
    def extra_state_attributes(self):
        if self.key == "year_fee":
            return {"pocet_placenych_svozu": self._paid_count(self.data["updated"][:4])}
        if self.key not in ("next", "last", "last_cost") or not (row := self._row()):
            return None
        if self.key == "next":
            return {"druh_odpadu": row["name"]}
        return {"nadoba": row["name"], "datum": timestamp(row["date"]).isoformat(), "hmotnost_kg": row["kg"],
                "objem_l": row["litres"], "cena": row["cost"]}
