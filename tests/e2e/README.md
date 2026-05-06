# E2E LLM Test Harness

Drives a Claude API tool-use loop against the flstudio-mcp server over real
stdio and grades the result. Two flows:

1. **Reorganize** — Ableton-style cleanup of a messy FLP.
2. **Full song edits** — add a melody, change a bassline, tweak plugin params.
   (Gated on Phase F2 mutations shipping.)

## Layout

- `harness/` — shared modules (client, agent loop, invariants, judge, state capture, reporting).
- `cases/` — per-flow test case definitions (input FLP, prompt, expected invariants).
- `unit/` — self-tests for the harness; **no API key required**, run in normal CI.
- `fixtures/` — committable synthetic FLPs.
- `runs/` — gitignored per-test artifact dump (transcript, before/after info, diff, judge).

## Running

```bash
# Self-tests (cheap, no API spend, default CI):
cd mcp && uv run pytest tests/e2e/unit/ -v

# E2E smoke (~$0.05 per run):
export FLSTUDIO_MCP_E2E=1
export ANTHROPIC_API_KEY=...
cd mcp && uv run pytest tests/e2e/test_reorganize_e2e.py::test_smoke -v

# Full local run (~$0.40):
make e2e-full
```

## Gates

| Env var | Purpose |
|---|---|
| `FLSTUDIO_MCP_E2E=1` | Opt-in to API-burning tests |
| `ANTHROPIC_API_KEY` | Required for agent + judge calls |
| `FLSTUDIO_MCP_BRIDGE_CMD` | Pin to dev `flpdiff/` checkout (auto-propagated) |

## Cost expectations

| Case | Iterations | Cost | Runtime |
|---|---|---|---|
| `synthetic_smoke` | ≤10 | ~$0.05 | ~30s |
| `bass_sketch_real` | ≤30 | ~$0.40 | ~3min |
| `full_song_synthetic` | ≤30 | ~$0.30 | ~3min |

Prompt caching (system + tool defs) drops per-iteration cost ~70%.

## Failure modes

- **Bridge wedge** — surfaced as `terminated="infra"` in the run report; server stdout/stderr captured under `runs/<id>/server.log`. Distinct from LLM failures.
- **Iteration cap hit** — invariants run anyway; partial credit reported. Test fails with the missing-invariant list.
- **Invalid args** — bridge's `ALLOWED_ARGS` whitelist catches; transcript records the rejection trail.

## Per-run artifacts

```
runs/<test-id>-<timestamp>/
├── transcript.jsonl     # every tool call + agent turn
├── before.flp.info.json # flpdiff info --format json
├── after.flp.info.json
├── diff.txt             # flpdiff diff --verbose
├── invariants.json
├── judge.json
└── meta.json            # iterations, tokens, cache stats
```

Last 20 runs retained; older auto-pruned.
