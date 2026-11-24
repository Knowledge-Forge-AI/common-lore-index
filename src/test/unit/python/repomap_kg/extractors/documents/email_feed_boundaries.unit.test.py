import json
import unittest
from unittest.mock import patch

from common_lore_index.extractors.documents.email import (
    extract_eml_file_observations,
)
from common_lore_index.extractors.documents.email_common import (
    _is_secret_prone,
    _redact_url,
    _redact_url_text,
    _redacted_filename,
)
from common_lore_index.extractors.documents.feed import (
    extract_feed_file_observations,
)
from common_lore_index.extractors.documents.feed_support import (
    _project_feed_items,
    _parse_date,
    _reference_target,
    _safe_summary,
)

from common_lore_index.graph.keys import feed_item_key


class EmailFeedExtractorBoundariesUnitTests(unittest.TestCase):
    def test_unsubscribe_target_identity_retains_each_valid_occurrence(self):
        observations = extract_eml_file_observations(
            "subscription.eml",
            b"List-Unsubscribe: <https://unreadable.invalid:port/a>, <https://good.invalid/a>, "
            b"<https://good.invalid/b>\nList-Unsubscribe: <https://good.invalid/c>\n\n",
        )
        refs = [row for row in observations if row.kind == "email.reference"]
        self.assertEqual([row.source_id.rsplit("list-unsubscribe:", 1)[1] for row in refs], ["1", "1:2", "2"])
        self.assertNotIn("unreadable.invalid", str([row.to_dict() for row in observations]))

    def test_credential_markers_are_redacted_per_span_without_affecting_prefix_neighbors(self):
        summary = _redact_url_text(
            "<https://example.invalid/a?token=frag>, <https://example.invalid/a?token=fragXYZ>",
        )
        self.assertEqual(summary.count("token=REDACTED"), 2)
        self.assertNotIn("XYZ", summary)

    def test_duplicate_addresses_have_distinct_sources_and_one_canonical_target(self):
        observations = extract_eml_file_observations(
            "duplicate.eml", b"To: First <same@example.invalid>, Second <same@example.invalid>\n\n",
        )
        addresses = [row for row in observations if row.kind == "email.address"]
        self.assertEqual(len(addresses), 2)
        self.assertEqual(addresses[1].source_id, addresses[0].source_id + ":2")
        self.assertEqual(addresses[1].target, addresses[0].target)

    def test_email_header_limits_and_address_normalization_boundaries(self):
        eml_bytes = (
            b"From: Normal User <user@example.com>\n"
            b"To: <user@>, @domain.com, missing-at, <real@sub.example.com>\n"
            b"Subject: Test email\n"
            b"Content-Type: text/plain\n\n"
            b"Body text\n"
        )
        with patch("common_lore_index.extractors.documents.email.MAX_HEADER_BYTES", 10):
            obs = extract_eml_file_observations("mail/test.eml", eml_bytes)
            error_kinds = {o.metadata["error_kind"] for o in obs if o.kind == "email.parse_error"}
            self.assertIn("header-size-limit", error_kinds)

        normal_obs = extract_eml_file_observations("mail/normal.eml", eml_bytes)
        addresses = [o for o in normal_obs if o.kind == "email.address"]
        domains = {a.metadata["address_domain"] for a in addresses}
        self.assertIn("example.com", domains)
        self.assertIn("sub.example.com", domains)

    def test_email_common_redaction_and_url_boundaries(self):
        # mailto without @
        self.assertIn("example.invalid", _redact_url("mailto:contact"))
        # mailto with secret-prone domain
        self.assertIn("example.invalid", _redact_url("mailto:alice@secret_token.com"))
        # URL with port
        port_redacted = _redact_url("https://example.com:8080/path?token=secret123&clean=value")
        self.assertIn(":8080", port_redacted)
        self.assertIn("token=REDACTED", port_redacted)
        self.assertIn("clean=value", port_redacted)

        # filename redactions
        self.assertIsNone(_redacted_filename(None))
        self.assertIsNone(_redacted_filename(""))
        self.assertEqual(_redacted_filename("no-extension"), "<redacted>")
        self.assertEqual(_redacted_filename("doc.pdf"), "<redacted>.pdf")

        # is_secret_prone
        self.assertFalse(_is_secret_prone(None))

    def test_feed_atom_and_json_fallback_identities(self):
        # Atom without id/link, with title
        atom_title_only = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Feed Title Only</title>
  <entry>
    <title>Entry 1</title>
    <updated>2026-08-26T12:00:00Z</updated>
  </entry>
