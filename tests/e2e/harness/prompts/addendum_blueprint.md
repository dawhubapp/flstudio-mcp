## Arrangement blueprint (follow it)

Before planning, call `offline_execute(kind="get_blueprint", genre=<the brief's genre>)`.
It returns arrangement rules measured on reference tracks of that genre. Build the beat
from it:

- **Form.** For a 32-bar beat use `forms.beat_32` (house: intro 8 · build 8 · drop 16).
  Every section is a whole number of 8-bar phrases (`phrase_bars`).
- **Parts.** Each section's `parts` maps `role.layer` to the share of the section's bars
  that part plays: ≥ 0.5 = play it, 0.25–0.5 = optional, missing = leave it out. Give each
  part you use its own channel, named after it (e.g. "Hat · offbeat", "Hat · loop",
  "Snare · build", "Bass · layer"), so layers can come and go independently.
- **Change something every 8 bars** (`transitions.phrase_change`). No two consecutive
  8-bar phrases with the same set of parts: in a 16-bar drop, the second 8 adds or swaps
  a part (e.g. `bass.layer`, `lead.main`, `perc.loop`).
- **Mark phrase ends** (`transitions.phrase_end`). Put a fill, a sweep or riser
  (`list_factory_samples(category="Risers")` or `"SFX"`), a vocal chop (`"Vocals"`) or a
  percussion hit in the last bar of most phrases.
- **Build:** a snare roll across the whole build (`snare.build`), getting denser and
  louder toward the drop; the kick keeps going, chords carry, bass mostly out.
- **Drop:** everything back, plus the drop-only `bass.layer`.
- **Clips don't loop.** A pattern clip longer than its pattern plays the pattern once and
  then stays silent. Make each pattern as long as the bars its clip covers, or place one
  clip per pattern length.
