"""Reverse-engineering harness for the FLP binary format (Phase 1.2).

Submodules:

* :mod:`tools.re_harness.binary_diff` — raw event-level diff between two
  `.flp` files. Works below PyFLP's abstraction so it can detect events
  the library doesn't yet decode.
* :mod:`tools.re_harness.modifications` — schema + loader for the catalog
  of atomic FL Studio modifications driven by the harness (step 1.2.2).
* :mod:`tools.re_harness.registry` — schema + persistence for the discovered
  event registry (step 1.2.5).
"""
