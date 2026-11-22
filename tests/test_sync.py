"""Regression tests for fee reconciliation across the New Year."""
from datetime import date
import unittest

from custom_components.jrk_digital.data import fetch_place, monthly_totals


class Portal:
    """Synthetic JRK account covering two adjacent billing years."""

    def __init__(self):
        self.records = {"1": ("2025-01-02", 95), "2": ("2025-12-31", 96), "3": ("2026-01-02", 100)}

    def get(self, path, params=None):
        if path.endswith("/reporting-period"):
            return {"items": [{"start": "2025-01-01"}]}
        if path.endswith(("/waste-collection-schedule", "/trash-bin")):
            return {"items": [], "pageCount": 0, "total": 0}
        records = [(key, day, cost) for key, (day, cost) in self.records.items()
                   if params["Date.From"] <= day <= params["Date.To"]]
        if path.endswith("/waste-evidence"):
            items = [{"id": key, "processedTs": day + "T09:00:00", "binName": "SKO", "weight": 1,
                      "volume": 120, "isWasteCollectionYardWaste": False} for key, day, _ in records]
            return {"items": items, "pageCount": 1, "total": len(items)}
        if path.endswith("/fee-monthly-view"):
            monthly = {}
            for key, day, cost in records:
                month = monthly.setdefault(day[:7], {"month": day[:7] + "-01", "fees": [], "finalMonthlyFee": 0})
                month["fees"].append({"recordId": key, "processedTs": day, "distributedValue": cost})
                month["finalMonthlyFee"] += cost
            return {"finalFee": sum(r[2] for r in records), "monthlyFees": list(monthly.values())}
        if path.endswith("/all-waste"):
            return {"items": []}
        raise AssertionError(path)


class TestSync(unittest.TestCase):
    def test_correction_deletion_and_new_year(self):
        portal = Portal()
        snapshot = fetch_place(portal, "1", {}, date(2025, 12, 31))
        portal.records["2"] = ("2025-12-31", 80)
        refreshed = fetch_place(portal, "1", snapshot, date(2026, 1, 5))
        totals = monthly_totals(refreshed)
        self.assertEqual(totals["2025-01"]["cost"], 95)
        self.assertEqual(totals["2025-12"]["cost"], 80)
        self.assertEqual(totals["2026-01"]["cost"], 100)
        self.assertEqual(refreshed["annual_fee"], 100)
        del portal.records["2"]
        refreshed = fetch_place(portal, "1", refreshed, date(2026, 1, 6))
        self.assertEqual(monthly_totals(refreshed)["2025-12"]["collections"], 0)
        self.assertEqual(monthly_totals(refreshed)["2025-12"]["cost"], 0)
        self.assertEqual(fetch_place(portal, "1", refreshed, date(2026, 1, 6)), refreshed)

    def test_counts_are_disjoint_and_follow_collection_date(self):
        history = {key: {"date": "2026-07-10", "yard": key == "yard"}
                   for key in ("paid", "free", "zero", "yard")}
        snapshot = {"first_month": "2026-07", "updated": "2026-08-15", "history": history,
                    "months": {"2026-08": {"charges": {"paid": 96, "zero": 0, "yard": 20}}}}
        totals = monthly_totals(snapshot)
        self.assertEqual(totals["2026-07"], {"collections": 3, "paid_collections": 1,
                         "unpaid_collections": 2, "cost": 0, "plastic_collections": 0,
                         "paper_collections": 0, "mixed_collections": 0})
        self.assertEqual(totals["2026-08"]["paid_collections"], 0)
        self.assertEqual(totals["2026-08"]["cost"], 116)

    def test_material_counts_include_older_bins_and_exclude_yard(self):
        bins = [("PAPÍR A LEPENKA 240l", None, False), ("Papír 120 L", None, False),
                ("SKO 120l", None, False), ("Žlutá nádoba", "Plasty", False),
                ("SKO 120l", None, True), ("Plasty a kovy", None, False)]
        history = {str(i): {"date": "2026-07-10", "name": name, "waste_name": material, "yard": yard}
                   for i, (name, material, yard) in enumerate(bins)}
        july = monthly_totals({"first_month": "2026-07", "updated": "2026-08-01",
                               "history": history, "months": {}})["2026-07"]
        self.assertEqual((july["plastic_collections"], july["paper_collections"], july["mixed_collections"]), (1, 2, 1))
        self.assertEqual(july["collections"], 5)

    def test_utc_collection_times_use_local_clock_and_month(self):
        from custom_components.jrk_digital.data import timestamp

        self.assertEqual(timestamp("2026-10-15T05:09:31.343").strftime("%H:%M %z"), "07:09 +0200")
        self.assertEqual(timestamp("2026-11-12T06:10:40.63").strftime("%H:%M %z"), "07:10 +0100")
        for value in ("2026-11-12T06:10:40Z", "2026-11-12T07:10:40+01:00"):
            self.assertEqual(timestamp(value).strftime("%H:%M"), "07:10")
        snapshot = {"first_month": "2025-12", "updated": "2026-01-02", "months": {},
                    "history": {"1": {"date": "2025-12-31T23:15:00", "name": "SKO", "yard": False}}}
        totals = monthly_totals(snapshot)
        self.assertEqual(totals["2025-12"]["collections"], 0)
        self.assertEqual(totals["2026-01"]["collections"], 1)
