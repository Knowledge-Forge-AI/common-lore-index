import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common_lore_index.cli import main

class CliLocalOpsMcpServeUnitTests(unittest.TestCase):
    def test_mcp_serve_sets_ops_config_and_delegates_to_stdio_server(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "repomap.local.toml"
            config_path.write_text("schema_version = 1\n", encoding="utf-8")

            with patch.dict("os.environ", {}, clear=True):
                def serve() -> int:
                    self.assertEqual(os.environ["REPOMAP_OPS_CONFIG"], str(config_path))
                    return 0

                with patch("common_lore_index.server.mcp.serve_stdio", side_effect=serve) as serve_stdio:
                    exit_code = main(["mcp", "serve", "--config", str(config_path)])

                self.assertEqual(exit_code, 0)
                self.assertNotIn("REPOMAP_OPS_CONFIG", os.environ)
                serve_stdio.assert_called_once_with()
    def test_mcp_serve_sets_repo_map_home_and_delegates_to_stdio_server(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_map_home = Path(tmpdir) / "repo-map-home"
            repo_map_home.mkdir()

            with patch.dict("os.environ", {}, clear=True):
                def serve() -> int:
                    self.assertEqual(os.environ["CLOE_HOME"], str(repo_map_home))
                    self.assertNotIn("REPOMAP_HOME", os.environ)
                    return 0

                with patch("common_lore_index.server.mcp.serve_stdio", side_effect=serve) as serve_stdio:
                    exit_code = main(["mcp", "serve", "--repo-map-home", str(repo_map_home)])

                self.assertEqual(exit_code, 0)
                self.assertNotIn("CLOE_HOME", os.environ)
                self.assertNotIn("REPO" + "_MAP_HOME", os.environ)
                self.assertNotIn("REPOMAP_OPS_CONFIG", os.environ)
                serve_stdio.assert_called_once_with()
