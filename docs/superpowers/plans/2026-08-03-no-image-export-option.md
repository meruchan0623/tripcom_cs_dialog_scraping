# No-image Export Option Implementation Plan

**Goal:** Keep normal images as binary files, prohibit Base64-at-rest, and add a Hermes runtime switch that omits image downloads and exported image references.

**Architecture:** Add an `export_images` runtime policy to structured export. The default path continues downloading image bytes into `_assets/`; `--no-images` strips message-body image attachments before JSON/Markdown/index writing and skips image download/index creation.

**Tech Stack:** Python, argparse, pytest, Markdown.

## Global Constraints

- Base64 may only be decoded from an upstream response and must never be persisted as an image representation.
- `--no-images` is per-run and must not rewrite persistent configuration.
- Preserve unrelated browser-migration work.

### Task 1: Runtime behavior

- [ ] Add failing CLI and structured-export tests for `--no-images`.
- [ ] Add the minimal config policy, CLI plumbing, attachment filtering, and index suppression.
- [ ] Run focused and full pytest suites.

### Task 2: Hermes documentation

- [ ] Document binary-file-only storage and `--no-images` in README and Hermes Skill.
- [ ] Verify documented commands through parser tests/search checks.
