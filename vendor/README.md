# vendor/

`re_harness/` is a vendored copy of `python/tools/re_harness/` from the private
dawhub workspace (MCP-SPEC vendoring decision — v1.0 cuts a clean copy so this
repo builds standalone). Sync manually when the harness changes:

    cp -R ../python/tools/re_harness vendor/  # from the dawhub workspace layout
    # then strip __pycache__/, commit with the source workspace commit hash

Last synced: 2026-07-04.
