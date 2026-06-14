#!/usr/bin/env python3
"""
memrem — transcript extractor + secret scrubber.

Reads Claude Code session transcripts (`*.jsonl`) from a project dir, filters to a
recency window, extracts the consolidation-relevant text (user prompts, assistant
text, and — scrubbed — tool results), and prints it labeled for the skill to reason
over. Secrets are redacted ON INGEST so they never reach the model's context.

This is *best-effort mechanical redaction of common credential shapes* — not a
guarantee. PHI (names, diagnoses) is not regex-detectable and is left to the skill's
judgment (and a write-back re-scrub). Bias is intentionally toward over-redaction.

Usage:
    python3 extract_transcripts.py <transcripts_dir> [--days N] [--since-epoch F] [--max-chars N]

Args:
    transcripts_dir   Dir containing <session>.jsonl (e.g. ~/.claude/projects/<slug>/)
    --days N          Only files modified within the last N days (default 7). Must be >= 0.
    --since-epoch F   Override: only files with mtime >= F (epoch seconds). Takes
                      precedence over --days.
    --max-chars N     Truncate each emitted message to N chars (default 2000).

Output is plain text grouped by file, with [USER]/[ASSISTANT]/[TOOL] tags, to stdout.
Diagnostics go to stderr. Exit 0 normally, 2 on a bad directory argument.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Iterable

# --- Secret / structured-credential redaction (applied to every emitted string) ---
# Order matters: multiline/specific patterns first, generic last.
SCRUB: list[tuple[re.Pattern[str], str]] = [
    # PEM private key blocks (multiline; the whole JSONL line is in memory so [\s\S] is safe)
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----'),
     '[REDACTED-PEM]'),
    # Provider tokens
    (re.compile(r'sk[-_][A-Za-z0-9]{10,}'), '[REDACTED-KEY]'),               # OpenAI sk- / sk_
    (re.compile(r'\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}'), '[REDACTED-STRIPE]'),
    (re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}'), '[REDACTED-SLACK]'),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}'), '[REDACTED-GH]'),
    (re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}'), '[REDACTED-GH]'),
    (re.compile(r'\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}'), '[REDACTED-SENDGRID]'),
    (re.compile(r'\bkey-[0-9a-f]{32}\b'), '[REDACTED-MAILGUN]'),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), '[REDACTED-AWS]'),
    (re.compile(r'(?i)\baws_secret_access_key\b\s*[=:]?\s*\S+'), 'aws_secret_access_key=[REDACTED]'),
    (re.compile(r'\bAIza[0-9A-Za-z_-]{20,}'), '[REDACTED-GOOGLE]'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+'), '[REDACTED-JWT]'),
    # Auth headers / bearer
    (re.compile(r'(?i)\bAuthorization\s*:\s*\S+'), 'Authorization: [REDACTED]'),
    (re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._-]+'), 'Bearer [REDACTED]'),
    # Connection strings + basic-auth-in-URL (user:pass@host)
    (re.compile(r'(?i)\b(?:postgres|postgresql|mysql|mongodb|redis|amqp)://\S+'), '[REDACTED-CONN]'),
    (re.compile(r'://[^/\s:@]+:[^/\s@]+@'), '://[REDACTED-AUTH]@'),
    # Keyword = value (broadened: also pass/pwd/key/cred/auth/priv substrings)
    (re.compile(r'(?i)\b([\w-]*(?:password|passwd|pwd|secret|api[_-]?key|token|client[_-]?secret|cred|auth|priv)[\w-]*)\b\s*[=:]\s*\S+'),
     r'\1=[REDACTED]'),
    # Generic .env / shell upper-snake assignment (value-redact) — catches DB_PASS=, APP_KEY=
    (re.compile(r'(?im)^\s*([A-Z][A-Z0-9_]{2,})\s*=\s*\S+'), r'\1=[REDACTED]'),
    # High-entropy hex blobs (>=32 hex chars) — likely keys/hashes
    (re.compile(r'\b[0-9a-fA-F]{32,}\b'), '[REDACTED-HEX]'),
    # Emails: require an alphabetic TLD so version specs like foo@1.2.3 are NOT matched.
    (re.compile(r'\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b'), '[REDACTED-EMAIL]'),
]

# Bound the text we scrub per message so a 20MB single-line tool dump doesn't get
# fully regex-scanned; we only ever emit max_chars, so scanning a modest multiple is enough.
_RAW_SLACK = 4


def scrub(s: str) -> str:
    for rx, rep in SCRUB:
        s = rx.sub(rep, s)
    return s


def _text_from(content: object) -> str:
    """Recursively pull human-readable text out of a message/tool_result content."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text":
            return content.get("text", "")
        # tool_result wrapper or unknown dict that carries nested content
        if "content" in content:
            return _text_from(content["content"])
        return content.get("text", "")
    if isinstance(content, list):
        return "\n".join(_text_from(b) for b in content)
    return ""


