# capcut-mcp

**Build and edit CapCut 9.3 projects from Claude Desktop, OpenCode or any MCP client.**
Built by [Spectrika](https://spectrika.ru), an AI assistant that answers questions about any video with timestamps.

[English](README.md) | [Русский](README.ru.md)

[![CI](https://github.com/spectrika/capcut-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/spectrika/capcut-mcp/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

capcut-mcp is a [Model Context Protocol](https://modelcontextprotocol.io) server packaged as a Docker image.
Your AI client starts it with `docker run`, talks to it over stdio, and the server writes CapCut project
folders straight into CapCut's projects directory. Restart CapCut and the project is there: clips, captions,
effects, ready to tweak and export.

It can also **open projects you have edited by hand** in CapCut, apply cuts, speed changes, mutes, caption edits
and blur regions without touching anything else, and write the result back, to a copy by default.

Inside the container runs a pinned commit of the open-source engine
[sun-guannan/VectCutAPI](https://github.com/sun-guannan/VectCutAPI) (Apache-2.0) plus a thin wrapper
(`entry.py`, `timeline_edit.py`) that makes the output compatible with CapCut 9.3, hardens the engine's
input handling, and adds the existing-project tools.

## What it does

22 tools, wrapper version 0.3.0:

| Group | Tools |
|---|---|
| New draft | `create_draft`, `add_video`, `add_audio`, `add_image`, `add_text`, `add_subtitle` (SRT file, URL or inline text), `add_effect`, `add_sticker`, `add_video_keyframe`, `get_video_duration`, `save_draft` |
| Existing projects | `list_projects`, `open_project` (copy or in place), `describe_draft` |
| Timeline edits | `delete_range`, `retime_range`, `set_segment`, `edit_text`, `set_track` |
| Blur and masks | `blur_region`, `edit_mask`; mask geometry on `add_video`/`add_image`, `effect`/`effect_strength` on `add_video` |
| Service | `capcut_healthcheck` |

Parameters of every tool are listed in [Tools reference](#tools-reference).

What it does not do: render or export video, or drive a running CapCut. Export stays manual: open the project
in CapCut and click Export. Segments with keyframes, speed curves or transitions are not cut (you get a clear
error; do those cuts in CapCut).

## Requirements

| Component | Notes |
|---|---|
| Windows 11 with Docker Desktop | Docker Desktop must be **running before** the MCP client starts (the client runs `docker run` itself) |
| CapCut for Windows **9.3.0** | the free version is enough; CapCut Pro is only required if you use Pro assets by ID |
| An MCP client | Claude Desktop installed from the `.exe` (the Microsoft Store build cannot reach Docker), OpenCode Desktop/CLI, or any stdio MCP client |
| Internet | only for media given by http(s) URL; local files are read from the mounted media folder |
| WSL2 or another Linux shell, Python 3.10+ | only to run `build.sh` and the tests; on Windows enable Docker Desktop → Settings → Resources → WSL integration for your distro, or build from PowerShell (below) |

macOS is not supported yet: the wrapper rewrites Windows paths and looks for CapCut's Windows cache layout.
Pull requests welcome.

## Install

```bash
git clone https://github.com/spectrika/capcut-mcp.git
cd capcut-mcp
./build.sh            # builds capcut-mcp:<version> and tags it capcut-mcp:local
```

`build.sh` is a thin wrapper around `docker build`. Without a Linux shell, run the equivalent from PowerShell in the
repo folder:

```powershell
docker build --build-arg WRAPPER_VERSION=0.3.0 -t capcut-mcp:0.3.0 .
docker tag capcut-mcp:0.3.0 capcut-mcp:local
```

The image is `python:3.12-slim` + ffmpeg + the vendored engine; it runs as an unprivileged user and exposes no ports.

Optional checks before wiring up a client:

```bash
python3 test_timeline.py      # timeline operations, no Docker needed
python3 smoke_test.py         # drives the container end to end in scratch folders; expects SMOKE PASSED
```

## Connect a client

Both clients run the same command. Replace `<you>` with your Windows user name and pick a media folder
(any folder with the videos, audio, images and SRT files you want to use). In JSON, every backslash must be doubled,
and a path containing spaces stays a single array element: Docker is launched directly, without a shell.
Media can be referenced either as `/media/clip.mp4` or by its Windows path `C:\...\clip.mp4`; both are accepted.

Volumes and variables:

| Argument | Meaning |
|---|---|
| `-v "<projects dir>:/drafts"` | CapCut's projects folder, the **only** place the container writes to. On Windows it is `C:\Users\<you>\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft` |
| `-v "<media dir>:/media:ro"` | local media for `add_video`, `add_audio`, `add_image`, `add_subtitle`; read-only |
| `-e CAPCUT_DRAFT_ROOT=<projects dir>` | the Windows path of `/drafts`; written into the project file instead of `/drafts` and used to locate CapCut's effect cache for blur |
| `-e VECTCUT_MEDIA_WIN_ROOT=<media dir>` | the Windows path of `/media`, so you can pass `C:\...\clip.mp4` as is |
| `--memory 2g --pids-limit 256 --init` | resource limits; adjust if you like |

Do not add `--name`: a fixed name together with `--rm` fails with "name already in use" when the client restarts the server.

### Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json` (full example in
[docs/claude_desktop.example.json](docs/claude_desktop.example.json)):

```json
{
  "mcpServers": {
    "capcut": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--init", "--label", "app=capcut-mcp",
        "-v", "C:\\Users\\<you>\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft:/drafts",
        "-v", "C:\\Users\\<you>\\Videos:/media:ro",
        "-e", "CAPCUT_DRAFT_ROOT=C:\\Users\\<you>\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft",
        "-e", "VECTCUT_MEDIA_WIN_ROOT=C:\\Users\\<you>\\Videos",
        "--memory", "2g", "--pids-limit", "256",
        "capcut-mcp:local"]
    }
  }
}
```

Quit Claude Desktop completely (right-click the tray icon → Quit; closing the window only minimises it) and start it again. The tools list should show `capcut` with 22 tools.
Logs: `%APPDATA%\Claude\logs\mcp-server-capcut.log`.

### OpenCode Desktop and CLI

OpenCode reads MCP servers from its config file: global `~/.config/opencode/opencode.json`
(on Windows `%USERPROFILE%\.config\opencode\opencode.json`) or a project-level `opencode.json` next to your
work. The Desktop app and the CLI share this file. Add the server under the `mcp` key
(full example in [docs/opencode.example.json](docs/opencode.example.json)):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "capcut": {
      "type": "local",
      "command": ["docker", "run", "-i", "--rm", "--init", "--label", "app=capcut-mcp",
        "-v", "C:\\Users\\<you>\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft:/drafts",
        "-v", "C:\\Users\\<you>\\Videos:/media:ro",
        "-e", "CAPCUT_DRAFT_ROOT=C:\\Users\\<you>\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft",
        "-e", "VECTCUT_MEDIA_WIN_ROOT=C:\\Users\\<you>\\Videos",
        "--memory", "2g", "--pids-limit", "256",
        "capcut-mcp:local"],
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

`type: "local"` means OpenCode spawns the command and speaks MCP over stdio, exactly like Claude Desktop.
`timeout` (milliseconds) is the time OpenCode waits for the tool list; the 5-second default can be too short
for a cold `docker run`, so the example uses 30 seconds. Restart OpenCode Desktop after editing the file. To verify,
run `opencode mcp list` in a terminal, or ask in the chat: "call the capcut_healthcheck tool and show every
check". Tool names appear prefixed with the server name (`capcut_...`). Reference:
[opencode.ai/docs/mcp-servers](https://opencode.ai/docs/mcp-servers/).

### Other MCP clients

Any client that can launch a stdio server works with the same `docker run ...` command line (Claude Code CLI,
Cursor, Cline and others). The server implements MCP `initialize`, `tools/list` and `tools/call`.

## First command

After restarting the client, paste this into the chat:

```
Use the capcut MCP server. Run a full check and report step by step:
1) call capcut_healthcheck and show every check (ok/fail with details);
2) list the capcut tools (there should be 22);
3) call get_video_duration for /media/<your clip>.mp4 and tell me the duration;
4) create a 1080x1920 draft, add the title "MCP test" at 0-3 s, add the video
   /media/<your clip>.mp4 in full (end = its duration), save it with save_draft, draft_name "mcp-test";
5) print the Windows path of the project folder and what I should do to see it in CapCut.
If a step fails, stop and show the error text.
```

Expected: every critical check `ok`, a path like `C:\Users\<you>\...\com.lveditor.draft\mcp-test`, and after
restarting CapCut the project `mcp-test` opens with the clip and the title.

## Working with existing projects

The flow is always the same: `list_projects` → `open_project` → `describe_draft` → timeline edits → `add_*` → `save_draft`.

- **`open_project(name, mode)`**. `mode="copy"` (default) duplicates the project folder the way CapCut's own
  "Copy" does (media included, same internal `id`, new `draft_id` in `draft_meta_info.json`) under the name
  `<name>-mcp` or `copy_name`; existing names are never overwritten, you get `name (2)`. `mode="in_place"`
  edits the project itself. **Close the project in CapCut before an in-place `save_draft`**; safest is to close CapCut entirely:
  CapCut's autosave would overwrite the edits, and edits CapCut made after `open_project` are rejected by the
  "changed on disk" check.
- **Everything CapCut wrote is preserved as is**: tracks, segments, effects, masks, animations, keyframes,
  bookkeeping keys (`platform`, `relationships`, `config`...). The project is not rebuilt through the engine's
  object model; the JSON is carried through and only the requested fields change.
- **`describe_draft`** returns track and segment indices, timeline seconds, speeds, volumes, caption text, masks
  and effects. All edit tools address segments by these indices; all times are timeline seconds.
- **Timeline edits** (`delete_range`, `retime_range`, `set_segment`, `edit_text`, `set_track`, `blur_region`,
  `edit_mask`) must be applied **before the first `add_*`** in the same draft (afterwards, reopen the project).
  `delete_range` and `retime_range` act on all tracks at once by default, so overlays, stickers and captions stay
  aligned with the base track. Apply several cuts **from the end of the timeline backwards** (latest range first),
  so earlier times do not shift under you. Clips on a boundary are
  split; the new half gets its own copies of per-segment materials (speed, mask, effects) while the source
  video file is shared. Speed is written both to the segment and to its `speeds` material.
- **New content** (`add_video`, `add_text`, `add_subtitle`, ...) always goes to **new tracks** at the end of the
  list: omit `track_name` (you get `text_mcp_1`, `video_mcp_1`, ...) or reuse a name created in the same
  session to stack segments on one track. Names of the project's existing tracks are rejected.
  `add_video_keyframe` works only on tracks created in the session.
- **Blur.** `blur_region(track, segment, center_x, center_y, width, height, strength, start_sec, end_sec)`
  reproduces the manual recipe: a copy of the clip on an overlay track `blur_mcp` (inserted right after the last
  video track) + a rectangle mask + the "Blur" effect. Units are CapCut's: centre from −1 to 1 relative to the
  clip centre (`+y` is up), width and height as fractions of the clip; `center_y=-0.6, height=0.4` blurs the
  lower part of the frame. `edit_mask` moves an existing mask (for example to keep a player's progress bar
  sharp). `add_effect(effect_type="Blur")` blurs the whole frame over a range (effect track), and
  `add_video(..., mask_type, mask_center_x/y, mask_size, mask_rect_width, effect="Blur")` adds a new clip
  with a mask and an effect.
- **`save_draft`** for an opened project writes in place: new media are copied into `<project>/assets/`,
  `draft_content.json` and its mirrors (`.bak`, `template-2.tmp`, `Timelines/<id>/*`) are updated, so are
  `draft_meta_info.json` (`tm_draft_modified`, `tm_duration`) and `draft_settings`. Before writing, backups
  `draft_content.json.vectcut-bak-<timestamp>` and `draft_meta_info.json.vectcut-bak-<timestamp>` are kept
  (last 3). `draft_name` is ignored for opened projects.
- **Undo**: rename `draft_content.json.vectcut-bak-<timestamp>` back to `draft_content.json` while CapCut is
  closed; CapCut rewrites the mirrors on its next save.
- After `save_draft`, restart CapCut (or open and close any project): a copy shows up in the list, in-place
  edits are visible when the project is opened.
- Materials orphaned by deleted segments stay in `materials` (CapCut tolerates it; the file is slightly larger).

### Example prompt: edit a copy of a project

```
Use the capcut MCP server. We work on an existing project without deleting anything from the source.
1) list_projects; open_project name="my-project", mode="copy", copy_name="my-project-v2";
2) describe_draft: show the tracks with indices and the segments of the main video track with times;
   all times below are timeline seconds;
3) cuts, strictly right to left: delete_range 80.6-81.4, 45-47, 14.5-25;
   speed-ups between the cuts: retime_range 63-73 x1.5, 25-33 x1.6, 6-11.5 x1.5
   (latest ranges first so earlier times do not shift);
4) set_track on the main video track with mute=true; then set_segment volume=0 for the remaining video clips;
5) edit_text: move the first caption to transform_y 0.86; change the last caption's text to "Menu: all your videos";
6) blur_region on the clip that shows the browser: center_y -0.6, height 0.4, strength 50, over its whole length;
7) add_subtitle with inline SRT (times on the new timeline), bold font, font_size 10, font_color #000000,
   background_color #FFD200, background_alpha 1, transform_y 0.67, track_name "captions";
8) save_draft; print the project path, the duration, the new tracks and the backup file name.
If a step fails, stop and show the full error.
```

## Tools reference

All 22 tools, grouped by workflow. Parameters marked `*` are required; times are seconds; positions and mask
geometry use CapCut units (−1..1 from the centre, `+y` up); sizes are fractions of the clip. Every tool that
takes `draft_id` expects the id returned by `create_draft` or `open_project`. Media arguments (`video_url`,
`audio_url`, `image_url`, `srt_path`) accept a public http(s) URL, a path under `/media/`, or the same file by its
Windows path when `VECTCUT_MEDIA_WIN_ROOT` is set.

### Service

| Tool | What it does | Parameters |
|---|---|---|
| `capcut_healthcheck` | Self-check of the container: draft profile, `/drafts` and `/media` mounts, `CAPCUT_DRAFT_ROOT`, ffprobe, internet egress, stage folder. Call it first in a new session. | none |
| `get_video_duration` | Duration of a media file in seconds (ffprobe). | `video_url*` |

### New drafts

| Tool | What it does | Parameters |
|---|---|---|
| `create_draft` | New empty draft in memory; returns `draft_id`. | `width` (1080), `height` (1920) |
| `add_video` | Add a video clip. `start`/`end` cut the source, `target_start` places it on the timeline. Optional mask and per-clip effect. | `video_url*`, `draft_id*`, `start` (0), `end`, `target_start` (0), `width`, `height`, `transform_x`, `transform_y`, `scale_x`, `scale_y`, `speed` (1), `volume` (1), `track_name` (`main`), `transition`, `transition_duration` (0.5), `background_blur` (1–4), `mask_type` (`rectangle`, `circle`, `linear`, `mirror`, `star`), `mask_center_x`, `mask_center_y`, `mask_size` (height fraction), `mask_rect_width`, `mask_rotation`, `mask_feather` (0–100), `mask_round_corner` (0–100), `mask_invert`, `effect` (e.g. `Blur`), `effect_strength` (0–100) |
| `add_audio` | Add an audio clip. | `audio_url*`, `draft_id*`, `start` (0), `end`, `target_start` (0), `volume` (1), `speed` (1), `track_name` (`audio_main`) |
| `add_image` | Add a still image for `start`–`end` seconds of the timeline, with optional animations, transition and mask. | `image_url*`, `draft_id*`, `start` (0), `end` (3), `width`, `height`, `transform_x`, `transform_y`, `scale_x`, `scale_y`, `track_name` (`main`), `intro_animation`, `outro_animation`, `transition`, `mask_*` (same as `add_video`) |
| `add_text` | Add a text overlay with optional shadow, background and per-range styles. | `text*`, `start*`, `end*`, `draft_id*`, `font_color` (`#ffffff`), `font_size` (24), `shadow_enabled`, `shadow_color`, `shadow_alpha` (0.8), `shadow_angle` (315), `shadow_distance` (5), `shadow_smoothing`, `background_color`, `background_alpha` (1), `background_style` (0), `background_round_radius`, `text_styles` (list), `track_name` |
| `add_subtitle` | Add captions from an SRT file, URL or inline SRT text, one styled segment per cue. | `srt_path*`, `draft_id*`, `track_name` (`subtitle`), `time_offset` (0), `font`, `font_size` (8), `font_color` (`#FFFFFF`), `bold`, `italic`, `underline`, `border_width`, `border_color`, `background_color`, `background_alpha` (0), `transform_x` (0), `transform_y` (−0.8) |
| `add_effect` | Whole-frame scene effect on its own effect track (e.g. `Blur`). | `effect_type*`, `draft_id*`, `start` (0), `end` (3), `track_name` (`effect_01`), `params` (list of numbers) |
| `add_sticker` | Add a sticker by CapCut resource id. | `resource_id*`, `draft_id*`, `start*`, `end*`, `transform_x`, `transform_y`, `scale_x`, `scale_y`, `alpha` (1), `rotation` (0), `track_name` (`sticker_main`) |
| `add_video_keyframe` | Keyframe animation on a clip of a track created in this session (position, scale, rotation, alpha, colour, volume). Single or batch mode. | `draft_id*`, `track_name` (`main`), `property_type`, `time` (0), `value`; batch: `property_types`, `times`, `values` |
| `save_draft` | Write the draft to disk. New draft: creates the project folder, copies or downloads media, fills CapCut metadata. Opened project: writes the edits back (backup kept). Call last; restart CapCut to see the result. | `draft_id*`, `draft_name` (new drafts only) |

`width`/`height` on the `add_*` tools are the canvas size used for placement and default to 1080×1920.

### Existing projects

| Tool | What it does | Parameters |
|---|---|---|
| `list_projects` | Projects in the CapCut folder with name, modification time, duration and media size. | none |
| `open_project` | Open a project for editing. `copy` duplicates the folder like CapCut's own "Copy" and edits the copy; `in_place` edits the original. Returns a `draft_id` and a timeline summary. | `name*` (folder name from `list_projects`), `mode` (`copy`, `in_place`), `copy_name` (default `<name>-mcp`) |
| `describe_draft` | Timeline summary: tracks with indices, segments with start/end, kind, speed, volume, text, masks, effects. The indices feed the editing tools. | `draft_id*` |

### Timeline edits

Only on a project opened with `open_project` and only before the first `add_*` call on it.

| Tool | What it does | Parameters |
|---|---|---|
| `delete_range` | Ripple-delete `[start_sec, end_sec)` on all tracks (or the given ones): clips on the edges are split, later content moves left. Apply later ranges first. | `draft_id*`, `start_sec*`, `end_sec*`, `tracks` (list of indices) |
| `retime_range` | Change the speed of everything inside the range (`speed=2` is twice as fast); edges are split, later content shifts. | `draft_id*`, `start_sec*`, `end_sec*`, `speed*` (0.05–100), `tracks` |
| `set_segment` | Edit one clip: volume (0 = mute), speed, trim by new timeline start/end, or delete. `ripple` shifts the following clips of the same track only. | `draft_id*`, `track*`, `segment*`, `volume` (0–10), `speed`, `start_sec`, `end_sec`, `delete`, `ripple` |
| `edit_text` | Edit an existing text overlay: text (style kept), timing, position, uniform scale. | `draft_id*`, `track*`, `segment*`, `text`, `start_sec`, `end_sec`, `transform_x`, `transform_y`, `scale` |
| `set_track` | Mute every clip of a track (or restore) or delete the track. | `draft_id*`, `track*`, `mute`, `delete` |

### Blur and masks

| Tool | What it does | Parameters |
|---|---|---|
| `blur_region` | Blur a rectangle of an existing clip the way CapCut does it by hand: a copy of the clip on an overlay track `blur_mcp` with a rectangle mask and the "Blur" effect. | `draft_id*`, `track*`, `segment*`, `center_x*`, `center_y*`, `width*`, `height*`, `strength` (50), `feather` (0–1), `round_corner` (0–1), `invert`, `start_sec`, `end_sec` |
| `edit_mask` | Change the mask of an existing clip (including the overlay made by `blur_region`). | `draft_id*`, `track*`, `segment*`, `center_x`, `center_y`, `width`, `height`, `feather`, `round_corner`, `invert` |

Results are JSON: `success`, `result` (for example `draft_id`, `track_name`, `duration_sec`, the changed tracks) or `error`
with a message that explains what to fix.

## Limits and safety

- Drafts and opened projects live in the container's memory until `save_draft`; the container lives as long as
  the client keeps it running. After a client restart unsaved work is gone and old `draft_id`s are invalid.
- A `save_draft` can take tens of seconds (media download + ffprobe). Put large files into the media folder and
  reference them as `/media/...` instead of URLs; files are copied into the project folder because CapCut needs
  local copies. Copying a large project (`open_project` in copy mode) takes seconds per hundred megabytes.
- The engine on its own is not safe to expose: an HTTP server without auth, `rmtree` on an arbitrary
  `draft_id`, SSRF and local file reads. The wrapper closes these holes:
  - no HTTP server, no ports, stdio only;
  - `draft_id` must match `dfd_cat_<ts>_<hex8>` and exist in memory, so the engine's `rmtree` can only hit
    `/drafts/dfd_cat_*` (new drafts) and the container's stage folder;
  - a project name in `open_project` is exactly one folder inside `/drafts` (no `/`, `\` or leading dot) that
    contains `draft_content.json`;
  - for opened projects the container writes only `<project>/{draft_content.json, mirrors, draft_meta_info.json,
    draft_settings, assets/**, *.vectcut-bak-*}` and the new copy folder; nothing in `/drafts` is deleted;
  - URLs: http(s) to public IPs only (loopback, LAN and link-local are rejected after DNS resolution); local
    paths only inside `/media` (read-only); other schemes (`file:`, `concat:`, ...) are rejected;
  - arguments are filtered by each tool's `inputSchema` (you cannot smuggle `draft_folder`);
  - non-root container, `--memory 2g --pids-limit 256`, a single writable volume;
  - `is_upload_draft: false`, no cloud credentials, no telemetry in the code.
- Pro assets (effects, stickers, fonts by ID from the paid catalogue) make CapCut ask for Pro at export time;
  stick to free ones. The "Blur" effect and the rectangle mask are free.
- Windows folders mounted into the container do not support chmod/utime; the wrapper skips copying file
  metadata (`shutil.copystat`), file contents are copied in full.
- CapCut's project format is undocumented and changes between versions. Verified with 9.3.0.3970. After a
  CapCut update, run the first command again and open a copy of a real project. The `platform` block written
  into new drafts comes from `platform_capcut_9.json` (synthetic device IDs, not taken from any real machine). To use
  the block from one of your own drafts instead, mount it and point the wrapper at it:
  `-v C:\path\platform.json:/app/my_platform.json:ro -e VECTCUT_PLATFORM_FILE=/app/my_platform.json`.
- Units: times are seconds; positions and mask geometry are CapCut's −1..1 coordinates; sizes are fractions of
  the clip. Segments with keyframes, speed curves or transitions are refused by the edit tools.

## Tested with

| Component | Version |
|---|---|
| CapCut for Windows | 9.3.0.3970 |
| Windows | 11 |
| Docker Desktop | 29.x |
| Claude Desktop | current `.exe` build (new drafts and edited copies open in CapCut) |
| OpenCode Desktop | configured per the official docs; not yet verified end to end by us |
| Engine | VectCutAPI `d14e7074`, unmodified |

## Versioning and rollback

- The wrapper version is `WRAPPER_VERSION` in `entry.py` (semver: patch for fixes, minor for new tools or
  behaviour, major for incompatible tool schemas or config). It is reported by `initialize` and
  `capcut_healthcheck`. History: `CHANGELOG.md`, git tags `v<version>`.
- `./build.sh` builds `capcut-mcp:<version>` (OCI labels: version, git sha, engine sha) and re-points
  `capcut-mcp:local` at it, so client configs never change.
- Rollback: `docker tag capcut-mcp:<old version> capcut-mcp:local` and restart the client.
  `docker image ls capcut-mcp` lists what you have.

## Updating upstream

```bash
cd /tmp && git clone https://github.com/sun-guannan/VectCutAPI.git && cd VectCutAPI
git archive --format=tar.gz --prefix=VectCutAPI/ -o <repo>/vendor/VectCutAPI-<sha8>.tar.gz <sha>
# Dockerfile: ARG VECTCUT_SHA=<sha> and the archive name in ADD; if upstream requirements changed:
cd <repo> && uv pip compile --python-version 3.12 --generate-hashes requirements.in -o requirements.lock
./build.sh && python3 test_timeline.py && python3 smoke_test.py
```

The current archive is a `git archive` of commit `d14e70749c9331424ab816a402bb45417c50cf68`
(sha256 `ce1bbbf193060b3c65b1826968420492aa8157f9a846f76ebb31dd7c33e4c707`). After an update, re-check the
upstream modules the wrapper relies on: `draft_profiles.py` (`DraftProfile` signature), `save_draft_impl.py`
(`draft_folder`, `get_task_status`), `mcp_server.py` (`TOOLS`, `execute_tool`), `pyJianYingDraft/track.py`
(`Track.__init__`, `Track_type`), `pyJianYingDraft/script_file.py` (`Script_file.__init__`, `materials.export_json`).

## Troubleshooting

| Symptom | What to do |
|---|---|
| The client shows no `capcut` server / start-up error | Is Docker Desktop running? Does `docker image ls capcut-mcp` list `local`? Claude Desktop log: `%APPDATA%\Claude\logs\mcp-server-capcut.log`; OpenCode: `opencode mcp list` |
| Fewer than 22 tools | The client was not restarted after `./build.sh` (the container starts with the client) |
| `capcut_healthcheck`: `drafts_mount` fail | Check the `-v` line with the `com.lveditor.draft` path (one array element, doubled backslashes) and that Docker Desktop may access `C:\Users` (Settings → Resources → File sharing) |
| `capcut_draft_root` fail | Add `-e CAPCUT_DRAFT_ROOT=...` |
| `internet_egress` fail | Media by URL will not work; local files from `/media` will |
| The project does not show up in CapCut | Restart CapCut completely; check that the folder is inside `com.lveditor.draft` and contains `draft_content.json` and `draft_meta_info.json` |
| CapCut says media is missing | Paths in `draft_content.json` must start with `C:/Users/<you>/.../com.lveditor.draft/<project>/assets/` or with CapCut's placeholder; `materials_failed` in the `save_draft` result lists what failed to download |
| `open_project`: `project ... not found` | Use the folder name exactly as in `list_projects` (folder, not `draft_name`); hidden folders such as `.recycle_bin` are not accessible |
| `save_draft`: `changed on disk since open_project` | CapCut modified the file (the project was open) or another `draft_id` of the same project saved first. Close the project in CapCut, `open_project` again, redo the edits |
| `track_name ... already exists in the project` | New segments go to new tracks only: drop `track_name` or pick a new name |
| `timeline edits must be applied before add_*` | Order: `delete_range` / `retime_range` / ... first, `add_*` after; otherwise reopen the project |
| `segment ... has keyframes / curve speed / transition` | Such segments are not cut; do it in CapCut or choose boundaries at the segment edges |
| CapCut overwrote the MCP edits | The project was open during `save_draft`. Undo: `draft_content.json.vectcut-bak-<timestamp>` → `draft_content.json` |
| Blur is not visible in CapCut | Check in `describe_draft` that the segment has `effects: ["Blur"]` and a mask; if CapCut cannot find the effect resource, open any "Blur" effect in CapCut once so it lands in the cache, then retry |
| `name already in use` | Remove `--name` from the arguments |
| Slow `save_draft` / timeout | Put large files into `/media` instead of URLs; check `took_sec` in the result |
| `save_draft`: `Operation not permitted` while copying | Windows mounts refuse chmod/utime; the wrapper ignores it, so if you see the error an old image is running: rebuild |
| An empty `dfd_cat_*` project appeared in CapCut | Leftover of a failed `save_draft` of a new draft; delete the folder (marked `leftover` in `list_projects`) |
| A CapCut update broke opening | Copy the `platform` block from a fresh real draft into `platform_capcut_9.json`, update `CONTENT_VERSION`/`NEW_VERSION` in `entry.py`, rebuild; re-run `test_timeline.py` with `VECTCUT_REAL_FIXTURE` on a new file |

## Project layout

```
Dockerfile               image: python:3.12-slim + ffmpeg + vendored engine + entry.py + timeline_edit.py
build.sh                 build with the version tag and re-point capcut-mcp:local
entry.py                 stdio wrapper (profile, validation, new drafts, open/save projects, healthcheck)
timeline_edit.py         timeline operations on existing projects (pure functions over JSON)
config.json              engine config inside the container
platform_capcut_9.json   platform block for CapCut 9.3.0 drafts (anonymised)
template_capcut_9/       project folder template (CapCut 9.x / Jianying 10.x layout)
vendor/                  git archive of the engine at the pinned commit
requirements.in/.lock    Python dependencies (lock with hashes)
test_timeline.py         host-side tests for timeline_edit (+ a real draft_content.json via VECTCUT_REAL_FIXTURE)
smoke_test.py            end-to-end test of the image without CapCut; --drafts-dir for a real folder, --open-existing NAME for a live copy
docs/                    example client configs
.github/workflows/       CI: timeline tests, docker build + smoke test
CHANGELOG.md             version history
CONTRIBUTING.md          dev setup, tests, rules for changes
SECURITY.md              what the wrapper protects against and how to report issues
LICENSE, NOTICE          Apache-2.0 and third-party attribution
```

## About Spectrika

[Spectrika](https://spectrika.ru) is an AI assistant for video: send it a link, and it transcribes the video,
answers questions about it with timestamped citations, and summarises it in Telegram, MAX or the web.
capcut-mcp grew out of our own editing workflow, where explainer videos are assembled and re-cut from an AI
chat. Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). The vendored engine VectCutAPI and the
pyJianYingDraft library are Apache-2.0 as well. CapCut and Jianying are trademarks of their respective owners;
this project is not affiliated with or endorsed by ByteDance or CapCut.
