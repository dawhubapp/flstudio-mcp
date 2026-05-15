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
| `FLSTUDIO_VISUAL_GATE=1` | Opt-in to visual-acceptance gates (drives FL Studio UI, requires macOS + FL running + Accessibility perms; ~$0.01 / ~30s per gate) |

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
├── visual_gates.json    # one entry per FullSongCase.visual_gates question (when FLSTUDIO_VISUAL_GATE=1)
├── visual_gates/        # PNGs captured per question for manual review
└── meta.json            # iterations, tokens, cache stats
```

## Visual acceptance gates

`FullSongCase.visual_gates: list[VisualGateExpectation]` declares
yes/no questions the harness asks Claude *after* opening the mutated
FLP in FL Studio. Flow:

1. `autodrive.open_flp(post_path)` cold-boots FL with the mutated
   project (~15 s).
2. `autodrive.screenshot_fl_window(...)` captures FL's frontmost
   window via `screencapture -x`.
3. `visual_gate(client, png, question)` POSTs the PNG to Claude
   (`sonnet-4-6` by default) with a `submit_verdict` tool. Forced
   tool-use → response is always `{passed, rationale, evidence}`.
4. Project closed, FL left running.

Gates are off by default. Set `FLSTUDIO_VISUAL_GATE=1` to enable.
Per-gate cost ~$0.01 / 30 s. Mark a question `must_pass=True` to
escalate gate failure to a test failure (default: surface to judge +
sidecar JSON, don't break the test).

Use for things byte-diffing can't verify cheaply — "does the
playlist actually show clips?", "is the channel rack populated?",
"does the EQ 2 plot look like a low-shelf cut?".

Last 20 runs retained; older auto-pruned.
