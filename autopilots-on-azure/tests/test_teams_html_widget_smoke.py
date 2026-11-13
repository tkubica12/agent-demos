import json
import unittest

from microsoft_agents.activity import Activity

from scripts.teams_html_widget_smoke import widget_activity


class WidgetSmokeTests(unittest.TestCase):
    def test_existing_activity_sdk_preserves_widget_wire_format_and_host_bootstrap(self):
        activity = Activity.model_validate(widget_activity("abc123"))
        encoded = activity.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(encoded["textFormat"], "extendedmarkdown")
        payload = json.loads(encoded["text"].split("```html-widget\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(payload["type"], "widget/mcp-ui")
        self.assertEqual(payload["domain"], "https://teams.microsoft.com")
        html = payload["html"]
        self.assertIn("ui/initialize", html)
        self.assertIn("ui/notifications/initialized", html)
        self.assertIn("ui/notifications/size-changed", html)
        self.assertNotIn("<button", html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("<script src=", html)

    def test_interaction_is_local_and_waits_for_host_initialization(self):
        encoded = widget_activity("abc123", interactive=True)["text"]
        html = json.loads(encoded.split("```html-widget\n", 1)[1].split("\n```", 1)[0])["html"]
        self.assertIn('id="increment" disabled', html)
        self.assertGreater(html.index(".disabled = false"), html.index("initialized = true"))
        self.assertNotIn("tools/call", html)

    def test_probe_id_cannot_inject_html_or_a_fence(self):
        for value in ("", "<script>", "```", "číslo"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                widget_activity(value)
