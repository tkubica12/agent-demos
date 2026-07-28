import unittest

from scripts.document_smoke import (
    DOCUMENT_URL_PREFIX,
    create_prompt,
    parse_document_url,
    private_invocation,
    readback_prompt,
    verify_readback,
)


class DocumentSmokeTests(unittest.TestCase):
    def test_prompt_requires_real_word_operations_and_exact_marker(self):
        prompt = create_prompt(
            "A13-WORD-test",
            "A13-COMMENT-test",
            "A13-REPLY-test",
            "A13 test document",
        )

        self.assertIn("workiq-word", prompt)
        self.assertIn("A13-WORD-test", prompt)
        self.assertIn("A13-COMMENT-test", prompt)
        self.assertIn("A13-REPLY-test", prompt)
        self.assertIn(DOCUMENT_URL_PREFIX, prompt)

    def test_readback_prompt_does_not_contain_hidden_validation_values(self):
        prompt = readback_prompt(
            "https://tenant-my.sharepoint.com/personal/worker/document.docx"
        )

        self.assertIn("GetDocumentContent", prompt)
        self.assertNotIn("A13-WORD-test", prompt)
        self.assertNotIn("A13-COMMENT-test", prompt)
        self.assertNotIn("A13-REPLY-test", prompt)

    def test_live_smoke_turns_disable_persistence(self):
        payload = private_invocation("validate", "readback")

        self.assertTrue(payload["persistenceDisabled"])
        self.assertEqual(payload["message"], "validate")
        self.assertIn("readback", payload["conversationId"])

    def test_parse_accepts_microsoft_document_url(self):
        url = (
            "https://tenant-my.sharepoint.com/personal/worker/document.docx"
        )

        result = parse_document_url(
            "Completed.\n" + DOCUMENT_URL_PREFIX + url
        )

        self.assertEqual(result, url)

    def test_verify_readback_requires_hidden_document_values(self):
        verify_readback(
            "A13-WORD-test A13-COMMENT-test A13-REPLY-test",
            marker="A13-WORD-test",
            comment="A13-COMMENT-test",
            reply="A13-REPLY-test",
        )

        with self.assertRaisesRegex(ValueError, "comment reply"):
            verify_readback(
                "A13-WORD-test A13-COMMENT-test",
                marker="A13-WORD-test",
                comment="A13-COMMENT-test",
                reply="A13-REPLY-test",
            )

    def test_parse_rejects_non_microsoft_document_url(self):
        with self.assertRaisesRegex(ValueError, "SharePoint"):
            parse_document_url(
                DOCUMENT_URL_PREFIX
                + "https://example.com/document.docx",
            )


if __name__ == "__main__":
    unittest.main()
