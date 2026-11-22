import unittest

from common_lore_index.extractors.documents.email import (
    MAX_MBOX_BYTES,
    extract_mbox_file_observations,
)
from common_lore_index.extractors.documents.email_parts import _walk_parts

SAMPLE_MBOX = b"""From MAILER-DAEMON Tue Jun 30 12:00:00 2026
Message-ID: <mbox-one@example.invalid>
Date: Tue, 30 Jun 2026 12:00:00 +0000
From: Example Sender <alice@example.invalid>
To: Example Receiver <bob@example.invalid>
Subject: MBOX private subject
List-Unsubscribe: <https://example.invalid/unsubscribe?token=fake-mail-token>
Content-Type: text/plain; charset=utf-8

MBOX body text must not leak.
From MAILER-DAEMON Tue Jun 30 12:05:00 2026
Message-ID: <mbox-two@example.invalid>
Date: Tue, 30 Jun 2026 12:05:00 +0000
From: Example Receiver <bob@example.invalid>
To: Example Sender <alice@example.invalid>
Subject: Re: MBOX private subject
In-Reply-To: <mbox-one@example.invalid>
References: <mbox-one@example.invalid>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="mbox-boundary"

--mbox-boundary
Content-Type: text/plain; charset=utf-8

Reply body text must not leak.
--mbox-boundary
Content-Type: text/plain
Content-Disposition: attachment; filename="mbox-secret-note.txt"

attachment body must not leak.
--mbox-boundary--
"""


