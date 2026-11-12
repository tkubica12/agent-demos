import unittest

from harness_lab.experiment import CRITERIA
from harness_lab.reporting import metrics_for_records


def measured_record(status="passed", phase="serial"):
    scores = {criterion: 4 if status == "passed" else 0 for criterion in CRITERIA}
    return {
        "variant": "deepagents", "run_id": f"{phase}-{status}", "status": status,
        "measurement_phase": phase, "elapsed_seconds": 10, "setup_seconds": 1,
        "export_seconds": 2, "model_estimated_usd": 0.01, "webiq_estimated_usd": 0.007,
        "tokens": {"input_tokens": 100, "output_tokens": 5, "cached_tokens": 80, "cache_write_tokens": 17},
        "quality": {"mean": sum(scores.values()) / len(scores), "criteria": scores},
    }


class ReportingTests(unittest.TestCase):
    def test_failures_affect_quality_not_success_latency(self):
        failed = measured_record("failed")
        failed["elapsed_seconds"] = 10000
        metric = metrics_for_records([measured_record(), failed])["deepagents"]
        self.assertEqual((metric["attempts"], metric["passed"], metric["quality_coverage"]), (2, 1, 2))
        self.assertEqual(metric["median_success_seconds"], 10)
        self.assertEqual(metric["quality_mean_including_failures"], 2)
        self.assertEqual(set(metric["quality_criteria_means"].values()), {2})
        self.assertAlmostEqual(metric["mean_success_estimated_usd"], 0.017)

    def test_quota_phase_is_excluded_from_filtered_metrics(self):
        records = [measured_record("failed", "parallel_quota"), measured_record()]
        selected = [record for record in records if record["measurement_phase"] == "serial"]
        metric = metrics_for_records(selected)["deepagents"]
        self.assertEqual((metric["planned"], metric["attempts"], metric["passed"]), (1, 1, 1))
        self.assertEqual(metric["quality_mean_including_failures"], 4)

    def test_missing_quality_and_usage_are_not_zero(self):
        record = measured_record()
        record["quality"] = record["tokens"] = None
        metric = metrics_for_records([record])["deepagents"]
        self.assertIsNone(metric["quality_mean_including_failures"])
        self.assertIsNone(metric["mean_success_estimated_usd"])
        self.assertEqual(metric["successful_usage_coverage"], 0)

    def test_empty_cohort_has_no_invented_measurements(self):
        metric = metrics_for_records([])["deepagents"]
        self.assertEqual(metric["attempts"], 0)
        self.assertIsNone(metric["median_success_seconds"])
        self.assertIsNone(metric["quality_mean_including_failures"])
