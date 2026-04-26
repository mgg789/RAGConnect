from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from client_gateway.config import ClientConfig
from client_gateway.project_registry import register_project


class ProjectRegistryTests(unittest.TestCase):
    def test_register_project_writes_snippet_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir()
            config_path = Path(tmpdir) / "client_config.yaml"
            config = ClientConfig()
            result = register_project(
                config=config,
                repo_root=str(repo_root),
                project_label="sample-project",
                config_path=config_path,
                write_agents=True,
                write_claude=True,
            )
            self.assertEqual(result["project_label"], "sample-project")
            self.assertTrue((repo_root / "AGENTS.md").exists())
            self.assertTrue((repo_root / "CLAUDE.md").exists())
            self.assertIn('memory-label = "sample-project"', (repo_root / "AGENTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