class EmailExtractorBoundariesUnitTests(unittest.TestCase):
    def test_mime_part_walk_stops_before_later_siblings(self):
        from email.message import EmailMessage

        root = EmailMessage()
        root.make_mixed()
        nested = EmailMessage()
        nested.make_mixed()
        leaf = EmailMessage()
        nested.attach(leaf)
        root.attach(nested)
        traversal = _walk_parts(root)
        self.assertEqual(next(traversal), (root, "1", 0))
        self.assertEqual(next(traversal), (nested, "1.1", 1))
        self.assertEqual(next(traversal), (leaf, "1.1.1", 2))

        later = EmailMessage()
        root.attach(later)
        self.assertEqual(list(traversal), [(later, "1.2", 1)])

    def test_extracts_mbox_mailbox_and_reuses_safe_message_metadata(self):
        observations = extract_mbox_file_observations(
            "mail/sample.mbox",
            SAMPLE_MBOX,
        )
        payload = "\n".join(item.to_json_line() for item in observations)
        mailbox = next(item for item in observations if item.kind == "email.mailbox")
        messages = [item for item in observations if item.kind == "email.message"]
        attachments = [
            item for item in observations if item.kind == "email.attachment_stub"
        ]
        thread_hints = [
            item for item in observations if item.kind == "email.thread_hint"
        ]

        self.assertEqual(mailbox.metadata["format"], "mbox")
        self.assertEqual(mailbox.metadata["mailbox_message_count"], 2)
        self.assertEqual(mailbox.metadata["mailbox_message_count_limited"], False)
        self.assertRegex(mailbox.metadata["mailbox_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].metadata["format"], "mbox")
        self.assertEqual(messages[0].metadata["mbox_message_ordinal"], 1)
        self.assertEqual(messages[0].metadata["mailbox_file_key"], "file:mail/sample.mbox")
        self.assertTrue(messages[0].metadata["mbox_message_identity"].startswith("message:"))
        self.assertEqual(messages[1].metadata["attachment_count"], 1)
        self.assertEqual(len(attachments), 1)
        self.assertTrue(thread_hints)
        self.assertNotIn("alice@example.invalid", payload)
        self.assertNotIn("bob@example.invalid", payload)
        self.assertNotIn("MBOX private subject", payload)
        self.assertNotIn("MBOX body text", payload)
        self.assertNotIn("Reply body text", payload)
        self.assertNotIn("attachment body", payload)
        self.assertNotIn("mbox-secret-note.txt", payload)
        self.assertNotIn("fake-mail-token", payload)

    def test_mbox_reports_limits_and_malformed_archive_diagnostics(self):
        limited = extract_mbox_file_observations(
            "mail/sample.mbox",
            SAMPLE_MBOX,
            max_messages=1,
        )
        limited_mailbox = next(item for item in limited if item.kind == "email.mailbox")
        limited_errors = [item for item in limited if item.kind == "email.parse_error"]

        self.assertEqual(limited_mailbox.metadata["mailbox_message_count"], 1)
        self.assertEqual(limited_mailbox.metadata["mailbox_message_count_limited"], True)
        self.assertEqual(limited_mailbox.metadata["mailbox_limit_reason"], "message-count-limit")
        self.assertTrue(
            any(item.metadata["error_kind"] == "mbox-message-count-limit" for item in limited_errors)
        )

        oversized_message = extract_mbox_file_observations(
            "mail/sample.mbox",
            SAMPLE_MBOX,
            max_message_bytes=120,
        )
        self.assertTrue(
            any(
                item.kind == "email.parse_error"
                and item.metadata["error_kind"] == "mbox-message-size-limit"
                for item in oversized_message
            )
        )

        malformed = extract_mbox_file_observations(
            "mail/malformed.mbox",
            b"Message-ID: <not-really-mbox@example.invalid>\n\nbody\n",
        )
        self.assertTrue(
            any(
                item.kind == "email.parse_error"
                and item.metadata["error_kind"] == "mbox-missing-from-separator"
                for item in malformed
            )
        )

    def test_mbox_reports_file_limit_and_message_identity_fallback(self):
        oversized = extract_mbox_file_observations(
            "mail/oversized.mbox",
            b"x" * (MAX_MBOX_BYTES + 1),
        )
        mailbox = next(item for item in oversized if item.kind == "email.mailbox")

        self.assertEqual(mailbox.metadata["mailbox_parse_status"], "limit_exceeded")
        self.assertEqual(mailbox.metadata["mailbox_limit_reason"], "file-size-limit")
        self.assertTrue(
            any(
                item.kind == "email.parse_error"
                and item.metadata["error_kind"] == "mbox-file-size-limit"
                for item in oversized
            )
        )

        fallback = extract_mbox_file_observations(
            "mail/fallback.mbox",
            (
                b"From MAILER-DAEMON Tue Jun 30 12:00:00 2026\n"
                b"Date: Tue, 30 Jun 2026 12:00:00 +0000\n"
                b"From: Example Sender <alice@example.invalid>\n"
                b"To: Example Receiver <bob@example.invalid>\n"
                b"Subject: fallback private subject\n"
                b"List-Unsubscribe: <mailto:unsubscribe-secret@example.invalid>\n"
                b"Content-Type: text/plain; charset=utf-8\n"
                b"\n"
                b"fallback body must not leak\n"
            ),
        )
        payload = "\n".join(item.to_json_line() for item in fallback)
        message = next(item for item in fallback if item.kind == "email.message")

        self.assertFalse(message.metadata["message_id_valid"])
        self.assertEqual(message.metadata["identity_strength"], "structural")
        self.assertEqual(message.metadata["mbox_message_ordinal"], 1)
        self.assertTrue(message.metadata["mbox_message_identity"].startswith("structural:"))
        self.assertIn("mailto:redacted@example.invalid", payload)
        self.assertNotIn("unsubscribe-secret@example.invalid", payload)
        self.assertNotIn("fallback private subject", payload)
        self.assertNotIn("fallback body", payload)

    def test_mbox_spans_and_count_cap_handle_mixed_and_empty_records(self):
        from common_lore_index.extractors.documents.email import _mbox_message_spans

        content = b"preamble\rFrom first\r\nFirst body\rFrom second\nFrom third"
        spans = list(_mbox_message_spans(content))
        assert [content[start:end] for start, end in spans] == [b"First body\r", b"", b""]
        for count in (0, 1, -1):
            rows = extract_mbox_file_observations("fixture.mbox", content, max_messages=count, max_message_bytes=1)
            assert rows[0].metadata["mailbox_message_count"] == (2 if count == -1 else count)
            assert rows[0].metadata["mailbox_limit_reason"] == "message-count-limit"


if __name__ == "__main__":
    unittest.main()
