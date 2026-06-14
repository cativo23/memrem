# memrem — Design Spec

**Date:** 2026-06-02 · **Revised:** 2026-06-13 (v2, post architecture review)
**Status:** Reviewed — ready to implement v1
**Owner:** Carlos (cativo23)

> **v2 changelog (after platform-correctness review against official Claude Code docs):**
> - **Frontmatter is no longer load-bearing.** Native Claude Code memory is plain markdown with no per-file schema; the prior spec wrongly claimed to "extend existing keys." Scoring/decay/confidence moved to **v1.1**. v1 operates on plain markdown. (Blocker B1)
> - **Ships as a skill only**, not command+skill — commands are merged into skills in current Claude Code; one `SKILL.md` already provides `/memrem`. (Blocker B2)
> - **Secret/PHI scrub now runs on ingest, not just write-back**, with `tool_result` redaction. (Blocker B3)
> - **Propose-then-confirm (dry-run) is the default** write mode. (decision)
> - Install string corrected to `memrem@memrem`; idempotency marker moved to a memrem-owned sidecar.

## 1. What it is

`memrem` is a Claude Code plugin that gives Claude **REM sleep for its long-term memory**. On demand, it reads the native markdown memory and recent session transcripts, then **consolidates**: merges duplicates, resolves contradictions, prunes stale entries, and rebuilds the index — idempotent and safe to re-run, and (by default) showing a plan for approval before it writes.

The name fuses **MEM**ory + **REM** sleep — the brain's consolidation cycle.

## 2. Why (the problem)

Native Claude Code memory accumulates over time: facts duplicate across files, the `MEMORY.md` index drifts from reality, old facts contradict new ones, and files grow past one concern. Today there's no maintenance pass — memory only ever grows messier. `memrem` is that maintenance pass.

## 3. Positioning (where it fits)

| | Capture/recall tools (`claude-mem`, `episodic-memory`) | Anthropic AutoDream / `/dream` | **`memrem`** |
|---|---|---|---|
| Job | Capture + semantically search conversations | Official consolidation (rolling out) | Consolidate the native markdown memory |
| Storage | Own SQLite store (`~/.claude-mem/`, `EPISODIC_MEMORY_CONFIG_DIR`) | Native | **Native markdown** (`~/.claude/projects/<slug>/memory/`) |
| Weight | Heavy (npm/node, MCP server, embeddings) | Built-in | **Lightweight, no DB, human-readable** |
| Trigger | Session hooks | Idle auto + `/dream` | **On-demand `/memrem`** |

**Niche:** grooming the *native, human-readable* markdown memory. Complementary to capture/recall tools (different files, different storage — verified: `claude-mem` writes only to `~/.claude-mem/`, and `obra/episodic-memory` writes to its own SQLite archive under `EPISODIC_MEMORY_CONFIG_DIR`; neither touches the native memory dir. Both *read* the same `*.jsonl` transcripts as memrem, but all read-only — no write contention). The distinct `memrem` namespace avoids any future collision with an official consolidation command (an Anthropic `/dream`/"AutoDream" feature is referenced informally but **could not be verified in current docs** — treated as unverified, not asserted).