</feed>"""
        obs = extract_feed_file_observations("feeds/atom_title.xml", atom_title_only)
        ch = next(o for o in obs if o.kind == "feed.channel")
        self.assertEqual(ch.metadata["identity_source"], "title+document")

        # Atom without id/link/title
        atom_no_title = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <summary>Summary text</summary>
  </entry>
</feed>"""
        obs2 = extract_feed_file_observations("feeds/atom_none.xml", atom_no_title)
        ch2 = next(o for o in obs2 if o.kind == "feed.channel")
        self.assertEqual(ch2.metadata["identity_source"], "document")

        # JSON feed with title only
        json_title_only = """{
  "version": "https://jsonfeed.org/version/1.1",
  "title": "JSON Feed Title Only",
  "items": [
    {
      "id": "1",
      "attachments": ["not-a-dict", {"mime_type": "audio/mp3"}],
      "authors": ["not-a-dict", {"avatar": "http://img"}]
    }
  ]
}"""
        obs3 = extract_feed_file_observations("feeds/json_title.json", json_title_only)
        ch3 = next(o for o in obs3 if o.kind == "feed.channel")
        self.assertEqual(ch3.metadata["identity_source"], "title+document")

        from common_lore_index.extractors.documents.feed_data import _json_channel_info
        from common_lore_index.graph.keys import feed_document_key
        doc_key = feed_document_key("feeds/j.json")
        # home_page_url only
        ch_home = _json_channel_info("feeds/j.json", doc_key, {"home_page_url": "https://example.com"})
        self.assertEqual(ch_home["identity_source"], "home_page_url")
        # neither
        ch_none = _json_channel_info("feeds/j.json", doc_key, {})
        self.assertEqual(ch_none["identity_source"], "document")

    def test_feed_projection_preserves_duplicate_and_fallback_identities_in_all_formats(self):
        rss = (
            "<rss><channel><title>Fixture</title><item><guid>same</guid></item><item><guid>same</guid></item>"
            "<item><link>https://example.invalid/item</link></item>"
            "<item><title>Fallback</title><pubDate>Fri, 02 Jan 2026 03:04:05 +0000</pubDate></item></channel></rss>"
        )
        atom = (
            '<feed xmlns="http://www.w3.org/2005/Atom"><title>Fixture</title><entry><id>same</id></entry>'
            '<entry><id>same</id></entry><entry><link href="https://example.invalid/item"/></entry>'
            '<entry><title>Fallback</title><published>2026-01-02T03:04:05Z</published></entry></feed>'
        )
        feed = json.dumps({"version": "https://jsonfeed.org/version/1.1", "title": "Fixture", "items": [
            {"id": "same"}, {"id": "same"}, {"external_url": "https://example.invalid/item"},
            {"url": "https://[invalid.example/item", "title": "Fallback", "date_modified": "2026-01-02T03:04:05Z"},
        ]})
        for text, (strong_id, link_id, date_id) in (
            (rss, ("guid", "link", "title+pubDate")),
            (atom, ("id", "alternate-link", "title+updated")),
            (feed, ("id", "url", "title+date")),
        ):
            with self.subTest(source=link_id):
                observations = extract_feed_file_observations("fixture", text)
                items = [row for row in observations if row.kind == "feed.item"]
                channel, = [row for row in observations if row.kind == "feed.channel"]
                self.assertEqual([item.metadata["identity_source"] for item in items], [strong_id, strong_id, link_id, date_id])
                self.assertEqual([item.metadata["identity_strength"] for item in items], ["strong", "strong", "strong", "weak"])
                self.assertEqual(
                    [item.metadata.get("duplicate_disambiguator") for item in items],
                    ["duplicate-1", "duplicate-2", None, None],
                )
                self.assertEqual([item.target for item in items], [
                    feed_item_key(channel.target or "", f"{strong_id}:same:duplicate-1"),
                    feed_item_key(channel.target or "", f"{strong_id}:same:duplicate-2"),
                    feed_item_key(channel.target or "", f"{link_id}:https://example.invalid/item"),
                    feed_item_key(channel.target or "", f"{date_id}:fallback:2026-01-02T03:04:05Z"),
                ])

    def test_duplicate_counts_are_ready_before_projecting_only_current_feed_item(self):
        events: list[tuple[str, int]] = []

        def identify(entry: str, ordinal: int) -> tuple[str, str, str]:
            events.append(("identity", ordinal))
            return entry, "id", "strong"

        def project(path, channel, entry, ordinal, identity):
            events.append(("project", ordinal))
            return {"entry": entry, "ordinal": ordinal, "identity": identity}

        rows = _project_feed_items(
            "fixture.json", "channel", enumerate(("same", "other", "same"), 1), identify, project,
        )
        item, count = next(rows)
        self.assertEqual((item["entry"], count), ("same", 2))
        self.assertEqual(events, [("identity", 1), ("identity", 2), ("identity", 3), ("project", 1)])
        self.assertEqual([(row["ordinal"], copies) for row, copies in rows], [(2, 1), (3, 2)])

    def test_invalid_url_keeps_feed_item_and_hides_the_original_spelling(self):
        payload = {"version": "https://jsonfeed.org/version/1.1", "title": "Fixture",
                   "feed_url": "https://invalid.example:not-a-port/feed", "home_page_url": "https://example.invalid/home",
                   "items": [
                       {"url": "https://[invalid.example/path"},
                       {"url": "https://[invalid.example/next", "external_url": "https://example.invalid/retained"},
                   ]}
        observations = extract_feed_file_observations("fixture.json", json.dumps(payload))
        items = [row for row in observations if row.kind == "feed.item"]
        self.assertEqual([row.metadata["identity_source"] for row in items], ["structural-ordinal", "url"])
        channel, = [row for row in observations if row.kind == "feed.channel"]
        self.assertEqual(channel.metadata["identity_source"], "home_page_url")
        first, second, third = [
            row for row in observations if row.kind == "feed.link" and row.metadata.get("scope") == "item"
        ]
        self.assertEqual(first.name, "malformed feed reference")
        self.assertNotIn("raw_target_summary", first.metadata)
        self.assertTrue((first.target or "").startswith("unknown:"))
        self.assertTrue((second.target or "").startswith("unknown:"))
        self.assertTrue((third.target or "").startswith("external.url:"))
        self.assertNotIn("invalid.example", str([row.to_dict() for row in observations]))

    def test_parseable_feed_url_without_authority_keeps_existing_unknown_summary(self):
        payload = {"version": "https://jsonfeed.org/version/1.1", "title": "Fixture",
                   "items": [{"id": "fixture", "url": "https:relative"}]}
        observations = extract_feed_file_observations("fixture.json", json.dumps(payload))
        (reference,) = [row for row in observations if row.kind == "feed.link"]
        self.assertTrue((reference.target or "").startswith("unknown:"))
        self.assertEqual(reference.name, "https:relative")
        self.assertEqual(reference.metadata["raw_target_summary"], "https:relative")

    def test_rss_and_atom_channel_identities_fall_through_unreadable_links(self):
        rss = '<rss><channel><title>Fixture</title><link>https://[invalid.example/item</link><item /></channel></rss>'
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>Fixture</title>'
                '<link rel="self" href="https://invalid.example:not-a-port/feed"/>'
                '<id>tag:fixture</id><entry><link href="https://[invalid.example/item"/></entry></feed>')
        for content, source in ((rss, "title+document"), (atom, "id")):
            with self.subTest(source=source):
                observations = extract_feed_file_observations("fixture.xml", content)
                channel, = [item for item in observations if item.kind == "feed.channel"]
                self.assertEqual(channel.metadata["identity_source"], source)
                self.assertNotIn("invalid.example", str([item.to_dict() for item in observations]))

    def test_malformed_mail_url_redacts_and_ipv6_authority_keeps_its_delimiters(self):
        self.assertEqual(_redact_url("https://example.invalid:not-a-port/path"), "<redacted-url>")
        redacted = _redact_url("https://user:fixture@[2001:db8::1]:8443/list?token=fixture")
        self.assertEqual(redacted, "https://[2001:db8::1]:8443/list?token=REDACTED")

    def test_json_feed_markup_in_items_stays_in_the_json_decoder(self):
        payload = {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "Fixture",
            "items": [{"id": "one", "content_html": "See <feed /> document"}],
        }
        observations = extract_feed_file_observations("fixture.json", json.dumps(payload))
        self.assertIn("feed.item", {item.kind for item in observations})
        self.assertTrue(all(item.kind != "feed.parse_error" for item in observations))
        self.assertEqual(extract_feed_file_observations("ordinary.json", '{"name":"<rss />"}'), ())

    def test_json_feed_decoding_limits_do_not_abort_other_document_routes(self):
        for error in (ValueError("fixture numeric decoding limit"), RecursionError("fixture depth limit")):
            with self.subTest(error=type(error)):
                with patch("common_lore_index.extractors.documents.feed.json.loads", side_effect=error):
                    self.assertEqual(extract_feed_file_observations("fixture.json", "{}"), ())

    def test_json_feed_ignores_wrong_shapes_in_optional_item_arrays(self):
        payload = {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "Fixture",
            "items": [
                {"id": "same", "title": "One", "attachments": None, "authors": 3, "tags": "phantom"},
                {"id": "same", "title": "Two", "tags": ["retained"], "authors": [{"name": "Ada"}]},
            ],
        }
        observations = extract_feed_file_observations("fixture.json", json.dumps(payload))
        items = [item for item in observations if item.kind == "feed.item"]
        self.assertEqual([item.name for item in items], ["One", "Two"])
        self.assertEqual([item.metadata["duplicate_disambiguator"] for item in items], ["duplicate-1", "duplicate-2"])
        self.assertEqual([item.name for item in observations if item.kind == "feed.category"], ["retained"])
        self.assertEqual([item.name for item in observations if item.kind == "feed.author"], ["Ada"])

    def test_feed_support_reference_and_summary_boundaries(self):
        self.assertIn("missing-target", _reference_target("feeds/feed.xml", "  "))
        self.assertIsNone(_parse_date("   "))
        # naive datetime
        self.assertEqual(_parse_date("2026-08-26 12:00:00"), "2026-08-26T12:00:00Z")
        # HTML stripped to empty
        self.assertIsNone(_safe_summary("<p>   <br/> </p>"))
        # summary length truncation
        long_text = "word " * 60
        summary = _safe_summary(long_text)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertTrue(summary.endswith("..."))

    def test_feed_and_email_defects_and_invalid_payloads(self):
        # Non-dict JSON feed
        obs = extract_feed_file_observations("feeds/list.json", "[1, 2, 3]")
        self.assertEqual(obs, ())

        # Email with defective headers
        bad_mime = b"Content-Type: ?invalid?mime\nSubject: =?utf-8?B?bad_base64?=\n\nBody\n"
        obs_email = extract_eml_file_observations("mail/defective.eml", bad_mime)
        self.assertTrue(len(obs_email) > 0)


if __name__ == "__main__":
    unittest.main()
