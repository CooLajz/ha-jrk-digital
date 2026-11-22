"""Collect a complete snapshot before replacing any saved history."""
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal
import re

from .api import PortalError
from .const import PORTAL_ZONE


MATERIAL_LABELS = {"plastic_collections": "Plasty", "paper_collections": "Papír", "mixed_collections": "SKO"}
MATERIAL_BY_NAME = {
    "plast": "plastic_collections", "plasty": "plastic_collections",
    "papír": "paper_collections", "papír a lepenka": "paper_collections",
    "papier": "paper_collections", "papier a lepenka": "paper_collections",
    "sko": "mixed_collections", "směsný komunální odpad": "mixed_collections",
    "zmesový komunálny odpad": "mixed_collections",
}


def timestamp(value):
    """Convert JRK collection instants to local time; naive processedTs values are UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(PORTAL_ZONE)


def material_key(row):
    label = row.get("waste_name")
    if not label:
        # Previously cached and retired bins may only have a name including capacity.
        label = re.sub(r"\s*\d+(?:[.,]\d+)?\s*(?:l|m³)\s*$", "", row.get("name") or "", flags=re.I)
    return MATERIAL_BY_NAME.get(" ".join(label.casefold().split()))


def money(values):
    return float(sum((Decimal(str(value)) for value in values), Decimal(0)))


def pages(client, path, params):
    first = client.get(path, dict(params, Page=1))
    total, count = first["total"], first["pageCount"]
    items = first["items"]
    if (not isinstance(items, list) or type(total) is not int or type(count) is not int
            or first.get("page", 1) != 1 or total < 0 or len(items) > total
            or (total == 0 and count not in (0, 1))
            or (total > 0 and not (items and 1 <= count <= min(total, 1000)))):
        raise PortalError("JRK vrátil neplatné údaje o stránkování.")
    items = list(items)
    for page in range(2, count + 1):
        if len(items) >= total:
            raise PortalError("Počet stránek JRK neodpovídá počtu záznamů.")
        result = client.get(path, dict(params, Page=page))
        batch = result["items"]
        if (result["page"] != page or result["total"] != total or result["pageCount"] != count
                or not isinstance(batch, list) or not batch):
            raise PortalError("Seznam se během načítání změnil.")
        items.extend(batch)
    if len(items) != first["total"]:
        raise PortalError("Portál neposkytl všechny stránky.")
    return items


def fetch_place(client, place, previous, today):
    base = f"/subject/{place}/"
    cutoff = today.replace(year=today.year - 1, day=min(today.day, monthrange(today.year - 1, today.month)[1]))
    if previous:
        # On return after a long outage, also cover the missed interval.
        cutoff = min(cutoff, date.fromisoformat(previous["updated"]))
    jan = date(today.year, 1, 1)
    if previous:
        start = cutoff
    else:
        periods = client.get(f"/catalog/{place}/reporting-period")["items"]
        start = min([jan, *(date.fromisoformat(p["start"][:10]) for p in periods)])
    end = today.isoformat()
    history = {key: item for key, item in previous.get("history", {}).items()
               if item["date"][:10] < start.isoformat()}
    params = {"Date.From": start.isoformat(), "Date.To": end}
    evidence = pages(client, base + "waste-evidence", params)
    bins = pages(client, base + "trash-bin", {}) if any(row.get("barcode") for row in evidence) else []
    waste_by_barcode = {b["barcode"]: b["wasteName"] for b in bins if b.get("barcode") and b.get("wasteName")}
    seen = set()
    for row in evidence:
        key = str(row["id"])
        if key in seen:
            raise PortalError("Portál vrátil duplicitní záznam v historii.")
        seen.add(key)
        history[key] = {"date": row["processedTs"], "name": row["binName"], "kg": row["weight"],
                        "waste_name": waste_by_barcode.get(row.get("barcode")),
                        "litres": row["volume"], "yard": row["isWasteCollectionYardWaste"]}
    month_key = start.isoformat()[:7]
    months = {k: v for k, v in previous.get("months", {}).items() if k < month_key}
    if start.day != 1 and month_key in previous.get("months", {}):
        old = previous["months"][month_key]
        dates = {k: d for k, d in old["dates"].items() if d < start.isoformat()}
        months[month_key] = {**old, "dates": dates, "charges": {k: old["charges"][k] for k in dates}}
    annual_fee = None
    for year in range(start.year, today.year + 1):
        lower = max(start, date(year, 1, 1))
        upper = min(today, date(year, 12, 31))
        report = client.get(base + "fee-monthly-view", {"Date.From": lower.isoformat(), "Date.To": upper.isoformat()})
        if year == today.year:
            annual_fee = report["finalFee"]
        for month in report["monthlyFees"] or []:
            key = month["month"][:7]
            if lower.isoformat()[:7] <= key <= upper.isoformat()[:7]:
                # The first month may straddle the rolling update window.
                old = previous.get("months", {}).get(key, {})
                dates = {k: d for k, d in old.get("dates", {}).items() if d < lower.isoformat()}
                charges = {k: old["charges"][k] for k in dates}
                for fee in month["fees"]:
                    if fee["recordId"] is not None and fee["distributedValue"] is not None:
                        ident = str(fee["recordId"])
                        processed = fee["processedTs"][:10]
                        if processed < lower.isoformat() and ident in dates:
                            continue
                        dates[ident] = processed
                        charges[ident] = money([charges.get(ident, 0), fee["distributedValue"]])
                months[key] = {"charges": charges, "dates": dates, "running": month["finalMonthlyFee"]}
    waste = client.get(base + "all-waste", {"Date.From": jan.isoformat(), "Date.To": end})
    schedule = pages(client, base + "waste-collection-schedule", {})
    return {"history": history, "months": months, "annual_fee": annual_fee, "updated": end,
            "first_month": previous.get("first_month", start.isoformat()[:7]),
            "weights": {str(r["wasteCode"]): {"name": r["wasteName"], "kg": r["weight"]} for r in waste["items"]},
            "schedule": sorted([{"day": date.fromisoformat(r["collectionDateTs"][:10]).isoformat(), "name": r["wasteName"],
                                 "route": str(r["wasteCollectionRouteId"])} for r in schedule],
                               key=lambda r: (r["day"], r["name"]))}


def paid_collection_ids(place):
    return {record_id for month in place["months"].values()
            for record_id, charge in month["charges"].items() if charge > 0}


def monthly_totals(place):
    """Include empty months so deletion/correction also overwrites old statistics."""
    totals = {}
    first = date.fromisoformat(place["first_month"] + "-01")
    last = date.fromisoformat(place["updated"])
    while first <= last:
        key = first.isoformat()[:7]
        charges = place["months"].get(key, {}).get("charges", {})
        totals[key] = {"collections": 0, "paid_collections": 0, "unpaid_collections": 0, "cost": money(charges.values()),
                       **dict.fromkeys(MATERIAL_LABELS, 0)}
        first = date.fromordinal(first.toordinal() + monthrange(first.year, first.month)[1])
    paid = paid_collection_ids(place)
    for record_id, row in place["history"].items():
        key = timestamp(row["date"]).date().isoformat()[:7]
        if not row["yard"] and key in totals:
            totals[key]["collections"] += 1
            category = "paid_collections" if record_id in paid else "unpaid_collections"
            totals[key][category] += 1
            material = material_key(row)
            if material:
                totals[key][material] += 1
    return totals
