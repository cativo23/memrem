# memrem — Design Spec

**Date:** 2026-06-02
**Status:** Draft for review
**Owner:** Carlos (cativo23)

## 1. What it is

`memrem` is a Claude Code plugin that gives Claude **REM sleep for its long-term memory**. On demand, it reads the native markdown memory and recent session transcripts, then **consolidates**: merges duplicates, resolves contradictions, prunes stale entries, and rebuilds the index — with provenance, importance-based decay, and idempotency so it's safe to re-run.

The name fuses **MEM**ory + **REM** sleep — the brain's consolidation cycle.

## 2. Why (the problem)

Native Claude Code memory accumulates over time: facts duplicate across files, the `MEMORY.md` index drifts from reality, old facts contradict new ones, and files grow past one concern. Today there's no maintenance pass — memory only ever grows messier. `memrem` is that maintenance pass.

## 3. Positioning (where it fits)

| | Capture tools (`claude-mem`, `claude-memory-compiler`) | Anthropic AutoDream / `/dream` | **`memrem`** |
|---|---|---|---|
| Job | Capture everything + retrieve | Official consolidation (rolling out) | Consolidate the native markdown memory |
| Storage | Own SQLite store (`~/.claude-mem/`) | Native | **Native markdown** (`~/.claude/projects/<slug>/memory/`) |
| Weight | Heavy (npm, tree-sitter, Agent SDK) | Built-in | **Lightweight, no DB, human-readable** |
| Trigger | Session hooks | Idle auto + `/dream` | **On-demand `/memrem`** |

**Niche:** grooming the *native, human-readable* markdown memory. Complementary to capture tools (different files, different storage — verified: `claude-mem` writes only to `~/.claude-mem/`, never to the native memory dir). Distinct command name avoids collision with the upcoming official `/dream`.

