from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from client_gateway.config import ClientConfig, DestinationConfig, ProjectContextConfig
from client_gateway.context import resolve_project_context


class ResolveProjectContextTests(unittest.TestCase):
    def test_explicit_label_wins(self) -> None:
        config = ClientConfig(default_project="fallback")
        resolved = resolve_project_context(config, "explicit", roots=["C:/repo"])
        self.assertEqual(resolved.resolved_project_label, "explicit")
        self.assertEqual(resolved.source, "explicit")

    def test_roots_match_registered_context(self) -> None:
        config = ClientConfig(
            destinations=[DestinationConfig(url="http://127.0.0.1:9621")],
            project_contexts=[ProjectContextConfig(repo_root="C:/work/project", project_label="team-space")],
        )
        resolved = resolve_project_context(config, None, roots=["C:/work/project"])
        self.assertEqual(resolved.resolved_project_label, "team-space")
        self.assertEqual(resolved.source, "roots")

    def test_default_project_used_when_roots_do_not_match(self) -> None:
        config = ClientConfig(default_project="fallback")
        resolved = resolve_project_context(config, None, roots=["C:/other"])
        self.assertEqual(resolved.resolved_project_label, "fallback")
        self.assertEqual(resolved.source, "default")


if __name__ == "__main__":
    unittest.main()
