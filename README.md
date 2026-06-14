# memrem

> **REM sleep for Claude Code's memory.** On demand, `memrem` consolidates Claude's native
> markdown memory — merges duplicates, resolves contradictions, archives stale facts with
> tombstones, and rebuilds a lean `MEMORY.md` index. Idempotent and safe to re-run.

The name fuses **MEM**ory + **REM** sleep — the brain's consolidation cycle.

## Why

Claude Code's native memory accumulates over time: facts duplicate across files, the
`MEMORY.md` index drifts from reality, old facts contradict new ones, and files grow past
one concern. There's no built-in maintenance pass — memory only ever gets messier. `memrem`
**is** that maintenance pass.

It grooms the *native, human-readable* markdown memory at
`~/.claude/projects/<project>/memory/`. It does not capture, embed, or store anything in a
database — it just keeps what's already there clean.

## Install

```
/plugin marketplace add cativo23/memrem
/plugin install memrem@memrem
/reload-plugins
```

## Use

```
/memrem:memrem            # consolidate and apply changes directly (archive-not-delete, safe)
/memrem:memrem --confirm  # read-only: show a plan first, write nothing until you approve
                          # (--dry-run works too)
```

> The canonical command is namespaced `/memrem:memrem` (plugin skills always are). The short
> `/memrem` also works when no other skill shares the name.

## What it does (4 phases)

1. **Orient** — read the memory dir, `MEMORY.md`, and archive tombstones.
2. **Gather signal** — scan recent session transcripts for new facts/corrections/decisions,
   **scrubbing secrets and PHI on ingest** before anything enters reasoning.
3. **Consolidate** — merge duplicates, resolve contradictions (recency wins; the loser is
   archived with a `superseded_by` back-link, never silently overwritten).
4. **Index & archive** — rebuild a lean `MEMORY.md` (< 200 lines / < 25 KB), move pruned
   entries to `_archive/` with tombstones, update the idempotency marker.

## Safety

- **Never hard-deletes** — pruning moves to `_archive/` with a tombstone.
- **Scrubs secrets on ingest** — best-effort mechanical redaction of common credential
  shapes (API keys, provider tokens, JWTs, PEM blocks, `.env` assignments, connection
  strings) plus a second re-scrub at the write boundary. It's defense-in-depth, not a
  guarantee — that's why `--confirm` exists: review the plan before applying. PHI (patient
  names, diagnoses) is handled by the model's judgment, never written to memory.
- **Idempotent** — a second run with no intervening session is a byte-identical no-op.
- **Anti-resurrection** — facts you deliberately rejected/contradicted don't come back.
- **No network, no telemetry, no exfiltration** — runs entirely on your machine. Local shell
  is used only to read/parse your transcripts for analysis; it never makes network calls,
  installs anything, or writes outside the memory dir. The repo ships logic, not your memory.

## Scope

**v1** operates on plain markdown (dedup, contradiction resolution, index rebuild,
archive-with-tombstone). Importance scoring and decay-based pruning are planned for **v1.1**
(they need durable per-fact metadata that Claude's native memory doesn't carry yet).

## License

MIT © Carlos Cativo