This project is built from the general REM-consolidation concept (the same idea Anthropic's AutoDream uses); it does not derive from or credit any third-party implementation.

## 4. Repo structure (plugin + its own marketplace, one repo)

```
memrem/
├── .claude-plugin/
│   ├── marketplace.json     # enables: /plugin marketplace add cativo23/memrem
│   └── plugin.json          # plugin metadata (name, version, author, description)
├── commands/
│   └── memrem.md            # the /memrem slash command (entry point)
├── skills/
│   └── memrem/
│       └── SKILL.md         # the 4-phase consolidation logic + v1 additions
├── docs/
│   └── specs/               # this design doc lives here
├── README.md
└── LICENSE                  # MIT (public marketplace)
```

## 5. Memory model — frontmatter schema

`memrem` extends the existing memory frontmatter. Existing keys (`name`, `description`, `metadata.type`) are preserved; new keys are additive and optional (a memory missing them is treated with safe defaults, then stamped on next run).

```yaml
---
name: <kebab-slug>
description: <one-line>
metadata:
  type: user | feedback | project | reference
  # --- memrem additions ---
  created: 2026-06-02            # first seen (absolute date)
  last_confirmed: 2026-06-02     # last session that re-asserted this fact
  last_used: 2026-06-02          # last time the fact was surfaced/recalled
  source: session | file | manual
  importance: 1-10               # LLM-rated salience
  confidence: high | medium | low
---
```

**Defaults when absent:** `created`/`last_confirmed` = file mtime; `importance` = 5; `confidence` = medium; `source` = manual.

## 6. Consolidation flow

Four phases, plus the v1 intelligence layer woven in.

**Phase 1 — Orient.** Read the memory dir, `MEMORY.md`, and `_archive/` tombstone headers into working context.

**Phase 2 — Gather signal.** Scan recent session transcripts (since `last_consolidated` marker; default last 7 days) for new facts, corrections, decisions, preferences. Skip anything matching an archived `contradicted|rejected` tombstone (anti-resurrection). Run **secret scrub** here: never promote tokens, passwords, API keys, or credentials found in transcripts into memory.

**Phase 3 — Consolidate.**
- Merge duplicates; convert relative dates → absolute.
- **Recency-wins invalidation:** on contradiction, write/keep the new fact and move the loser to `_archive/` with `superseded_by:` back-link. Never silently overwrite.
- Resolve conflicts by `confidence`, then recency.
- Stamp `last_confirmed` on facts a session re-asserted.

**Phase 4 — Prune & index.**
- Compute effective score: `importance * e^(-rate * days_since_last_used)`.
- Low-score → archive (not delete). **Exempt `type: user` and `importance >= 9` from decay-based pruning** (catastrophic-forgetting safeguard) — those only leave via explicit invalidation or user request.
- Rebuild `MEMORY.md` as a lean index (target < 200 lines).
- Write a `last_consolidated` session marker so transcripts aren't re-scanned next run (idempotency).

## 7. Archive & tombstone policy

- Pruned memories **move to `_archive/`** (never hard-deleted) with appended frontmatter: `archived: <date>`, `reason: stale | contradicted | redundant | rejected`, `superseded_by:` if merged.
- **Tombstones are the load-bearing mechanic:** Phases 2 & 3 read archive headers and never re-ingest a fact archived as `contradicted | rejected`. This is what makes the skill idempotent — without it, archiving is theater.
- **Bounding the archive:** `contradicted | rejected` tombstones are kept permanently but header-only (body stripped after 90 days). `stale | redundant` archives auto-expire after 180 days (no anti-resurrection value once the active set has moved on).
- **Hard delete only on explicit user request** (privacy / credential redaction carve-out).

## 8. Trigger model

**v1: manual only.** Runs when the user types `/memrem`. No hooks, nothing in `settings.json`. Simplest and safest.

Explicitly **rejected for v1:** a background headless agent with pre-authorized shell access (the dangerous auto-consolidation pattern). Not shipped.

Documented as opt-in for **later:** a lightweight "it's been a while, consider consolidating" reminder flag — never runs consolidation unattended; the user still triggers it.

## 9. Security

- **Secret scrub** (Phase 2): credentials/tokens/passwords in transcripts are never written to memory. Aligns with security-first rules.
- The skill ships **logic only** — never the user's actual memory content. The repo is public; the memory stays local on the user's machine.
- No network calls, no telemetry, no `curl | bash`, no headless shell execution.

## 10. Distribution

- **Primary:** official Claude Code marketplace. Users run `/plugin marketplace add cativo23/memrem` then `/plugin install memrem`.
- **Secondary:** listing on skills.sh (third-party registry with security scanning). Exact submission mechanism to be confirmed at publish time — not asserted yet.

## 11. Roadmap (explicitly NOT in v1)

Documented so scope stays honest:
- **Reflection / synthesis** — derive higher-level insights from clusters of facts (high value, but risks compounding hallucination; ship once raw-fact layer is trusted).
- **Memory linking (Zettelkasten)** — `links:` between related files; pays off once the corpus is large.
- **Hierarchical topic summaries** — `topics/<topic>.md` rollups when a topic exceeds N facts.

## 12. v1 scope (locked)

In: name `memrem`; plugin + marketplace repo; 4-phase flow; provenance frontmatter; recency-wins invalidation; anti-resurrection/idempotency guard; importance + decay; catastrophic-forgetting safeguard; optional `confidence`; archive-with-tombstone; secret scrub; manual `/memrem` trigger; public repo at `~/projects/personal/memrem`; MIT license.

Out: reflection, linking, hierarchical summaries, any auto/background trigger, any vector DB.

## 13. Success criteria

- Running `/memrem` on a messy memory dir produces: fewer duplicates, a `MEMORY.md` index that matches reality (< 200 lines), stale facts archived (not lost), and no contradicted facts resurrected on a second run (idempotent).
- No secret ever lands in a memory file.
- `type: user` identity facts survive aggressive consolidation.
- Installs cleanly via `/plugin marketplace add`.
