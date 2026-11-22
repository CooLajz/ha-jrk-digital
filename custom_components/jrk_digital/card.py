"""Serve the card and expose the synchronized records for a single history calendar."""
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

import probatio as vol
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN
from .data import MATERIAL_LABELS, material_key, money, timestamp


def year_report(place, year):
    """Return read-only display data, omitting account and collection point identifiers."""
    current = int(place["updated"][:4])
    years = {current}
    fees = defaultdict(list)
    months = {}
    for month, values in place["months"].items():
        if values["running"] or values["charges"]:
            years.add(int(month[:4]))
        for record, charge in values["charges"].items():
            fees[record].append(charge)
        if int(month[:4]) == year:
            months[month] = {"cost": money(values["charges"].values()), "running": values["running"]}
    rows = []
    materials = set()
    for record, row in place["history"].items():
        if row["yard"]:
            continue
        local = timestamp(row["date"])
        record_year = local.year
        material = MATERIAL_LABELS.get(material_key(row)) or row.get("waste_name") or row["name"] or "Neurčeno"
        materials.add(material)
        years.add(record_year)
        if record_year == year:
            rows.append({"date": local.isoformat(), "bin": row["name"], "material": material,
                         "kg": row["kg"], "volume": row["litres"],
                         "cost": money(fees[record]) if record in fees else None})
    return {"year": year, "years": sorted(years, reverse=True), "currency": place["currency"],
            "materials": sorted(materials), "updated": place["updated"], "months": months,
            "rows": sorted(rows, key=lambda r: timestamp(r["date"]).timestamp(), reverse=True)}


@websocket_api.websocket_command({
    vol.Required("type"): "jrk_digital/report",
    vol.Required("entity_id"): cv.entity_id,
    vol.Optional("year"): vol.All(int, vol.Range(min=1900, max=9999)),
})
@callback
def ws_report(hass, connection, msg):
    entity_id = msg["entity_id"]
    if not connection.user.permissions.check_entity(entity_id, POLICY_READ):
        connection.send_error(msg["id"], "unauthorized", "K tomuto místu nemáte přístup.")
        return
    entity = er.async_get(hass).async_get(entity_id)
    if not entity or entity.platform != DOMAIN or not entity_id.startswith("calendar.") or not entity.unique_id.endswith("_history"):
        connection.send_error(msg["id"], "invalid_entity", "Vyberte kalendář historie svozů JRK.")
        return
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    place_id = entity.unique_id.removesuffix("_history")
    coordinator = getattr(entry, "runtime_data", None)
    snapshot = (coordinator.data or {}).get(place_id) if coordinator else None
    if not snapshot:
        connection.send_error(msg["id"], "not_ready", "Údaje o svozech nejsou dostupné.")
        return
    result = year_report(snapshot, msg.get("year", int(snapshot["updated"][:4])))
    result["cached"] = coordinator.using_cache
    result["place"] = entry.data["places"][place_id]
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "jrk_digital/places"})
@callback
def ws_places(hass, connection, msg):
    registry = er.async_get(hass)
    places = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            place_id, _, kind = entity.unique_id.rpartition("_")
            if kind == "history" and not entity.disabled_by and place_id in entry.data["places"]:
                if connection.user.permissions.check_entity(entity.entity_id, POLICY_READ):
                    places.append({"value": entity.entity_id, "label": entry.data["places"][place_id]})
    connection.send_result(msg["id"], places)


async def async_register_card(hass):
    path = Path(__file__).parent / "www" / "jrk-waste-card.js"
    url = "/jrk_digital/jrk-waste-card.js"
    content = await hass.async_add_executor_job(path.read_bytes)
    await hass.http.async_register_static_paths([StaticPathConfig(url, str(path), False)])
    add_extra_js_url(hass, f"{url}?v={sha256(content).hexdigest()[:12]}")
    websocket_api.async_register_command(hass, ws_report)
    websocket_api.async_register_command(hass, ws_places)
