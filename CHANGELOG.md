# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow the wrapper's semver
(`WRAPPER_VERSION` in `entry.py`). Image: `capcut-mcp:<version>`, with `capcut-mcp:local` pointing at it (see `build.sh`).

## [0.3.0] — 2026-09-09

First public release as `spectrika/capcut-mcp`. Engine: VectCutAPI `d14e7074` (unmodified).

### Changed
- Renamed from `vectcut-mcp` to `capcut-mcp`: image tags, docker label, MCP `serverInfo.name`, log prefix.
  Environment variables (`VECTCUT_*`) and the backup suffix (`*.vectcut-bak-*`) are unchanged.
- Tests no longer depend on the author's machine: `smoke_test.py` synthesises a 5 s sample clip with the
  ffmpeg inside the image unless `VECTCUT_SAMPLE_MP4` is set; the real-project checks in `test_timeline.py`
  and `smoke_test.py` run only when `VECTCUT_REAL_FIXTURE` points at a `draft_content.json` and work with any
  project that has a video clip.
- `platform_capcut_9.json` ships anonymised device identifiers (`VECTCUT_PLATFORM_FILE` overrides the file).

### Added
- `LICENSE` (Apache-2.0), `NOTICE`, `CONTRIBUTING.md`, `SECURITY.md`, GitHub Actions CI (timeline tests,
  docker build + smoke test), example client configs in `docs/` for Claude Desktop and OpenCode.
- English README with OpenCode Desktop/CLI setup; Russian translation in `README.ru.md`.

### Removed
- `probe_media.py` (a machine-specific helper).

## [0.2.0] — 2026-09-07

Verified: CapCut for Windows 9.3.0.3970 opened a copy of a real project with edits (split, retime shift,
blur overlay, new track); Docker Desktop 29.7.2.

### Added
- Existing CapCut projects: `list_projects`, `open_project` (`copy`, like CapCut's own "Copy", and `in_place`),
  `describe_draft` (tracks and segments with indices, speeds, texts, masks, effects).
- Lossless timeline edits on top of hand-made projects (raw passthrough of everything CapCut wrote):
  `delete_range`, `retime_range` (all tracks at once, so overlays stay aligned with the base track),
  `set_segment` (volume, speed, trim, delete), `edit_text`, `set_track` (mute/delete).
- Blur: `blur_region` (clip copy on an overlay track + rectangle mask + "Blur", the manual recipe),
  `edit_mask` (edit existing masks); mask geometry on `add_video`/`add_image`, `effect`/`effect_strength`
  on `add_video`.
- `save_draft` for opened projects: staged write inside the container, backups
  `draft_content.json.vectcut-bak-<ts>` (last 3), mirror updates (`.bak`, `template-2.tmp`, `Timelines/<id>/*`),
  `draft_meta_info.json`, sha256 check "file unchanged since open".
- `timeline_edit.py` (pure JSON operations) + `test_timeline.py`, smoke steps [9]–[12],
  `smoke_test.py --open-existing NAME [--edit-demo]` for a live check on a copy of a real project.
- Versioning: `build.sh` (version tag + `local`, OCI labels), `CHANGELOG.md`, git tags.

### Changed
- `WRAPPER_VERSION` 0.1.1 → 0.2.0; 22 tools (was 12).
- In opened projects new segments always go to new tracks (`<kind>_mcp_<n>`); existing track names are
  rejected; `track_name` added to the `add_text` schema.
- Text segments no longer reference a non-existent `speeds` material (an upstream artefact).
- `rewrite_paths` matches a prefix only as a whole path component.

### Notes
- Timeline edits are only possible before the first `add_*` in the same draft.
- Segments with keyframes, speed curves or transitions are not cut (error with an explanation).
- Materials orphaned by deleted segments stay in `materials` (CapCut tolerates it).

## [0.1.1] — 2026-09-06

First working version: new CapCut 9.3 drafts from Claude Desktop (12 tools), `capcut_9` profile, validation
of paths/URLs/`draft_id`, `capcut_healthcheck`, smoke test, verified by opening the draft in CapCut.
