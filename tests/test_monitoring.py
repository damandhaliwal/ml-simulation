import unittest

from monitoring.checks import check_drift, summarize_feature_rows


def row(**overrides):
    base = {"distance_km": 2.0, "item_count": 2, "traffic_index": 1.0,
            "restaurant_backlog": 2, "orders_waiting_for_courier": 1,
            "idle_couriers": 1, "precipitation_mm_per_hour": 0,
            "temperature_c": 18.0, "pickup_zone_id": "Z1",
            "dropoff_zone_id": "Z2", "weather_type": "clear"}
    base.update(overrides)
    return base


class TestMonitoring(unittest.TestCase):
    def test_summary_statistics(self):
        summary = summarize_feature_rows([row(), row(distance_km=4.0)])
        self.assertEqual(summary["n"], 2)
        self.assertEqual(summary["numeric"]["distance_km"], {"mean": 3.0, "std": 1.0})
        self.assertEqual(summary["categorical"]["weather_type"], {"clear": 1.0})

    def test_identical_population_has_no_findings(self):
        baseline = summarize_feature_rows([row(), row(distance_km=4.0)])
        self.assertEqual(check_drift(baseline, summarize_feature_rows([row(), row(distance_km=4.0)])), [])

    def test_shifted_mean_is_flagged_in_std_units(self):
        baseline = summarize_feature_rows([row(distance_km=2.0), row(distance_km=4.0)])
        shifted = summarize_feature_rows([row(distance_km=12.0), row(distance_km=14.0)])
        findings = check_drift(baseline, shifted)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["feature"], "distance_km")
        self.assertEqual(findings[0]["std_shifts"], 10.0)

    def test_category_redistribution_is_flagged(self):
        baseline = summarize_feature_rows([row(), row()])
        stormy = summarize_feature_rows([row(weather_type="storm", precipitation_mm_per_hour=10),
                                         row(weather_type="storm", precipitation_mm_per_hour=10)])
        weather = [f for f in check_drift(baseline, stormy) if f["feature"] == "weather_type"]
        self.assertEqual(len(weather), 1)
        self.assertEqual(weather[0]["l1_distance"], 2.0)


if __name__ == "__main__":
    unittest.main()
