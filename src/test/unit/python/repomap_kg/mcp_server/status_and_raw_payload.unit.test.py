import json
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest.mock import patch

from common_lore_index.storage import StorageSchemaError
from common_lore_index.storage.sqlite_local.raw_payload import (
    ascii_lower, contains_ascii_folded, jsonb_contains_ascii_folded, jsonb_text, jsonb_value,
)

from repomap_test_support.mcp_server import McpServerTestSupport


class McpServerStatusAndRawPayloadUnitTests(McpServerTestSupport):
    def test_mcp_smoke3_refresh_status_flags_complete_without_finished_at(
        self,
    ):
        from common_lore_index.server.mcp import cloe_refresh_status

        config_path = self.write_visible_ops_config()
        with self.patch_ops_config(config_path):
            with patch(
                "common_lore_index.server.ops.query_refresh_status",
                return_value={
                    "repo-map": self.synthetic_refresh_status(
                        latest_run_status="complete",
                        latest_run_finished_at=None,
                        raw_observations=9,
                        canonical_nodes=8,
                        canonical_edges=7,
                    )
                },
            ):
                payload = cloe_refresh_status()

        self.assertEqual(payload["graphs"][0]["latest_run_status"], "complete")
        self.assertIsNone(payload["graphs"][0]["latest_run_finished_at"])
        self.assertEqual(
            payload["graphs"][0]["latest_run_consistency"],
            {
                "complete_without_finished_at": True,
                "diagnostic": "complete run has no finished timestamp in storage",
            },
        )
        self.assert_read_only_payload(payload)

    def test_mcp_smoke3_graph_status_flags_complete_without_finished_at(
        self,
    ):
        from common_lore_index.server.mcp import cloe_graph_status

        config_path = self.write_visible_ops_config()
        with self.patch_ops_config(config_path):
            with patch(
                "common_lore_index.server.ops.query_refresh_status",
                return_value={
                    "repo-map": self.synthetic_refresh_status(
                        latest_run_status="complete",
                        latest_run_finished_at=None,
                    )
                },
            ):
                payload = cloe_graph_status(graph_id="repo-map")

        self.assertEqual(payload["storage"]["latest_run_status"], "complete")
        self.assertIsNone(payload["storage"]["latest_run_finished_at"])
        self.assertEqual(
            payload["storage"]["latest_run_consistency"],
            {
                "complete_without_finished_at": True,
                "diagnostic": "complete run has no finished timestamp in storage",
            },
        )
        self.assert_read_only_payload(payload)

    def _live_ops3_graph_status(self, host_error: str) -> tuple[dict, str, list]:
        from common_lore_index.server.mcp import cloe_graph_status

        container_probes: list[str] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            home.mkdir()
            (home / "repomap.rpl.toml").write_text(
                self.visible_ops_config().replace(
                    'host = "127.0.0.1"',
                    'host = "postgres"',
                ),
                encoding="utf-8",
            )
            private_path = str(Path.home() / "private-live-ops3")

            def fake_run_psql(command, **kwargs):
                if command[0] == "psql":
                    raise StorageSchemaError(host_error)
                raise StorageSchemaError(
                    "psql failed: FATAL: database "
                    '"repomap_live_ops3" does not exist '
                    f"while reading {private_path} via docker exec psql"
                )

            def container_probe(name):
                def _probe(*args, **kwargs):
                    container_probes.append(name)
                    raise AssertionError(f"host-only status reached {name}")
                return _probe

            with (
                patch.dict(
                    "os.environ",
                    {"CLOE_OPS_CONFIG": str(home)},
                    clear=True,
                ),
                patch(
                    "common_lore_index.ops.refresh.run_psql",
                    side_effect=fake_run_psql,
                ),
                patch(
                    "common_lore_index.ops.readback.execute_json_readback_with_driver",
                    side_effect=lambda sql, **kwargs: (
                        fake_run_psql([kwargs["psql_command"]], input_text=sql)
                    ),
                ),
                patch(
                    "common_lore_index.ops.readback.selected_json_readback_driver",
                    return_value="psql",
                ),
                patch(
                    "common_lore_index.ops.readback._resolve_container_readback_plan",
                    side_effect=container_probe("container plan"),
                ),
                patch(
                    "common_lore_index.ops.readback.shutil.which",
                    side_effect=container_probe("shutil.which"),
                    create=True,
                ),
                patch(
                    "common_lore_index.runtime.local.inspect_container",
                    side_effect=container_probe("inspect_container"),
                    create=True,
                ),
            ):
                payload = cloe_graph_status(graph_id="repo-map")
        return payload, private_path, container_probes

    def test_live_ops3_mcp_graph_status_missing_database_error_is_safe(self):
        # READSTORE2: graph status is host-only. An unreachable container-internal
        # host yields the bounded host reason; the container is never probed.
        payload, private_path, probes = self._live_ops3_graph_status(
            'could not translate host name "postgres" to address'
        )

        message = payload["storage"]["error"]
        self.assertIn('could not translate host name "postgres"', message)
        self.assertNotIn("graph database is missing or not initialized", message)
        self.assertNotIn("no running RepoMap-owned Postgres container", message)
        self.assertNotIn("Postgres container", message)
        self.assertNotIn(private_path, message)
        self.assertNotIn("docker exec", message)
        self.assertEqual(probes, [])
        self.assert_read_only_payload(payload)

    def test_live_ops3_mcp_graph_status_host_missing_database_maps_safely(self):
        payload, private_path, probes = self._live_ops3_graph_status(
            'psql failed: FATAL: database "repomap_live_ops3" does not exist'
        )

        message = payload["storage"]["error"]
        self.assertIn("graph database is missing or not initialized", message)
        self.assertNotIn("Postgres container", message)
        self.assertNotIn(private_path, message)
        self.assertNotIn("docker exec", message)
        self.assertEqual(probes, [])
        self.assert_read_only_payload(payload)

    def test_mcp_smoke3_complete_with_finished_at_has_no_timestamp_warning(self):
        from common_lore_index.server.mcp import cloe_refresh_status

        config_path = self.write_visible_ops_config()
        with self.patch_ops_config(config_path):
            with patch(
                "common_lore_index.server.ops.query_refresh_status",
                return_value={
                    "repo-map": self.synthetic_refresh_status(
                        latest_run_id=78,
                        latest_run_status="complete",
                        latest_run_finished_at="2026-07-01T00:01:00Z",
                    )
                },
            ):
                payload = cloe_refresh_status()

        self.assertEqual(
            payload["graphs"][0]["latest_run_consistency"],
            {"complete_without_finished_at": False},
        )

    def test_mcp_smoke3_non_complete_null_finished_at_has_no_complete_warning(self):
        from common_lore_index.server.mcp import cloe_refresh_status

        config_path = self.write_visible_ops_config()
        with self.patch_ops_config(config_path):
            with patch(
                "common_lore_index.server.ops.query_refresh_status",
                return_value={
                    "repo-map": self.synthetic_refresh_status(
                        latest_run_id=79,
                        latest_run_status="running",
                        latest_run_finished_at=None,
                    )
                },
            ):
                payload = cloe_refresh_status()

        self.assertEqual(
            payload["graphs"][0]["latest_run_consistency"],
            {"complete_without_finished_at": False},
        )

    def test_mcp_smoke4_include_raw_false_documents_metadata_semantics(self):
        from common_lore_index.server.mcp import cloe_search_observations

        config_path = self.write_visible_ops_config()
        observation_payload = {
            "results": [
                {
                    "ordinal": 1,
                    "kind": "command.reference",
                    "path": "synthetic/config.txt",
                    "source_id": "synthetic/config.txt#command:1",
                    "metadata": {"raw": "synthetic command text"},
                }
            ],
            "total": 1,
        }

        with self.patch_ops_config(config_path):
            with patch(
                "common_lore_index.server.ops.query_mcp_search",
                return_value=observation_payload,
            ) as query:
                payload = cloe_search_observations(
                    graph_id="repo-map",
                    query="command",
                    include_raw=False,
                )

        self.assertFalse(query.call_args.kwargs["include_raw"])
        self.assertNotIn("payload", payload["results"][0])
        self.assert_raw_payload_policy(payload, include_raw=False)
        self.assertEqual(
            payload["results"][0]["metadata"]["raw"],
            "synthetic command text",
        )
        self.assert_read_only_payload(payload)

    def test_mcp_smoke4_include_raw_true_reports_payload_included(self):
        from common_lore_index.server.mcp import cloe_search_observations

        config_path = self.write_visible_ops_config()
        observation_payload = {
            "results": [
                {
                    "ordinal": 1,
                    "kind": "command.reference",
                    "path": "synthetic/config.txt",
                    "source_id": "synthetic/config.txt#command:1",
                    "metadata": {"raw": "synthetic command text"},
                    "payload": {
                        "kind": "command.reference",
                        "metadata": {"raw": "synthetic command text"},
                    },
                }
            ],
            "total": 1,
        }

        with self.patch_ops_config(config_path):
            with patch(
                "common_lore_index.server.ops.query_mcp_search",
                return_value=observation_payload,
            ) as query:
                payload = cloe_search_observations(
                    graph_id="repo-map",
                    query="command",
                    include_raw=True,
                )

        self.assertTrue(query.call_args.kwargs["include_raw"])
        self.assertIn("payload", payload["results"][0])
        self.assert_raw_payload_policy(payload, include_raw=True)
        self.assert_read_only_payload(payload)

    def test_observation_payload_literal_match_crosses_fold_window(self):
        payload = "x" * (65536 - 3) + "CrOSSing É"
        for needle, expected in (("", True), ("crossing", True), ("CROSSING É", True), ("crossing é", False),
                                 ("y" * (len(payload) + 1), False)):
            with self.subTest(needle=needle[:24]):
                self.assertEqual(contains_ascii_folded(payload, ascii_lower(needle)), expected)

    def test_sqlite_jsonb_projection_preserves_key_order_and_numeric_types(self):
        value = {"z": (Decimal("-0.000"), Decimal("12"), "é\t"), "é": [None, True], "a": Decimal("1.20")}
        actual = jsonb_value(value)
        expected = json.loads(jsonb_text(value))
        self.assertEqual(list(actual), ["a", "z", "é"])
        self.assertEqual(json.dumps(actual, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))
        self.assertIsInstance(actual["z"][0], float)
        self.assertIsInstance(actual["z"][1], int)


