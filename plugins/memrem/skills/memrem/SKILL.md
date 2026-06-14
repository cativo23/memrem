---
name: memrem
description: REM sleep for Claude's native memory. Consolidates the markdown memory dir — merges duplicates, resolves contradictions, archives stale facts with tombstones, and rebuilds a lean MEMORY.md index. Idempotent and safe to re-run. Invoke explicitly with /memrem:memrem (the short /memrem also works when unambiguous); pass --confirm or --dry-run to review a plan before any write.
disable-model-invocation: true
allowed-tools: Read, Edit, Write, Glob, Grep, Bash
---

# memrem — memory consolidation

You are running a **memory consolidation pass** ("REM sleep") over Claude Code's native
markdown memory for the current project. You groom the memory dir so it stays lean,
non-contradictory, and matched to reality — **without losing anything** (archive, never
hard-delete).

## Scope & safety contract (read first)

- **Operate ONLY** on this project's native memory: the `memory/` directory and `MEMORY.md`
  index inside it, plus a memrem-owned sidecar `memory/_memrem/` and archive `memory/_archive/`.
  Touch nothing else in the repo.
- **Never hard-delete** a memory. Pruning means *moving to `_archive/` with a tombstone*.
- **Never write secrets or PHI** into any memory file (see Phase 2 scrub). This is absolute.
- **Mode (apply vs confirm).** This run was invoked with arguments: `$ARGUMENTS`. If those
  arguments include `--confirm` or `--dry-run`, run in **confirm/dry-run mode**: do everything
  read-only, present a PLAN, then stop and wait for approval. Otherwise (no such flag), run in
  **apply mode** — apply changes directly (the operation is archive-not-delete and the dir is
  typically git-recoverable, so direct apply is safe).
- **No network, no telemetry, no exfiltration.** Local shell is allowed for *analysis only*
  (`git log`, `ls`, `grep`, `python3` to read/parse transcripts) — but **never** for network
  calls, installing anything, hard-deleting memory, or writing outside the memory dir.

## Locating the memory dir

The native memory dir for the current project is at:
`~/.claude/projects/<project-slug>/memory/`

where `<project-slug>` is the current working directory path with `/` replaced by `-`
(e.g. `/home/me/projects/app` → `-home-me-projects-app`; note the **leading dash** because
the absolute path starts with `/`). The index is `MEMORY.md` in that
dir; topic files are sibling `.md` files. If you cannot resolve the slug, ask the user to
confirm the path rather than guessing. If the dir or `MEMORY.md` does not exist, treat it as
an empty first run (Phase 1 handles this).

---

## Phase 1 — Orient

1. Resolve the memory dir. If `MEMORY.md` is absent, this is a **first/empty run**: there is
   nothing to consolidate yet — create a minimal `MEMORY.md` if missing, write the sidecar
   marker (see shape below), and report "nothing to consolidate." Do not scan transcripts on
   a truly empty dir.
2. Read every `*.md` in the memory dir (skip `_archive/` and `_memrem/` for *content*, but
   read `_archive/` tombstone **headers** — frontmatter only — into context).
3. Read the sidecar `memory/_memrem/state.json` if present. Its shape is:
   ```json
   {"last_consolidated": "2026-06-13", "last_consolidated_epoch": 1781740800}
   ```
   Note `last_consolidated_epoch` — you pass it to the extractor in Phase 2. If the file is
   absent (first run), default the transcript window to the last 7 days (`--days 7`).
4. **Snapshot** the path + mtime of every memory file you read. You will re-check these
   before writing (concurrency guard): if any changed underneath you, abort with a message
   rather than clobber another session's write.

## Phase 2 — Gather signal (scrub on ingest)

Goal: find new facts, corrections, decisions, and preferences from recent sessions that
should be reflected in memory.

