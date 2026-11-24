import unittest
from common_lore_index.extractors.config.generic import extract_config_file_observations
from common_lore_index.extractors.config.jsonc import (
    JsoncNormalizationError,
    normalize_jsonc,
    _strip_jsonc_comments,
)

class ConfigStructureBoundariesUnitTests(unittest.TestCase):
    def test_protects_invalid_and_credentialed_config_urls(self):
        rows = extract_config_file_observations(
            "fixture.json",
            '{"malformed":"https://broken.invalid:port/path",'
            '"protected":"https://user:fixture@hidden.invalid/path",'
            '"retained":"https://valid.invalid/path"}',
        )
        refs = [row for row in rows if row.kind == "config.reference"]
        self.assertEqual(
            [row.metadata["resolution_reason"] for row in refs],
            ["malformed-url", "credentialed-url", "url-literal"],
        )
        self.assertTrue((refs[0].target or "").startswith("unknown:"))
        self.assertTrue((refs[1].target or "").startswith("external:url:"))
        self.assertTrue((refs[2].target or "").startswith("external.url:"))
        serialized = str([row.to_dict() for row in rows])
        self.assertNotIn("broken.invalid", serialized)
        self.assertNotIn("hidden.invalid", serialized)

    def test_protected_url_cannot_become_stable_array_identity(self):
        rows = extract_config_file_observations(
            "fixture.toml",
            '[[endpoints]]\nname = "https://user:fixture@hidden.invalid:broken/path"\n'
            '[[endpoints]]\nname = "ordinary"\n',
        )
        path, = [row for row in rows if row.kind == "config.path"]
        self.assertEqual(path.name, "/endpoints")
        self.assertEqual(path.metadata["array_policy"], "summary-only")
        self.assertNotIn("hidden.invalid", str([row.to_dict() for row in rows]))

    def test_jsonc_strip_comments_and_trailing_commas(self):
        jsonc_text = """// Header comment
{
    /* multi-line
       comment */
    "key": "value", // line comment
    "escaped_quote": "hello \"world\"",
    "list": [1, 2, 3,],
}
"""
        normalized = normalize_jsonc(jsonc_text)
        self.assertIn('"key": "value"', normalized)
        self.assertIn('"list": [1, 2, 3]', normalized)

        with self.assertRaises(JsoncNormalizationError):
            _strip_jsonc_comments("/* unterminated comment")

    def test_generic_structure_deep_nesting_and_scalars(self):
        content = """{
    "root": {
        "level1": {
            "level2": {
                "items": ["a", "b", null, true, false, 42]
            }
        }
    }
}"""
        obs = extract_config_file_observations("nested.json", content)
        self.assertEqual(obs[0].kind, "config.document")
        kinds = {o.kind for o in obs}
        self.assertIn("config.path", kinds)

    def test_generic_structure_empty_structures(self):
        content = """{
    "empty_dict": {},
    "empty_list": [],
    "empty_string": ""
}"""
        obs = extract_config_file_observations("empty.json", content)
        self.assertEqual(obs[0].kind, "config.document")

if __name__ == "__main__":
    unittest.main()
