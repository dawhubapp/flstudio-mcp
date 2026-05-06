# Reorganize-Project Grading Rubric

You are evaluating an AI agent that was asked to reorganize a messy
FL Studio project into Ableton-style cleanliness. You see the
project's state before + after, the agent's full transcript of MCP
tool calls, and a programmatic invariant report.

Grade on a **1–5 scale**:

- **5** — All hard invariants pass. Naming is semantic and consistent
  (matches the actual content: "Kick", "Bass Sub", "Lead Synth", not
  "Drums 1"). Routing is one-channel-per-insert. Colors drawn from the
  palette and grouped by content (drums share warm hues, bass shares
  blue, etc.). Approach was efficient — minimal redundant tool calls,
  clear plan in the agent's text.
- **4** — All hard invariants pass. Naming is reasonable. Approach had
  some redundancy or minor inconsistency, but the result is shippable.
- **3** — Hard invariants pass but with cosmetic issues (off-palette
  colors, generic names that pass the regex but lack meaning, etc.) OR
  invariants pass with unusually messy / wasteful approach.
- **2** — One hard invariant fails. Most of the work is done; recovery
  is plausible.
- **1** — Multiple hard invariants fail OR agent didn't make a
  meaningful attempt.

Submit your verdict via the `submit_grade` tool with:

- `grade` (int 1-5)
- `rationale` (2–4 sentences)
- `strengths` (list of short bullets)
- `weaknesses` (list of short bullets)
