"""
Manifest sanity tests — catch a broken marketplace.json / plugin.json / SKILL.md
before it ships (the kind of error `/plugin validate` flags, but in CI). Stdlib only.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN = ROOT / "plugins" / "memrem" / ".claude-plugin" / "plugin.json"
SKILL = ROOT / "plugins" / "memrem" / "skills" / "memrem" / "SKILL.md"
SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+].+)?$")


class TestMarketplace(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(MARKETPLACE.read_text())

    def test_required_top_level_fields(self):
        for key in ("name", "owner", "plugins"):
            self.assertIn(key, self.data, f"marketplace.json missing '{key}'")

    def test_owner_shape(self):
        owner = self.data["owner"]
        self.assertIn("name", owner)
        # only name/email are documented owner fields — no stray 'url'
        self.assertNotIn("url", owner, "owner.url is undocumented; use email")

    def test_plugin_entry(self):
        plugins = self.data["plugins"]
        self.assertTrue(plugins, "no plugins listed")
        entry = plugins[0]
        self.assertEqual(entry["name"], "memrem")
        self.assertIn("source", entry)

    def test_source_path_exists(self):
        # relative source must resolve to a real plugin dir with its own manifest
        src = self.data["plugins"][0]["source"]
        self.assertTrue(src.startswith("./"), "source should be a relative ./path")
        plugin_dir = (ROOT / src).resolve()
        self.assertTrue((plugin_dir / ".claude-plugin" / "plugin.json").is_file(),
                        f"source {src} has no plugin.json")


class TestPluginManifest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(PLUGIN.read_text())

    def test_name_is_namespace(self):
        self.assertEqual(self.data["name"], "memrem")

    def test_version_is_semver(self):
        self.assertRegex(self.data.get("version", ""), SEMVER)

    def test_license_present(self):
        self.assertIn("license", self.data)


class TestSkillFrontmatter(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text()

    def test_has_frontmatter(self):
        self.assertTrue(self.text.startswith("---\n"), "SKILL.md must open with YAML frontmatter")

    def test_side_effecting_skill_is_not_model_invocable(self):
        # a memory-mutating skill must require explicit user invocation
        self.assertIn("disable-model-invocation: true", self.text)

    def test_declares_allowed_tools(self):
        self.assertRegex(self.text, r"(?m)^allowed-tools:")

    def test_references_bundled_script_via_skill_dir(self):
        # Phase 2 must call the bundled extractor through the skill-dir variable
        self.assertIn("${CLAUDE_SKILL_DIR}/scripts/extract_transcripts.py", self.text)
        self.assertTrue(
            (SKILL.parent / "scripts" / "extract_transcripts.py").is_file(),
            "bundled extractor script is missing",
        )


if __name__ == "__main__":
    unittest.main()
