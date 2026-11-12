import unittest
from types import SimpleNamespace

from harness_lab.adapters import record_usage
from harness_lab.core import Report, ResearchRun, config


def report_fixture():
    quote = "A retrieved first-party passage with enough exact words for a citation."
    sources = {
        str(index): {"url": f"https://{domain}/page", "passage": quote}
        for index, domain in enumerate(("one.example", "two.example", "three.example"))
    }
    draft = Report(
        title="A bounded technical decision",
        recommendation="Choose the constrained approach because the evidence supports this limited use case.",
        findings=[
            {"claim": f"Finding {index} is supported by an inspected source.",
             "evidence": [{"source_id": str(index), "quote": quote}]}
            for index in range(3)
        ],
        counterargument="The alternative remains useful when the stated operational constraints no longer apply.",
        uncertainties=["Source passages do not establish the entire document's context."],
        next_steps=["Verify the critical assumption against a real workload."],
    )
    run = object.__new__(ResearchRun)
    run.settings = config()
    run.draft, run.sources, run.inspected, run.queries = draft, sources, set(sources), ["first", "second"]
    return run


class ContractTests(unittest.TestCase):
    def test_valid_traceable_report(self):
        self.assertEqual(report_fixture().problems(), [])

    def test_uninspected_quote_is_rejected(self):
        run = report_fixture()
        run.inspected.remove("0")
        self.assertTrue(any("not inspected" in issue for issue in run.problems()))

    def test_invented_quote_is_rejected(self):
        run = report_fixture()
        run.draft.findings[0].evidence[0].quote = "This invented quotation is not in the retrieved passage."
        self.assertTrue(any("not exact" in issue for issue in run.problems()))

    def test_one_domain_is_insufficient(self):
        run = report_fixture()
        for source in run.sources.values():
            source["url"] = "https://one.example/page"
        self.assertTrue(any("two distinct source domains" in issue for issue in run.problems()))

    def test_schema_rejects_executable_html_extension(self):
        data = report_fixture().draft.model_dump()
        data["findings"][0]["html"] = "<script>bad()</script>"
        with self.assertRaises(ValueError):
            Report.model_validate(data)

    def test_native_cache_semantics_are_normalized(self):
        inputs = (
            ({"input": 3, "output": 5, "cacheRead": 80, "cacheWrite": 17}, "pi_assistant_message"),
            ({"input_token_count": 100, "output_token_count": 5,
              "cache_read_input_token_count": 80, "cache_creation_input_token_count": 17}, "maf_stream_usage"),
            ({"input_tokens": 100, "output_tokens": 5,
              "cache_read_tokens": 80, "cache_write_tokens": 17}, "copilot_assistant_usage"),
            ({"input_tokens": 100, "output_tokens": 5,
              "input_token_details": {"cache_read": 80, "cache_creation": 17}}, "langchain_message"),
        )
        for raw, origin in inputs:
            run = SimpleNamespace(usage=[], log=lambda *args, **kwargs: None)
            record_usage(run, raw, origin)
            observed = run.usage[0]
            self.assertEqual(
                [observed[key] for key in ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens")],
                [100, 5, 80, 17],
            )

    def test_invalid_cache_counters_are_not_silently_priced(self):
        run = SimpleNamespace(usage=[], log=lambda *args, **kwargs: None)
        with self.assertRaises(ValueError):
            record_usage(run, {"input_tokens": 1, "output_tokens": 3, "cache_read_tokens": 10}, "other")
        self.assertEqual(run.usage, [])


class SourceBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_limit_is_enforced_without_network(self):
        run = report_fixture()
        run.settings["limits"]["max_source_reads"] = 3
        run.sources["extra"] = {"url": "https://four.example", "passage": "not inspected"}
        with self.assertRaisesRegex(ValueError, "budget exhausted"):
            await run.inspect_source("extra")
        self.assertNotIn("extra", run.inspected)
        self.assertTrue((await run.inspect_source("0"))["ok"])
