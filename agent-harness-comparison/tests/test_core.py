import tempfile
import unittest
from pathlib import Path

from harness_lab.core import Budget, Report, config, escape


class CoreTests(unittest.TestCase):
    def test_html_content_is_escaped(self):
        self.assertEqual(escape('<script a="x">&'), "&lt;script a=&quot;x&quot;&gt;&amp;")

    def test_report_cannot_smuggle_extra_html_field(self):
        with self.assertRaises(ValueError):
            Report.model_validate({"html": "<script>alert(1)</script>"})

    def test_run_budget_persists_across_instances(self):
        settings = config()
        settings["limits"]["max_runs"] = 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = Budget(root)
            run_id = first.start("pi", "unit", settings)
            first.finish(run_id, "failed", None)
            with self.assertRaisesRegex(RuntimeError, "CAPPED"):
                Budget(root).start("maf", "unit", settings)

    def test_failed_calls_consume_webiq_budget(self):
        settings = config()
        settings["webiq"]["max_calls"] = 1
        with tempfile.TemporaryDirectory() as directory:
            budget = Budget(Path(directory))
            budget.web_call("unit-test", settings)
            with self.assertRaisesRegex(RuntimeError, "CAPPED"):
                budget.web_call("unit-test", settings)

    def test_unknown_usage_retains_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = Budget(Path(directory))
            run_id = budget.start("pi", "unit", config())
            budget.finish(run_id, "failed", None)
            with budget.connect() as db:
                row = db.execute("SELECT cost,reserve FROM runs").fetchone()
            self.assertIsNone(row[0])
            self.assertGreater(row[1], 0)


if __name__ == "__main__":
    unittest.main()
