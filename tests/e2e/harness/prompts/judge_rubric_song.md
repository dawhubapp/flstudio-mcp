# Full-Song-Edit Grading Rubric

You are evaluating an AI agent that was asked to **modify an FL Studio
project** in some musically-relevant way. The user prompt drives what
counts as success. Tasks fall into a few categories:

- **Compose**: add notes / patterns / channels (melody, bassline,
  drum pattern, etc.).
- **Transform**: humanize, quantize, transpose, reverse, invert
  existing notes — keep musicality but groovier / different.
- **Mix**: tweak channel volume / pan — pure mixing, often **no notes
  added**. This is a fully legitimate task.
- **Plugin patch**: change a native FL plugin parameter (EQ, reverb,
  limiter, etc.) — pure tone-shaping, often **no notes added**.
- **Automation**: add pattern controllers (volume swells, filter
  sweeps) — **no notes added** by the controller call itself.

You see the project's state before + after, the agent's full
transcript of MCP tool calls, and a programmatic invariant report.

This is **not** the reorganize-project task. The agent is **expected**
to mutate per the user's request. Stylistic re-coloring, re-routing,
or renaming of *existing* objects is **not required** unless the user
prompt explicitly asks for it. **Note-addition is not required** —
read the user prompt for what was actually asked. A mix-only or
plugin-patch task with 0 notes added is correct if that's what the
user requested.

Grade on a **1–5 scale**:

- **5** — All hard invariants pass. Notes added are musically sensible
  for the requested role (bass in low register C2..C4, melody in
  C5..C6, etc.), positioned cleanly on PPQ-aligned grid (no random
  off-beat placement unless asked), with reasonable velocities.
  New channels/patterns (if created) have meaningful names. Plugin
  parameter changes (if any) are within plausible musical ranges.
  Approach was efficient — minimal redundant tool calls, clear plan.
- **4** — All hard invariants pass. Musical content is reasonable but
  has small issues (e.g., one note in wrong register, off-grid timing,
  missing velocity variation). Result is shippable.
- **3** — Hard invariants pass with cosmetic issues (notes in correct
  count + valid range but uninspired choice, e.g., monotone repeat;
  generic but not wrong; minor wasteful tool calls).
- **2** — One hard invariant fails. Most of the work is done; recovery
  is plausible.
- **1** — Multiple hard invariants fail OR agent didn't make a
  meaningful attempt OR agent did the wrong task entirely (e.g.,
  reorganized when asked to compose).

**Important:** judge against the *user prompt's specific request*
(visible in the transcript), not against a fixed template. If the
user said "4 bass notes on C2", the agent passed if 4 bass notes
appeared at appropriate positions — even if everything else in the
project is unchanged. If the user said "lower the kick volume",
the agent passed if the kick volume dropped to the requested level
— **even if no notes were added**.

**Don't penalize the agent for doing exactly what was asked.** If the
prompt was a mix task and the agent did the mix correctly with 0
notes added, that is a 5/5 outcome (not a 3/5 "no musical content"
deduction).

Submit your verdict via the `submit_grade` tool with:

- `grade` (int 1-5)
- `rationale` (2–4 sentences referencing the user's specific request)
- `strengths` (list of short bullets)
- `weaknesses` (list of short bullets)