def extract_blocks(content: object) -> tuple[str, str]:
    """Return (user_text, tool_text) from a message content, branching on block TYPE
    rather than string-sniffing. NOTE: `tool_use` *input* args are intentionally NOT
    emitted (a command like `export TOKEN=...` in tool input is dropped, not surfaced).
    """
    if isinstance(content, str):
        return content, ""
    user_parts: list[str] = []
    tool_parts: list[str] = []
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                user_parts.append(b.get("text", ""))
            elif t == "tool_result":
                tool_parts.append(_text_from(b.get("content")))
            # tool_use / image / other: intentionally skipped
    elif isinstance(content, dict):
        return extract_blocks([content])
    return "\n".join(p for p in user_parts if p), "\n".join(p for p in tool_parts if p)


def emit(role_tag: str, text: str, max_chars: int) -> None:
    text = text.strip()
    if not text:
        return
    # slice generously, scrub the kept slice, then hard-cut — bounds memory AND
    # guarantees the emitted text is fully scrubbed.
    cap = max(max_chars * _RAW_SLACK, 8000)
    if len(text) > cap:
        text = text[:cap]
    text = scrub(text)
    if len(text) > max_chars:
        text = text[:max_chars] + " …[trunc]"
    print(f"\n[{role_tag}] {text}")


def iter_recent(base: Path, cutoff: float) -> Iterable[Path]:
    paths: list[tuple[float, Path]] = []
    for p in base.glob("*.jsonl"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue  # deleted between glob and stat (TOCTOU) — skip
        if mtime >= cutoff:
            paths.append((mtime, p))
    return [p for _, p in sorted(paths)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcripts_dir")
    ap.add_argument("--days", type=int, default=7, help="recency window in days (>= 0)")
    ap.add_argument("--since-epoch", type=float, default=None, help="mtime >= this epoch (overrides --days)")
    ap.add_argument("--max-chars", type=int, default=2000, help="truncate each message to N chars")
    args = ap.parse_args()

    if args.days < 0:
        print("ERROR: --days must be >= 0", file=sys.stderr)
        return 2

    base = Path(args.transcripts_dir).expanduser()
    if not base.is_dir():
        print(f"ERROR: not a directory: {base}", file=sys.stderr)
        return 2

    cutoff = args.since_epoch if args.since_epoch is not None else (time.time() - args.days * 86400)

    files = iter_recent(base, cutoff)
    if not files:
        print(f"(no transcripts modified since cutoff in {base})", file=sys.stderr)
        return 0

    for path in files:
        print(f"\n########## {path.name} ##########")
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  (could not open {path.name}: {e})", file=sys.stderr)
            continue
        with fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = record.get("message", {}) or {}
                role = msg.get("role") or record.get("type")
                if role not in ("user", "assistant"):
                    continue
                content = msg.get("content")
                if role == "assistant":
                    user_text, _ = extract_blocks(content)
                    emit("ASSISTANT", user_text, args.max_chars)
                else:  # user turn: may carry a real prompt and/or tool results
                    user_text, tool_text = extract_blocks(content)
                    emit("USER", user_text, args.max_chars)
                    emit("TOOL", tool_text, args.max_chars)
    return 0


if __name__ == "__main__":
    sys.exit(main())
