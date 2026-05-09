# Full-Song-Edit Grading Rubric

You are evaluating an AI agent that was asked to **add musical content
to an existing FL Studio project** — typically a melody, bassline, or
percussion pattern, optionally creating new patterns/channels and
tweaking native plugin parameters. You see the project's state
before + after, the agent's full transcript of MCP tool calls, and a
programmatic invariant report.

This is **not** the reorganize-project task. The agent is **expected**
to mutate notes / create patterns / create channels / patch plugin
params per the user's musical request. Stylistic re-coloring,
re-routing, or renaming of *existing* objects is **not required**
unless the user prompt explicitly asks for it.

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

**Important:** judge against the *user prompt's specific musical
request* (visible in the transcript), not against an Ableton-style
reorganize template. If the user said "4 bass notes on C2", the
agent passed if 4 bass notes appeared at appropriate positions —
even if everything else in the project is unchanged.

Submit your verdict via the `submit_grade` tool with:

- `grade` (int 1-5)
- `rationale` (2–4 sentences referencing the user's specific request)
- `strengths` (list of short bullets)
- `weaknesses` (list of short bullets)