This project is built from the general REM-consolidation concept (the same idea Anthropic's AutoDream uses); it does not derive from or credit any third-party implementation.

## 4. Repo structure (marketplace at root + plugin in a subdirectory — documented standard)

Per the official Claude Code marketplace docs, the standard layout — even for a single-plugin repo — is: `marketplace.json` lives at the repo root in `.claude-plugin/`, and the plugin itself lives in a `plugins/<name>/` subdirectory referenced by `source: "./plugins/memrem"`. (Repo-root-as-plugin / `source: "./"` is not a documented convention.) No separate `commands/` dir — in current Claude Code, commands are merged into skills, so a single `SKILL.md` already exposes `/memrem`.

```
memrem/                              # repo = marketplace root, GitHub: cativo23/memrem
├── .claude-plugin/
│   └── marketplace.json             # name + owner + plugins[]; plugins[0].source = "./plugins/memrem"
├── plugins/
│   └── memrem/
│       ├── .claude-plugin/
│       │   └── plugin.json          # name (=skill namespace), description, explicit version, author, license
│       └── skills/
│           └── memrem/
│               ├── SKILL.md         # the consolidation logic — sole entry point, gives /memrem
│               └── scripts/
│                   └── extract_transcripts.py  # bundled: read+scrub JSONL (no /tmp)
├── docs/
│   └── specs/                       # this design doc lives here
├── README.md
└── LICENSE                          # MIT (public marketplace)
```

**SKILL.md frontmatter:** set `disable-model-invocation: true` (this is a side-effecting maintenance action — Claude must not auto-run it because memory "looks messy"; only the user invokes `/memrem`). `allowed-tools: Read, Edit, Write, Glob, Grep, Bash` — note `allowed-tools` is an **auto-approval allowlist, not a sandbox** (verified: every tool stays callable regardless; it only suppresses the prompt). Bash is included deliberately for transcript parsing; the "no exfiltration / no hard-delete" guarantees are enforced by skill instructions + the archive-not-delete design, not by a tool restriction.

**Install constraints:** installed plugins are copied to `~/.claude/plugins/cache` — no `../` references outside the plugin dir, and relative `./plugins/...` sources only resolve when the marketplace is added via Git (not a direct URL to `marketplace.json`). Correct install flow: `/plugin marketplace add cativo23/memrem` → `/plugin install memrem@memrem` → `/reload-plugins`.

## 5. Memory model — plain markdown (v1)

**Verified:** native Claude Code memory is **plain markdown** topic files under `~/.claude/projects/<project>/memory/` plus a `MEMORY.md` index. There is **no native per-file frontmatter schema** — Claude writes and rewrites these files freely during normal sessions. `<project>` is derived from the git repo and shared across worktrees.

**Consequence:** memrem v1 does **not** depend on frontmatter. It operates on the markdown content itself (headings, the `MEMORY.md` pointer lines, file names). Any scoring/decay that needs durable per-fact timestamps is deferred to v1.1, because the only "timestamp" available today is file `mtime`, which Claude bumps constantly → an unreliable staleness proxy.

**v1.1 (not now):** memrem *introduces* (does not "extend") an optional, memrem-owned frontmatter convention for `importance` / `last_confirmed` / `confidence`, treated as best-effort enrichment that degrades gracefully when absent. Until enough real runs have written it, decay-based pruning stays off. Durable timestamps will come from a memrem sidecar ledger or git history of the memory dir — never from `mtime`.

## 6. Consolidation flow (v1)

Four phases. v1 stays on plain markdown; decay-pruning (the old Phase 4 scoring) is deferred to v1.1.

**Phase 1 — Orient.** Read the memory dir, `MEMORY.md`, and `_archive/` tombstone headers into working context. Handle the empty/first-run case (no `MEMORY.md` → create a minimal one; don't crash). Snapshot file mtimes for the concurrency guard (see §8a).

**Phase 2 — Gather signal.** Scan recent session transcripts (since the sidecar `last_consolidated` marker; default last 7 days) for new facts, corrections, decisions, preferences, using the **bundled `scripts/extract_transcripts.py`** (referenced via `${CLAUDE_SKILL_DIR}` — verified: that variable resolves the skill's own dir regardless of cwd) rather than writing a parser to `/tmp`. Skip anything matching an archived `contradicted | rejected` tombstone (anti-resurrection; exact/normalized match in v1). **Scrub runs on ingest, before content enters reasoning** — the script does mechanical credential/email/hex redaction; PHI judgment (names, diagnoses) stays with the model. See §9.

**Phase 3 — Consolidate.**
- Merge duplicates; convert relative dates → absolute.
- **Recency-wins invalidation:** on contradiction, keep the new fact and move the loser to `_archive/` with a `superseded_by:` back-link. **Never silently overwrite.**
- Deterministic ordering of merged entries (stable sort) so re-runs are byte-stable.

**Phase 4 — Index & archive.**
- Rebuild `MEMORY.md` as a lean index — target **< 200 lines AND < 25 KB** (verified: only the first 200 lines or 25 KB of `MEMORY.md` load at session start; the rest is dead weight).
- Move pruned/redundant entries to `_archive/` with a tombstone (never hard-delete).
- **No-op guard:** if nothing changed, do not re-stamp or rewrite files (preserves idempotency — a second run with no intervening session produces a byte-identical dir).
- Update the `last_consolidated` marker in the memrem **sidecar** (`_memrem/state.json`), *not* in `MEMORY.md` (which gets rebuilt) and *not* in a memory file (which Claude may clobber).

**Deferred to v1.1:** effective-score decay `importance * e^(-rate * days_since_last_used)`, the `type: user` / `importance >= 9` decay exemptions, and `confidence`-based conflict resolution. These need durable per-fact timestamps that don't exist yet (§5).

## 7. Archive & tombstone policy

- Pruned memories **move to `_archive/`** (never hard-deleted) with appended frontmatter: `archived: <date>`, `reason: stale | contradicted | redundant | rejected`, `superseded_by:` if merged.
- **Tombstones are the load-bearing mechanic:** Phases 2 & 3 read archive headers and never re-ingest a fact archived as `contradicted | rejected`. This is what makes the skill idempotent — without it, archiving is theater.
- **Bounding the archive:** `contradicted | rejected` tombstones are kept permanently but header-only (body stripped after 90 days). `stale | redundant` archives expire after 180 days. Since memrem only runs on manual `/memrem`, "after N days" is evaluated **on the next invocation past that age** — there is no background timer.
- **Hard delete only on explicit user request** (privacy / credential redaction carve-out).

## 8. Trigger model

**v1: manual only.** Runs when the user types `/memrem`. No hooks, nothing in `settings.json`. `disable-model-invocation: true` so Claude never auto-runs it.

Explicitly **rejected for v1:** a background headless agent with pre-authorized shell access (the dangerous auto-consolidation pattern). Not shipped.

Documented as opt-in for **later:** a lightweight "it's been a while, consider consolidating" reminder flag — never runs consolidation unattended; the user still triggers it.

### 8a. Write mode — apply by default, confirm by flag

- **`/memrem`** (default): runs the full flow and **applies** changes directly. Safe to do so because the operation is **archive-not-delete** and the memory dir is typically git-recoverable; nothing is destroyed.
- **`/memrem --confirm`** (alias `--dry-run`): runs the flow read-only, prints the **plan** (what it would merge / archive / rebuild, as a diff/summary), and writes nothing until the user approves. Opt-in for when the user wants a checkpoint.
- **Concurrency guard:** mtimes are snapshotted in Phase 1; before writing, if any memory file changed underneath (another session wrote memory mid-run), **abort with a message** rather than clobber. Full locking is out of scope for v1 — detect-and-bail only.

## 9. Security

- **Scrub on ingest AND write-back.** Transcripts are JSONL containing verbatim `tool_result` blocks (env dumps, `.env` contents, connection strings, API responses) — and, given the owner's healthcare context, **PHI**. v1 **ingests `tool_result` with redaction** (the chosen tradeoff: more signal, robust scrub), then **re-scrubs at the write boundary** (defense in depth):
  - Redact known credential shapes: OpenAI `sk-/sk_`, Stripe `sk_/rk_/pk_live|test`, Slack `xox…`, GitHub `ghp_/github_pat_`, SendGrid `SG.`, Mailgun, AWS `AKIA`/secret-key, Google `AIza`, JWTs, PEM private-key blocks, `Authorization`/`Bearer`, connection strings, basic-auth-in-URL, `.env`-style `UPPER_SNAKE=` assignments, and high-entropy hex. **Best-effort, not a guarantee** — the README states this plainly.
  - **PHI guard** (owner's healthcare-data rules): never write patient identifiers — names, IDs, diagnoses, DOB — into memory, even in a derived/summarized form.
  - Redaction happens **before** content enters consolidation reasoning, so secrets/PHI never reach a memory file even indirectly.
- **Never echo raw transcript content back to the user** in the plan output (the dry-run plan summarizes facts, not raw tool output).
- The skill ships **logic only** — never the user's actual memory content. The repo is public; memory stays local.
- No network calls, no telemetry, no `curl | bash`, no headless/background execution, no hard-delete, no writes outside the memory dir. Local shell (`git`/`ls`/`grep`/`python3`) is used for *read-only analysis* of transcripts only. Note: `allowed-tools` is an auto-approval allowlist, **not** a sandbox — these guarantees are enforced by the skill's instructions and the archive-not-delete design, not by tool gating. A user wanting a hard shell block can add `permissions.deny: ["Bash"]` in their own `settings.json`, but the skill does not depend on it.

## 10. Distribution

- **Primary:** official Claude Code marketplace. `/plugin marketplace add cativo23/memrem` → `/plugin install memrem@memrem` → `/reload-plugins`.
- **Secondary:** listing on skills.sh (third-party registry with security scanning). Exact submission mechanism to be confirmed at publish time — not asserted yet.

## 11. Roadmap (explicitly NOT in v1)

Documented so scope stays honest:
- **Scoring + decay-based pruning (v1.1)** — memrem-owned optional frontmatter (`importance`, `last_confirmed`, `confidence`), effective-score decay with `type: user` / `importance >= 9` exemptions (catastrophic-forgetting safeguard), durable timestamps from a sidecar ledger or git history. Deferred because native memory has no frontmatter today and `mtime` is an unreliable staleness proxy.
- **Reflection / synthesis** — derive higher-level insights from clusters of facts (high value, but risks compounding hallucination; ship once raw-fact layer is trusted).
- **Memory linking (Zettelkasten)** — `links:` between related files; pays off once the corpus is large.
- **Hierarchical topic summaries** — `topics/<topic>.md` rollups when a topic exceeds N facts.

## 12. v1 scope (locked)

**In:** name `memrem`; marketplace-at-root + plugin in `plugins/memrem/` (`source: "./plugins/memrem"`, the documented standard); skill-only entry (`SKILL.md`, `disable-model-invocation: true`, `allowed-tools` without Bash); 4-phase flow **on plain markdown**; dedup; recency-wins invalidation; anti-resurrection + no-op idempotency guard; archive-with-tombstone; lean `MEMORY.md` rebuild (< 200 lines AND < 25 KB); **scrub-on-ingest with `tool_result` redaction + PHI guard**; **apply-by-default, `--confirm`/`--dry-run` flag**; concurrency detect-and-bail; sidecar `_memrem/state.json` for the `last_consolidated` marker; public repo at `~/projects/personal/memrem`; MIT license.

**Out:** scoring/decay/`confidence` (→ v1.1); reflection; linking; hierarchical summaries; any auto/background trigger; any vector DB; any reliance on native per-file frontmatter.

## 13. Success criteria

- Running `/memrem` on a messy memory dir produces: fewer duplicates, a `MEMORY.md` index that matches reality (< 200 lines AND < 25 KB), redundant/stale facts archived (not lost), and no contradicted facts resurrected on a second run (idempotent — second run with no intervening session is a byte-identical no-op).
- No secret or PHI ever lands in a memory file, even derived.
- Identity facts (the `user`-type memories) survive consolidation.
- `/memrem --confirm` writes nothing until approved.
- Installs cleanly via `/plugin marketplace add cativo23/memrem` + `/plugin install memrem@memrem`.