def test_streamed_jsonb_literal_matches_fragment_boundaries_and_numeric_spelling():
    value = {
        "a": ["x" * 65542 + "Ab", "", "é"],
        "β": {"text": "quote\" slash\\", "number": 0.00002},
        "z": ["", *([["k", True]] * 27000)],
    }
    canonical = ascii_lower(jsonb_text(value))
    needles = ['Ab", "", "é"', ']["different"', ', "β": {', '0.00002', 'ab", "", "É"', ""]
    for needle in needles:
        folded = ascii_lower(needle)
        assert jsonb_contains_ascii_folded(value, folded) is (folded in canonical)


def test_jsonb_literal_matches_across_wide_escaped_string_chunks():
    middle = '先Ab"' + chr(92) + chr(1) + "É"
    body = "先" * (16 * 1024 - 2) + middle + "É" * (16 * 1024)
    data = {"key": [body, "end"]}
    crossing = ascii_lower(json.dumps(middle, ensure_ascii=False)[1:-1])
    assert jsonb_contains_ascii_folded(data, crossing)
    assert jsonb_contains_ascii_folded(data, 'É", "end')
    assert not jsonb_contains_ascii_folded(data, 'é", "end')


def test_jsonb_projection_preserves_small_and_wide_integer_values_and_types():
    positive = (1 << 64) - 1
    numeric = [True, False, 0, 1, -1, positive, -positive, 1 << 64, -(1 << 128), Decimal("1.50"), 1.2e8]
    expected = json.loads(jsonb_text(numeric))
    value = jsonb_value(numeric)
    assert value == expected and [type(number) for number in value] == [type(number) for number in expected]
