"""
Tests for memrem's transcript extractor + scrubber.

Stdlib-only (unittest) so CI needs no pip install. Run:
    python3 -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

# Load the script as a module (it lives in the plugin's skill dir).
_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "plugins" / "memrem" / "skills" / "memrem" / "scripts" / "extract_transcripts.py"
)
_spec = importlib.util.spec_from_file_location("extract_transcripts", _SCRIPT)
ext = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ext)


class TestScrub(unittest.TestCase):
    """Every credential shape must be redacted; legitimate facts must survive.
    NOTE: all secrets below are synthetic, not real."""

    REDACTED = [
        ("openai_hyphen", "sk-abc123def456ghi789jkl"),
        ("openai_underscore", "sk_abc123def456ghi789jkl"),
        ("stripe_live", "sk_live_abcd1234efgh5678ijkl"),
        ("stripe_restricted", "rk_test_abcd1234efgh5678ijkl"),
        ("slack", "xoxb-1234567890-abcdefghij"),
        ("github_classic", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
        ("github_fine", "github_pat_11ABCDEFG0123456789_abcdefghijklmnop"),
        ("sendgrid", "SG.abcdefghij1234567890.abcdefghij1234567890xyz"),
        ("mailgun", "key-0123456789abcdef0123456789abcdef"),
        ("aws_access", "AKIAIOSFODNN7EXAMPLE"),
        ("aws_secret", "aws_secret_access_key = wJalrXUtnFEMIabcdEXAMPLEKEY"),
        ("google", "AIzaSyA0123456789abcdefghijklmnopqrstu"),
        ("jwt", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF"),
        ("authorization", "Authorization: Token deadbeef1234"),
        ("bearer", "Bearer abc.def.ghi"),
        ("conn_postgres", "postgres://u:p@host:5432/db"),
        ("basic_auth_url", "https://user:p4ss@host.com/x"),
        ("env_dbpass", "DB_PASS=supersecret123"),
        ("env_appkey", "APP_KEY=base64:abcd1234EFGH=="),
        ("kw_token", "token: abc123def456"),
        ("hex_blob", "deadbeefdeadbeefdeadbeefdeadbeef0123"),
        ("email", "carlos@example.com"),
        ("pem", "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"),
    ]

    PRESERVED = [
        ("package_spec", "npm i foo@1.2.3"),
        ("plain_fact", "Carlos is Tech Lead at Blue Medical, prefers decisive answers"),
        ("version", "Laravel 13.8 with React/Vite PWA"),
        ("scoped_pkg", "install @angular/core@21"),
    ]

    def test_secrets_redacted(self):
        for name, payload in self.REDACTED:
            with self.subTest(secret=name):
                out = ext.scrub(payload)
                self.assertIn("REDACTED", out, f"{name} leaked: {out!r}")

    def test_legit_facts_preserved(self):
        for name, payload in self.PRESERVED:
            with self.subTest(fact=name):
                out = ext.scrub(payload)
                self.assertNotIn("REDACTED", out, f"{name} over-redacted: {out!r}")

    def test_pem_block_fully_consumed(self):
        out = ext.scrub("-----BEGIN PRIVATE KEY-----\nSECRETBODY\n-----END PRIVATE KEY-----")
        self.assertNotIn("SECRETBODY", out)


class TestExtractBlocks(unittest.TestCase):
    """Branch on block TYPE, not string-sniffing (py-review B1/B2)."""

    def test_string_content_is_user(self):
        user, tool = ext.extract_blocks("hola, una idea")
        self.assertEqual(user, "hola, una idea")
        self.assertEqual(tool, "")

    def test_text_blocks_extracted(self):
        user, tool = ext.extract_blocks([{"type": "text", "text": "fact A"}])
        self.assertEqual(user, "fact A")
        self.assertEqual(tool, "")

    def test_tool_result_list_content(self):
        # tool_result.content is usually a LIST of text blocks — the real shape
        content = [{"type": "tool_result", "content": [{"type": "text", "text": "git log output"}]}]
        user, tool = ext.extract_blocks(content)
        self.assertEqual(user, "")
        self.assertEqual(tool, "git log output")

    def test_tool_result_string_content(self):
        content = [{"type": "tool_result", "content": "plain string result"}]
        _, tool = ext.extract_blocks(content)
        self.assertEqual(tool, "plain string result")

    def test_mixed_user_and_tool(self):
        content = [
            {"type": "text", "text": "my prompt"},
            {"type": "tool_result", "content": "tool out"},
        ]
        user, tool = ext.extract_blocks(content)
        self.assertEqual(user, "my prompt")
        self.assertEqual(tool, "tool out")

    def test_tool_use_input_is_dropped(self):
        # tool_use args must NOT be emitted (a secret in a command stays unsurfaced)
        content = [{"type": "tool_use", "name": "Bash", "input": {"command": "export T=sk_live_secret"}}]
        user, tool = ext.extract_blocks(content)
        self.assertEqual(user, "")
        self.assertEqual(tool, "")

    def test_dict_content(self):
        user, _ = ext.extract_blocks({"type": "text", "text": "single block"})
        self.assertEqual(user, "single block")


class TestIterRecent(unittest.TestCase):
    """Window filtering by mtime, generic across any project dir."""

    def test_window_filters_by_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            recent = base / "recent.jsonl"
            old = base / "old.jsonl"
            recent.write_text('{"type":"user"}\n')
            old.write_text('{"type":"user"}\n')
            now = time.time()
            os.utime(recent, (now, now))
            os.utime(old, (now - 30 * 86400, now - 30 * 86400))  # 30 days old
            cutoff = now - 7 * 86400  # last 7 days
            found = [p.name for p in ext.iter_recent(base, cutoff)]
            self.assertIn("recent.jsonl", found)
            self.assertNotIn("old.jsonl", found)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(list(ext.iter_recent(Path(d), 0.0)), [])


class TestEndToEnd(unittest.TestCase):
    """A secret in a tool_result must never appear in emitted output."""

    def test_secret_in_transcript_is_scrubbed_on_emit(self):
        import io
        import contextlib

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            line = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result",
                                 "content": "leaked sk_live_abcd1234efgh5678ijkl here"}],
                },
            }
            (base / "s.jsonl").write_text(json.dumps(line) + "\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ext.main([str(base), "--days", "3650"])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertNotIn("sk_live_abcd1234", out)
            self.assertIn("REDACTED", out)


if __name__ == "__main__":
    unittest.main()