1. **Extract + scrub transcripts using the bundled script** (do NOT write your own parser to
   `/tmp`). The skill ships `scripts/extract_transcripts.py`, which reads the project's
   `*.jsonl` within a recency window, extracts USER / ASSISTANT / (scrubbed) tool-result
   text, and redacts secrets on ingest. Invoke it via the skill-dir variable so it resolves
   regardless of cwd:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/extract_transcripts.py" \
     ~/.claude/projects/<project-slug>/ --days 7
   ```

   When a `last_consolidated_epoch` exists in `state.json`, pass it as
   `--since-epoch <epoch>` instead of `--days 7`, so the window starts exactly there.
   (Pass the **epoch number**, not the ISO date — `--since-epoch` takes epoch seconds.)
   The script does **best-effort mechanical redaction of common credential shapes** (API keys,
   provider tokens, JWTs, PEM blocks, connection strings, `.env`-style assignments,
   `Authorization`/`Bearer`, high-entropy hex, emails) before any content reaches you. It is
   **not a guarantee** — stay alert for anything secret-like the regexes missed and drop it.

2. **PHI judgment is yours** (the script can't regex names/diagnoses). On top of the script's
   output, never carry into memory: patient names, patient IDs, diagnoses, dates of birth,
   national IDs, or contact info — even summarized or derived. When in doubt, drop it. **Keep
   a running count** of secret/PHI candidates you drop, for the final summary — never store
   the content itself anywhere.

3. **Anti-resurrection:** discard any candidate fact that matches a tombstone in `_archive/`
   marked `reason: contradicted` or `reason: rejected`. **"Match" = lexical, not semantic:**
   case-insensitive comparison after collapsing internal whitespace to single spaces and
   stripping leading/trailing punctuation. Do NOT do fuzzy/semantic matching — if the wording
   differs materially, treat it as a new fact (a human can re-archive it). Those tombstones
   were deliberately invalidated; do not bring them back.

4. Produce a clean list of candidate facts with their target memory type
   (`user | feedback | project | reference`).

> **No stray temp files.** Use the bundled script (or `Read`/`Grep` directly). Do not write
> helper scripts or extracted content to `/tmp` or anywhere outside the memory dir. If you
> ever need a scratch file, put it under `memory/_memrem/` and remove it before finishing.

## Phase 3 — Consolidate

Working on the in-memory set of existing facts + clean candidates:

1. **Merge duplicates.** Same fact across files → one canonical entry. Prefer the most
   complete phrasing. Convert relative dates ("yesterday", "next week") to absolute dates.
2. **Recency-wins invalidation.** When a new fact contradicts an existing one: keep the new
   fact, and **move the old one to `_archive/`** with a tombstone (`reason: contradicted`,
   `superseded_by:` pointing at the winner). **Never silently overwrite** — the loser always
   leaves a tombstone.
3. **Redundancy.** A fact fully subsumed by another → archive the redundant one
   (`reason: redundant`).
4. **Determinism** (so two runs — or two people — produce byte-identical output):
   - All dates ISO-8601 (`YYYY-MM-DD`).
   - Sort entries within each file by their `name`/slug, ascending.
   - Every file ends with exactly one trailing newline.
5. Preserve each file's existing frontmatter keys (`name`, `description`, `metadata.type`)
   exactly. **Do not invent new frontmatter keys** in v1 (scoring/decay is v1.1).

## Phase 4 — Index & archive

1. **Write-back re-scrub (defense in depth).** Before writing ANY memory file, run the same
   credential redaction over the candidate text one more time. The ingest scrub is the first
   gate; this is the second, at the write boundary. PHI still rides on your judgment (step
   Phase 2.2) — do not write patient identifiers even if they slipped the regexes.
2. **Rebuild `MEMORY.md`** as a lean index: one line per active memory
   (`- [Title](file.md) — one-line hook`). **Group order is fixed:** `user`, then `feedback`,
   then `project`, then `reference`, then any other types alphabetically. Within a group, sort
   by title ascending. One blank line between groups, none between entries.
   **Hard targets: < 200 lines AND < 25 KB** (only the first 200 lines / 25 KB load at session
   start — anything beyond is dead weight). If you exceed it, the memory set itself is too
   large → flag it, don't pad.
3. **Apply archives** decided in Phase 3 into `memory/_archive/`. Each tombstone keeps its
   frontmatter and appends: `archived: <date>`, `reason: stale|contradicted|redundant|rejected`,
   and `superseded_by:` if merged.
4. **Archive bounding** (evaluated on this run, since there is no background timer):
   - `contradicted | rejected` tombstones: keep permanently, but strip the body (header-only)
     once older than 90 days.
   - `stale | redundant` tombstones: remove once older than 180 days.
5. **No-op guard.** If consolidation produced no changes, do **not** rewrite files or re-stamp
   anything — leave the dir byte-identical (this is what makes a second run idempotent).
6. **Concurrency re-check, then batch-write.** Re-stat the files snapshotted in Phase 1
   **once, immediately before the first write**. If any mtime changed, **abort before writing**
   and tell the user another session modified memory mid-run. If clean, perform all writes in
   one batch without re-stating between them.
7. Update `memory/_memrem/state.json` with both the new `last_consolidated` (ISO date) and
   `last_consolidated_epoch` (epoch seconds). This marker lives in the sidecar — never in
   `MEMORY.md` (rebuilt) or a memory file (Claude may clobber it).

---

## Output

After applying (or, in `--confirm` mode, as the PLAN), report a concise summary:

- **Merged:** N duplicate sets
- **Contradictions resolved:** N (old → archived, new kept)
- **Archived:** N (redundant/stale) — with reasons
- **Index:** MEMORY.md rebuilt → X lines / Y KB
- **Scrubbed:** N secrets/PHI candidates dropped on ingest (count only — never echo the content)
- **Idempotent:** "no changes" if it was a no-op run

In `--confirm` / `--dry-run` mode, end with: "No changes written. Reply to approve and I'll
apply this plan." and wait.

## Edge cases

- **Empty / first run:** nothing to consolidate; create minimal `MEMORY.md` + sidecar; report.
- **Concurrent write detected:** abort before writing, report which file changed.
- **Memory dir not found:** ask the user to confirm the path; do not guess or create it
  somewhere arbitrary.
- **Everything already clean:** no-op; say so; do not churn files.
