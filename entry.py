#!/usr/bin/env python3
"""
capcut-mcp entry point.

A thin stdio JSON-RPC wrapper around upstream VectCutAPI's ``mcp_server.py`` that:

* swaps the draft profile for a CapCut 9.3 layout (``draft_content.json``) while
  keeping CapCut (not Jianying) effect/animation IDs;
* validates every argument that touches the filesystem or the network
  (draft ids, media URLs, local media paths, project names);
* writes new projects straight into the CapCut projects folder (``/drafts`` bind
  mount) and rewrites embedded paths to the Windows paths CapCut expects;
* opens EXISTING CapCut projects (in place or as a copy), keeps everything CapCut
  wrote byte-for-byte, lets the timeline be edited (cuts, speed, mute, texts, blur)
  and new tracks be added, then writes the project back with a backup;
* adds ``capcut_healthcheck``, ``list_projects``, ``open_project``, ``describe_draft``
  and the timeline-editing tools.

stdout carries JSON-RPC only; everything else goes to stderr.
"""

import errno
import hashlib
import ipaddress
import json
import shutil
import logging
import os
import re
import socket
import subprocess
import sys
import time
import traceback
import types
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

WRAPPER_VERSION = "0.3.0"

REPO = os.environ.get("VECTCUT_REPO", "/app/VectCutAPI")
DRAFTS_DIR = os.environ.get("VECTCUT_DRAFTS_DIR", "/drafts")
MEDIA_DIR = os.environ.get("VECTCUT_MEDIA_DIR", "/media")
STAGE_DIR = os.environ.get("VECTCUT_STAGE_DIR", "/tmp/vectcut-stage")
CAPCUT_DRAFT_ROOT = os.environ.get("CAPCUT_DRAFT_ROOT", "").strip()
MEDIA_WIN_ROOT = os.environ.get("VECTCUT_MEDIA_WIN_ROOT", "").strip()
PROFILE_MODE = os.environ.get("VECTCUT_PROFILE_MODE", "capcut_9").strip().lower()
UPSTREAM_SHA = os.environ.get("VECTCUT_UPSTREAM_SHA", "unknown")
PLATFORM_FILE = os.environ.get("VECTCUT_PLATFORM_FILE", "/app/platform_capcut_9.json")
EGRESS_PROBE_URL = os.environ.get("VECTCUT_EGRESS_PROBE_URL", "https://github.com/")

CONTENT_VERSION = 360000          # CapCut 9.3 draft_content.json "version"
NEW_VERSION = "183.0.0"           # CapCut 9.3 draft_content.json "new_version"
DRAFT_ID_RE = re.compile(r"^dfd_cat_\d+_[0-9a-f]{8}$")
WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
PROJECT_NAME_RE = re.compile(r"^[^/\\\x00-\x1f]{1,120}$")
MEDIA_KEYS = ("video_url", "audio_url", "image_url", "srt_path")
EDIT_TOOLS = {"delete_range", "retime_range", "set_segment", "edit_text", "set_track", "blur_region", "edit_mask"}
TRACK_TOOLS = {  # tool -> kind used for auto-generated track names in opened projects
    "add_video": "video", "add_audio": "audio", "add_image": "video", "add_text": "text",
    "add_subtitle": "text", "add_sticker": "sticker", "add_effect": "effect", "add_video_keyframe": None,
}
DRAFT_TOOLS = {
    "add_video", "add_audio", "add_image", "add_text", "add_subtitle",
    "add_effect", "add_sticker", "add_video_keyframe", "save_draft", "describe_draft",
} | EDIT_TOOLS
TEXT_SUFFIXES = {".json", ".bak", ".tmp"}
BACKUP_KEEP = 3
MIRROR_CANDIDATES = ("draft_content.json.bak", "template-2.tmp", "Timelines/*/draft_content.json",
                     "Timelines/*/draft_content.json.bak", "Timelines/*/template-2.tmp")

log = logging.getLogger("capcut-mcp")

# Filled in by bootstrap()
REAL_STDOUT = None
mcp_server = None
draft_cache = None
save_task_cache = None
draft_profiles = None
save_draft_impl_fn = None
ORIG_EXECUTE_TOOL = None
Track = None
Track_type = None
Script_file = None
pj_util = None
CapCut_Video_scene_effect_type = None
te = None

# draft_id -> state of a project opened with open_project (see load_project)
OPENED = {}


_LENIENT_ERRNOS = {errno.EPERM, errno.EACCES, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}


def _install_lenient_copystat():
    """Tolerate metadata-copy failures on Windows bind mounts.

    Docker Desktop / drvfs mounts of Windows folders accept file writes but refuse
    chmod/utime/xattr, so shutil.copy2/copytree (used by upstream for the template
    and for local media) would raise EPERM after the data was already copied.
    File contents are unaffected; only mtime/mode preservation is skipped.
    """
    original = shutil.copystat

    def lenient_copystat(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except OSError as exc:
            if exc.errno in _LENIENT_ERRNOS:
                log.debug("copystat skipped on %s: %s", args[1] if len(args) > 1 else "?", exc)
                return None
            raise

    shutil.copystat = lenient_copystat


# --------------------------------------------------------------------------- boot

def bootstrap():
    """Import upstream with stdout redirected so stray print() never hits JSON-RPC."""
    global REAL_STDOUT, mcp_server, draft_cache, save_task_cache, draft_profiles
    global save_draft_impl_fn, ORIG_EXECUTE_TOOL, Track, Track_type, Script_file, pj_util
    global CapCut_Video_scene_effect_type, te

    logging.basicConfig(
        stream=sys.stderr,
        level=os.environ.get("VECTCUT_LOG_LEVEL", "INFO"),
        format="[capcut-mcp] %(levelname)s %(message)s",
    )
    REAL_STDOUT = sys.stdout
    sys.stdout = sys.stderr
    _install_lenient_copystat()

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import timeline_edit as _te  # noqa: E402  (ours, lives next to entry.py)

    sys.path.insert(0, REPO)
    os.chdir(REPO)

    import mcp_server as _mcp            # noqa: E402  (upstream, needs sys.path)
    import draft_cache as _dc            # noqa: E402
    import save_task_cache as _stc       # noqa: E402
    import draft_profiles as _dp         # noqa: E402
    from save_draft_impl import save_draft_impl as _save  # noqa: E402
    from pyJianYingDraft.track import Track as _Track, Track_type as _Track_type  # noqa: E402
    from pyJianYingDraft.script_file import Script_file as _Script_file  # noqa: E402
    from pyJianYingDraft import util as _pj_util  # noqa: E402
    from pyJianYingDraft.metadata.capcut_effect_meta import CapCut_Video_scene_effect_type as _fx  # noqa: E402

    mcp_server, draft_cache, save_task_cache, draft_profiles = _mcp, _dc, _stc, _dp
    save_draft_impl_fn = _save
    Track, Track_type, Script_file, pj_util = _Track, _Track_type, _Script_file, _pj_util
    CapCut_Video_scene_effect_type = _fx
    te = _te
    ORIG_EXECUTE_TOOL = mcp_server.execute_tool
    if not mcp_server.CAPCUT_AVAILABLE:
        raise RuntimeError("upstream CapCut modules failed to import (see stderr above)")
    _define_raw_track()


def patch_profile():
    """Replace upstream's capcut_legacy profile with the CapCut 9.3 layout.

    settings/local.py already resolved DRAFT_PROFILE="capcut_legacy" and
    IS_CAPCUT_ENV=True from config.json at import time; get_draft_profile()
    looks the name up lazily in PROFILES, so swapping the entry is enough.
    """
    if PROFILE_MODE == "upstream":
        prof = draft_profiles.get_draft_profile()
        log.info("profile mode 'upstream': %s (%s, %s)", prof.name, prof.template_dir, prof.content_file)
        return prof

    with open(PLATFORM_FILE, encoding="utf-8") as f:
        platform = json.load(f)
    prof = draft_profiles.DraftProfile(
        name="capcut_9",
        template_dir="template_capcut_9",
        content_file="draft_content.json",
        content_mirrors=("draft_content.json.bak", "template-2.tmp"),
        timeline_content_file="template.tmp",
        is_capcut_env=True,
        platform=platform,
    )
    template_dir = Path(REPO) / prof.template_dir
    if not template_dir.is_dir():
        raise RuntimeError(f"template dir missing: {template_dir}")
    draft_profiles.PROFILES["capcut_legacy"] = prof
    draft_profiles.PROFILE_ALIASES["capcut_9"] = "capcut_legacy"
    log.info("profile patched: capcut_9 (template=%s, app_version=%s, os=%s)",
             prof.template_dir, platform.get("app_version"), platform.get("os"))
    return prof


# --------------------------------------------------------------------------- tool schemas

def _num(desc, **extra):
    return {"type": "number", "description": desc, **extra}


def _int(desc):
    return {"type": "integer", "description": desc}


def _str(desc, **extra):
    return {"type": "string", "description": desc, **extra}


def _bool(desc):
    return {"type": "boolean", "description": desc}


TRACK_IDX = _int("Track index from describe_draft.")
SEG_IDX = _int("Segment index inside that track, from describe_draft.")
EDIT_NOTE = (" Works only on a project opened with open_project and only BEFORE any add_* call on it "
             "(re-open the project to edit again). Times are timeline seconds as shown by describe_draft.")

HEALTHCHECK_TOOL = {
    "name": "capcut_healthcheck",
    "description": (
        "Self-check of the CapCut MCP container: draft profile, /drafts and /media mounts, "
        "CAPCUT_DRAFT_ROOT, ffprobe, internet egress, stage dir. Call it first in a new session."
    ),
    "inputSchema": {"type": "object", "properties": {}},
}

NEW_TOOLS = [
    {
        "name": "list_projects",
        "description": "List existing CapCut projects in the projects folder (name, modified, duration, media size). "
                       "Use the returned name with open_project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "open_project",
        "description": (
            "Open an existing CapCut project for editing. mode='copy' (default) duplicates the project folder "
            "(like CapCut's own copy) and edits the copy; mode='in_place' edits the original. Everything CapCut "
            "wrote is preserved. Returns a draft_id for describe_draft, the timeline-editing tools "
            "(delete_range, retime_range, set_segment, edit_text, set_track, blur_region, edit_mask), add_* and "
            "save_draft. Close the project in CapCut before save_draft (in_place) — CapCut autosave would overwrite."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": _str("Project folder name exactly as returned by list_projects."),
                "mode": _str("'copy' (default, safe) or 'in_place'.", enum=["copy", "in_place"]),
                "copy_name": _str("Name for the copy (mode='copy'); default '<name>-mcp'; never overwrites: "
                                  "an existing name becomes 'name (2)'."),
            },
            "required": ["name"],
        },
    },
    {
        "name": "describe_draft",
        "description": "Timeline summary of a draft: tracks with indices, segments with start/end seconds, speed, "
                       "volume, text, masks and effects. Use the indices with the editing tools.",
        "inputSchema": {"type": "object", "properties": {"draft_id": _str("Draft id.")}, "required": ["draft_id"]},
    },
    {
        "name": "delete_range",
        "description": "Ripple-delete a timeline range [start_sec, end_sec) on all tracks (or the given tracks): "
                       "clips are split/trimmed at the edges, everything after moves left. Delete later ranges first "
                       "when applying several cuts from one table." + EDIT_NOTE,
        "inputSchema": {"type": "object", "properties": {
            "draft_id": _str("Draft id."), "start_sec": _num("Range start, seconds."), "end_sec": _num("Range end, seconds."),
            "tracks": {"type": "array", "items": {"type": "integer"},
                       "description": "Optional track indices; default all tracks (keeps overlays aligned)."},
        }, "required": ["draft_id", "start_sec", "end_sec"]},
    },
    {
        "name": "retime_range",
        "description": "Change the speed of everything inside [start_sec, end_sec) on all tracks (or the given "
                       "tracks): speed=2 makes it twice as fast; clips are split at the edges, later content shifts."
                       + EDIT_NOTE,
        "inputSchema": {"type": "object", "properties": {
            "draft_id": _str("Draft id."), "start_sec": _num("Range start, seconds."), "end_sec": _num("Range end, seconds."),
            "speed": _num("Speed factor, e.g. 1.5 or 3 (0.05..100)."),
            "tracks": {"type": "array", "items": {"type": "integer"}, "description": "Optional track indices; default all."},
        }, "required": ["draft_id", "start_sec", "end_sec", "speed"]},
    },
    {
        "name": "set_segment",
        "description": "Edit one existing clip: volume (0 = mute), speed, trim (start_sec/end_sec on the timeline), "
                       "or delete. ripple=true shifts the following clips of the SAME track only; use delete_range/"
                       "retime_range for multi-track edits." + EDIT_NOTE,
        "inputSchema": {"type": "object", "properties": {
            "draft_id": _str("Draft id."), "track": TRACK_IDX, "segment": SEG_IDX,
            "volume": _num("0..10, 1 = original, 0 = mute."), "speed": _num("New speed (video/audio clips)."),
            "start_sec": _num("New start on the timeline (trim/extend the head)."),
            "end_sec": _num("New end on the timeline (trim/extend the tail)."),
            "delete": _bool("Remove the segment."), "ripple": _bool("Shift following segments of this track."),
        }, "required": ["draft_id", "track", "segment"]},
    },
    {
        "name": "edit_text",
        "description": "Edit an existing text overlay: text, timing, position (transform_x/transform_y, CapCut units: "
                       "0 = centre, +y up, e.g. 0.67 = upper zone), scale." + EDIT_NOTE,
        "inputSchema": {"type": "object", "properties": {
            "draft_id": _str("Draft id."), "track": TRACK_IDX, "segment": SEG_IDX,
            "text": _str("New text (style is kept)."), "start_sec": _num("New start, seconds."), "end_sec": _num("New end, seconds."),
            "transform_x": _num("Horizontal position, -1..1."), "transform_y": _num("Vertical position, -1..1 (+ up)."),
            "scale": _num("Uniform scale, e.g. 0.8."),
        }, "required": ["draft_id", "track", "segment"]},
    },
    {
        "name": "set_track",
        "description": "Mute (volume 0 on every clip) or delete an existing track." + EDIT_NOTE,
        "inputSchema": {"type": "object", "properties": {
            "draft_id": _str("Draft id."), "track": TRACK_IDX,
            "mute": _bool("true = mute all clips, false = restore."), "delete": _bool("Remove the track."),
        }, "required": ["draft_id", "track"]},
    },
    {
        "name": "blur_region",
        "description": "Blur a rectangular region of an existing clip the way CapCut does it manually: a copy of the "
                       "clip goes to an overlay track with a rectangle mask and the Blur effect. Mask units: "
                       "center_x/center_y in -1..1 from the clip centre (+y up), width/height as fractions of the clip "
                       "(e.g. center_y=-0.6, height=0.4 blurs the lower part). Optional start_sec/end_sec limit the "
                       "blur inside the clip." + EDIT_NOTE,
        "inputSchema": {"type": "object", "properties": {
            "draft_id": _str("Draft id."), "track": TRACK_IDX, "segment": SEG_IDX,
            "center_x": _num("Mask centre X, -1..1."), "center_y": _num("Mask centre Y, -1..1 (+ up)."),
            "width": _num("Mask width as a fraction of the clip (1 = full width)."),
            "height": _num("Mask height as a fraction of the clip."),
            "strength": _num("Blur strength 0..100 (default 50)."), "feather": _num("Edge feather 0..1."),
            "round_corner": _num("Corner rounding 0..1."), "invert": _bool("Blur everything except the rectangle."),
            "start_sec": _num("Blur only from this timeline second."), "end_sec": _num("Blur only until this second."),
        }, "required": ["draft_id", "track", "segment", "center_x", "center_y", "width", "height"]},
    },
    {
        "name": "edit_mask",
        "description": "Change the mask of an existing clip (e.g. the blur overlay CapCut made): center_x/center_y "
                       "(-1..1, +y up), width/height (fractions of the clip), feather, round_corner, invert." + EDIT_NOTE,
        "inputSchema": {"type": "object", "properties": {
            "draft_id": _str("Draft id."), "track": TRACK_IDX, "segment": SEG_IDX,
            "center_x": _num("Mask centre X."), "center_y": _num("Mask centre Y."), "width": _num("Mask width."),
            "height": _num("Mask height."), "feather": _num("0..1"), "round_corner": _num("0..1"), "invert": _bool("Invert."),
        }, "required": ["draft_id", "track", "segment"]},
    },
]


def patch_tools():
    tools = mcp_server.TOOLS
    by_name = {t["name"]: t for t in tools}
    media_note = (
        f" Accepts a public http(s) URL or a file under {MEDIA_DIR}/ (read-only mount"
        + (f" of {MEDIA_WIN_ROOT}" if MEDIA_WIN_ROOT else "")
        + "). Private/LAN hosts and other local paths are rejected."
    )
    for tool, key in (("add_video", "video_url"), ("add_audio", "audio_url"),
                      ("add_image", "image_url"), ("get_video_duration", "video_url")):
        by_name[tool]["inputSchema"]["properties"][key]["description"] += media_note
    by_name["add_subtitle"]["inputSchema"]["properties"]["srt_path"]["description"] += (
        media_note + " Inline SRT text is also accepted."
    )
    track_note = (" In a project opened with open_project new content always goes to a NEW track: omit "
                  "track_name to get an auto-named one, or reuse a name created earlier in this session; "
                  "names of the project's existing tracks are rejected.")
    for tool in DRAFT_TOOLS:
        if tool not in by_name:
            continue
        props = by_name[tool]["inputSchema"]["properties"]
        props["draft_id"]["description"] = "Draft id returned by create_draft or open_project (required)."
        req = by_name[tool]["inputSchema"].setdefault("required", [])
        if "draft_id" not in req:
            req.append("draft_id")
        if tool in TRACK_TOOLS and tool != "add_video_keyframe":
            if "track_name" not in props:
                props["track_name"] = {"type": "string", "description": "Optional track name."}
            props["track_name"]["description"] = (props["track_name"].get("description") or "Track name.") + track_note
    by_name["create_draft"]["description"] = (
        "Create a new CapCut draft in memory (default 1080x1920). Returns draft_id; pass it to "
        "every add_* call and finish with save_draft. To edit an existing project use open_project instead."
    )
    mask_props = {
        "mask_center_x": _num("Mask centre X in CapCut units: -1..1 from the clip centre (default 0)."),
        "mask_center_y": _num("Mask centre Y in CapCut units: -1..1 from the clip centre, +y up (default 0)."),
        "mask_size": _num("Mask height as a fraction of the clip height (default 1.0; e.g. 0.4)."),
        "mask_rotation": _num("Mask rotation, degrees."),
        "mask_feather": _num("Mask feather 0..100."),
        "mask_invert": _bool("Invert the mask (keep everything except the shape)."),
        "mask_rect_width": _num("Rectangle mask width as a fraction of the clip width (default = mask_size)."),
        "mask_round_corner": _num("Rectangle mask corner rounding 0..100."),
    }
    for tool in ("add_video", "add_image"):
        props = by_name[tool]["inputSchema"]["properties"]
        props["mask_type"]["description"] = "Mask type: rectangle, circle, linear, mirror, star."
        props.update(deepcopy(mask_props))
    by_name["add_video"]["inputSchema"]["properties"].update({
        "effect": _str("Optional scene effect applied to this clip only, e.g. 'Blur'."),
        "effect_strength": _num("Effect strength 0..100 (first effect parameter)."),
    })
    sd = by_name["save_draft"]
    sd["description"] = (
        "Write the draft to disk. New draft: creates the project folder in the CapCut projects folder "
        "(downloads/copies media, fills CapCut metadata). Opened project: writes the edits back into that "
        "project folder (backup of draft_content.json is kept; close the project in CapCut first). "
        "Call this last. CapCut must be restarted (or a project opened and closed) to pick up the changes."
    )
    sd["inputSchema"]["properties"]["draft_name"] = {
        "type": "string",
        "description": "New drafts only: project folder/name shown in CapCut (letters, digits, space, - _ .). "
                       "Default: the draft_id. Ignored for opened projects.",
    }
    for t in NEW_TOOLS:
        if t["name"] not in by_name:
            tools.append(t)
    if "capcut_healthcheck" not in by_name:
        tools.append(HEALTHCHECK_TOOL)


# --------------------------------------------------------------------------- validation

def validate_draft_id(value):
    if not isinstance(value, str) or not DRAFT_ID_RE.match(value):
        raise ValueError(
            f"invalid draft_id {value!r}: expected the id returned by create_draft/open_project (dfd_cat_<ts>_<hex8>)"
        )
    return value


def _is_public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve host {host!r}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _translate_windows_media(value):
    """Map 'C:\\Users\\<you>\\Videos\\x.mp4' to '/media/x.mp4' when VECTCUT_MEDIA_WIN_ROOT is set."""
    if not MEDIA_WIN_ROOT:
        return value
    root = MEDIA_WIN_ROOT.replace("/", "\\").rstrip("\\").lower()
    norm = value.replace("/", "\\")
    if norm.lower() == root:
        return MEDIA_DIR
    if norm.lower().startswith(root + "\\"):
        rest = norm[len(root):].lstrip("\\")
        return os.path.join(MEDIA_DIR, *[p for p in rest.split("\\") if p])
    return value


def resolve_media(key, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    v = value.strip()
    low = v.lower()

    if low.startswith(("http://", "https://")):
        host = urlparse(v).hostname
        if not host:
            raise ValueError(f"{key}: malformed URL {v!r}")
        if not _is_public_host(host):
            raise ValueError(
                f"{key}: host {host!r} resolves to a private/loopback address; only public hosts are allowed"
            )
        return v

    if "://" in v or low.startswith(("file:", "concat:", "pipe:", "data:")):
        raise ValueError(f"{key}: only http(s) URLs or files under {MEDIA_DIR}/ are allowed (got {v!r})")

    looks_like_path = (
        v.startswith(("/", "\\\\", "~")) or bool(WIN_ABS_RE.match(v)) or os.path.exists(v)
    )
    if key == "srt_path" and not looks_like_path:
        return v  # inline SRT text

    candidate = _translate_windows_media(v)
    media_root = os.path.realpath(MEDIA_DIR)
    real = os.path.realpath(candidate)
    if real != media_root and not real.startswith(media_root + os.sep):
        raise ValueError(
            f"{key}: local files must live under {MEDIA_DIR}/"
            + (f" (= {MEDIA_WIN_ROOT})" if MEDIA_WIN_ROOT else "")
            + f"; got {v!r}"
        )
    if not os.path.isfile(real):
        raise ValueError(f"{key}: file not found: {v!r}")
    return real


def _imported_track_names(script):
    return {t.name for t in getattr(script, "imported_tracks", []) if getattr(t, "name", "")}


def resolve_track_name(tool, clean, script, meta):
    """Opened projects: new content always lands on a track created in this session."""
    kind = TRACK_TOOLS.get(tool)
    name = clean.get("track_name")
    if tool == "add_video_keyframe":
        if not name or name not in script.tracks:
            raise ValueError(
                "add_video_keyframe in an opened project needs track_name of a track created in this session"
                f" (existing session tracks: {sorted(script.tracks)})"
            )
        return
    imported = _imported_track_names(script)
    if name is None or (isinstance(name, str) and not name.strip()):
        n = meta.get("next_no", 1)
        while True:
            cand = f"{kind}_mcp_{n}"
            n += 1
            if cand not in imported and cand not in script.tracks:
                break
        meta["next_no"] = n
        clean["track_name"] = cand
    elif not isinstance(name, str):
        raise ValueError("track_name must be a string")
    elif name in imported:
        raise ValueError(
            f"track_name {name!r} already exists in the project; new content always goes to a new track — "
            f"omit track_name or pass a new name (existing tracks: {sorted(imported)[:20]})"
        )


def sanitize_arguments(tool, args):
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("arguments must be a JSON object")
    schema = next((t for t in mcp_server.TOOLS if t["name"] == tool), None)
    if schema is None:
        raise ValueError(f"unknown tool {tool!r}")
    allowed = schema["inputSchema"].get("properties", {})
    clean, dropped = {}, []
    for k, v in args.items():
        (clean.__setitem__(k, v) if k in allowed else dropped.append(k))
    if dropped:
        log.warning("%s: dropped unsupported arguments %s", tool, dropped)
    for k in MEDIA_KEYS:
        if k in clean:
            clean[k] = resolve_media(k, clean[k])
    if tool in DRAFT_TOOLS:
        did = clean.get("draft_id")
        if not did:
            raise ValueError("draft_id is required: call create_draft or open_project first and pass its draft_id")
        validate_draft_id(did)
        if did not in draft_cache.DRAFT_CACHE:
            raise ValueError(
                f"unknown draft_id {did!r}: not in memory (new session or server restart?). "
                "Call create_draft/open_project again and redo the steps."
            )
        if tool in TRACK_TOOLS and did in OPENED:
            resolve_track_name(tool, clean, draft_cache.DRAFT_CACHE[did], OPENED[did])
    return clean


# --------------------------------------------------------------------------- paths

def _win_style(root):
    return bool(WIN_ABS_RE.match(root)) or root.startswith("\\\\")


def join_fwd(root, *parts):
    """CapCut stores draft_fold_path and material paths with forward slashes."""
    base = root.replace("\\", "/").rstrip("/")
    return "/".join([base, *parts])


def root_backslash(root):
    return root.replace("/", "\\").rstrip("\\") if _win_style(root) else root


def win_folder_path(win_root, folder):
    p = join_fwd(win_root, folder)
    return p.replace("/", "\\") if _win_style(win_root) else p


def slugify(name):
    s = re.sub(r"[^\w\- .()]", "_", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:60]


def _prefix_pattern(old_prefix):
    # the prefix must be followed by "/" or by the closing quote, so "foo" never matches "foo-bar"
    return re.compile(r'"' + re.escape(old_prefix) + r'((?:/[^"\\]*)?)"')


def rewrite_text(text, old_prefix, new_prefix_fwd):
    pattern = _prefix_pattern(old_prefix)

    def repl(m):
        return json.dumps(new_prefix_fwd + m.group(1), ensure_ascii=False)

    return pattern.subn(repl, text)


def rewrite_paths(draft_dir: Path, old_prefix: str, new_prefix_fwd: str) -> int:
    """Replace every JSON string starting with old_prefix by the CapCut-visible path."""
    replaced = 0
    for p in draft_dir.rglob("*"):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if old_prefix not in text:
            continue
        new_text, n = rewrite_text(text, old_prefix, new_prefix_fwd)
        if n:
            p.write_text(new_text, encoding="utf-8")
            replaced += n
    return replaced


def rename_timeline(draft_dir: Path):
    for rel in ("Timelines/project.json", "Timelines/project.json.bak", "timeline_layout.json"):
        p = draft_dir / rel
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            if "时间线01" in text:
                p.write_text(text.replace("时间线01", "Timeline 1"), encoding="utf-8")


# --------------------------------------------------------------------------- save (new drafts)

def collect_assets(draft_dir: Path, script):
    ok, failed = [], []

    def check(kind, material, sub):
        if not getattr(material, "remote_url", None):
            return
        f = draft_dir / "assets" / sub / material.material_name
        size = f.stat().st_size if f.is_file() else 0
        entry = {
            "type": kind,
            "name": material.material_name,
            "source": material.remote_url,
            "bytes": size,
            "duration_us": int(getattr(material, "duration", 0) or 0),
            "width": int(getattr(material, "width", 0) or 0),
            "height": int(getattr(material, "height", 0) or 0),
            "rel_path": f"assets/{sub}/{material.material_name}",
        }
        (ok if size > 0 else failed).append(entry)

    for a in script.materials.audios:
        check("music", a, "audio")
    for v in script.materials.videos:
        check("photo" if v.material_type == "photo" else "video", v, "image" if v.material_type == "photo" else "video")
    return ok, failed


def write_draft_meta(draft_dir: Path, folder: str, win_root: str, script, assets_ok):
    now_us = int(time.time() * 1_000_000)
    now_s = now_us // 1_000_000
    materials = []
    for a in assets_ok:
        materials.append({
            "ai_group_type": "",
            "create_time": now_s,
            "duration": a["duration_us"],
            "enter_from": 0,
            "extra_info": a["name"],
            "file_Path": join_fwd(win_root, folder, *a["rel_path"].split("/")),
            "height": a["height"],
            "id": str(uuid.uuid4()),
            "import_time": now_s,
            "import_time_ms": now_us,
            "item_source": 1,
            "material_color_tag": "",
            "md5": "",
            "metetype": a["type"],
            "roughcut_time_range": {"duration": a["duration_us"], "start": 0},
            "sub_time_range": {"duration": -1, "start": -1},
            "type": 0,
            "width": a["width"],
        })
    meta = {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_package_type": "",
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "", "draft_enterprise_id": "",
            "draft_enterprise_name": "", "enterprise_material": [],
        },
        "draft_fold_path": join_fwd(win_root, folder),
        "draft_id": str(uuid.uuid4()).upper(),
        "draft_is_ae_produce": False,
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_is_infinite_canvas_draft": False,
        "draft_is_invisible": False,
        "draft_is_pippit_draft": False,
        "draft_is_web_article_video": False,
        "draft_materials": [
            {"type": 0, "value": materials},
            {"type": 1, "value": []}, {"type": 2, "value": []}, {"type": 3, "value": []},
            {"type": 6, "value": []}, {"type": 7, "value": []}, {"type": 8, "value": []},
        ],
        "draft_materials_copied_info": [],
        "draft_name": folder,
        "draft_need_rename_folder": False,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": root_backslash(win_root),
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": sum(a["bytes"] for a in assets_ok),
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "pippit_avatar_url": "",
        "pippit_extra_info": "",
        "pippit_id": "",
        "pippit_user_name": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": int(script.duration),
    }
    (draft_dir / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (draft_dir / "draft_settings").write_text(
        "[General]\n"
        f"draft_create_time={now_s}\n"
        f"draft_last_edit_time={now_s}\n"
        "real_edit_seconds=0\n"
        "real_edit_keys=0\n"
        "cloud_last_modify_platform=windows\n",
        encoding="utf-8",
    )
    return meta


def finalize_save(args):
    args = args or {}
    draft_id = validate_draft_id(args.get("draft_id"))
    draft_name = args.get("draft_name") or ""
    if not isinstance(draft_name, str):
        raise ValueError("draft_name must be a string")
    script = draft_cache.DRAFT_CACHE.get(draft_id)
    if script is None:
        raise ValueError(
            f"unknown draft_id {draft_id!r}: not in memory (new session or server restart?). "
            "Call create_draft again and re-add the materials."
        )
    drafts = Path(DRAFTS_DIR)
    if not drafts.is_dir():
        raise RuntimeError(f"{DRAFTS_DIR} is not mounted; run the container with -v <CapCut projects dir>:{DRAFTS_DIR}")

    # Fresh identity + CapCut 9.3 schema markers; upstream dumps() keeps these fields.
    script.content["id"] = str(uuid.uuid4()).upper()
    script.content["version"] = CONTENT_VERSION
    script.content["new_version"] = NEW_VERSION

    started = time.time()
    res = save_draft_impl_fn(draft_id=draft_id, draft_folder=DRAFTS_DIR)
    status = save_task_cache.get_task_status(draft_id)
    if not (isinstance(res, dict) and res.get("success")) or status.get("status") != "completed":
        return {
            "success": False,
            "error": status.get("message") or (res or {}).get("error") or "save_draft failed",
            "task": status,
        }
    src = drafts / draft_id
    if not src.is_dir():
        return {"success": False, "error": f"upstream reported success but {src} is missing"}

    base = slugify(draft_name) or draft_id
    folder, dst = base, drafts / base
    if folder != draft_id:
        n = 2
        while dst.exists():  # never clobber an existing CapCut project; "name (2)" like CapCut itself
            folder = f"{base} ({n})"
            dst = drafts / folder
            n += 1
        src.rename(dst)

    win_root = CAPCUT_DRAFT_ROOT or DRAFTS_DIR
    rewritten = rewrite_paths(dst, f"{DRAFTS_DIR}/{draft_id}", join_fwd(win_root, folder))
    rename_timeline(dst)
    ok, failed = collect_assets(dst, script)
    write_draft_meta(dst, folder, win_root, script, ok)

    project_folder = win_folder_path(win_root, folder)
    result = {
        "draft_id": draft_id,
        "draft_name": folder,
        "project_folder": project_folder,
        "duration_sec": round(script.duration / 1e6, 3),
        "materials_ok": [{k: e[k] for k in ("type", "name", "bytes", "width", "height")} for e in ok],
        "materials_failed": [{k: e[k] for k in ("type", "name", "source")} for e in failed],
        "paths_rewritten": rewritten,
        "took_sec": round(time.time() - started, 1),
        "next_step": "Restart CapCut (or open and close any project) so it rescans the projects folder; "
                     "the project appears under draft_name.",
    }
    if failed:
        result["warning"] = f"{len(failed)} material(s) failed to download/copy; CapCut will show them as missing."
    log.info("saved %s -> %s (%d ok, %d failed, %d paths rewritten, %.1fs)",
             draft_id, project_folder, len(ok), len(failed), rewritten, result["took_sec"])
    return {"success": True, "result": result}


# --------------------------------------------------------------------------- existing projects

class _NoSegment:
    """Sentinel segment type for tracks whose type upstream does not know."""


RawTrack = None  # defined by _define_raw_track() once upstream's Track is imported


def _define_raw_track():
    global RawTrack

    class _RawTrack(Track):
        """An existing CapCut track kept as raw JSON; exported verbatim, never accepts new segments."""

        def __init__(self, raw, copy=True):
            try:
                tt = Track_type.from_name(raw.get("type", ""))
            except ValueError:
                tt = None
            render_index = max([int(s.get("render_index", 0) or 0) for s in raw.get("segments") or []], default=0)
            Track.__init__(self, tt, raw.get("name", "") or "", render_index, bool(raw.get("attribute", 0)))
            self.track_id = raw.get("id") or self.track_id
            self.raw_data = deepcopy(raw) if copy else raw

        @property
        def accept_segment_type(self):
            st = getattr(getattr(self.track_type, "value", None), "segment_type", None)
            return st if st is not None else _NoSegment

        def add_segment(self, segment):
            raise ValueError(f"existing CapCut track {self.name!r} is read-only; use a new track_name")

        def export_json(self):
            return deepcopy(self.raw_data)

        @property
        def end_time(self):
            return max([te.seg_end(s) for s in self.raw_data.get("segments") or []], default=0)

    RawTrack = _RawTrack


def serialize_script(script, profile=None):
    """Pure serialisation of a Script_file (new or opened): returns (content dict, number of existing tracks)."""
    profile = profile or draft_profiles.get_draft_profile()
    meta = getattr(script, "_vectcut_opened", None)
    content = {k: deepcopy(v) for k, v in script.content.items() if k not in ("materials", "tracks")}
    mats = {k: list(v) for k, v in script.materials.export_json(profile.is_capcut_env).items()}
    for k, lst in (script.imported_materials or {}).items():
        mats.setdefault(k, []).extend(lst)
    content["materials"] = mats

    exported = [t.export_json() for t in script.imported_tracks]
    n_existing = len(exported)
    new_tracks = sorted(script.tracks.values(), key=lambda t: t.render_index)
    if meta is not None:
        new_tracks = [t for t in new_tracks if t.segments]
    exported.extend(t.export_json() for t in new_tracks)
    tri = bool(meta and meta.get("tri_consistent"))
    for idx, t in enumerate(exported):
        for seg in t.get("segments") or []:
            if tri or idx >= n_existing:
                seg["track_render_index"] = idx
    content["tracks"] = exported
    content["fps"] = script.fps
    cc = dict(content.get("canvas_config") or {})
    cc.update({"width": script.width, "height": script.height})
    cc.setdefault("ratio", "original")
    content["canvas_config"] = cc
    content["duration"] = max([te.seg_end(s) for t in exported for s in t.get("segments") or []], default=0)
    if meta is None and profile.platform:  # new drafts keep upstream's behaviour
        content["platform"] = dict(profile.platform)
        content["last_modified_platform"] = dict(profile.platform)
    return content, n_existing


def _dumps_override(self, profile=None):
    content, _ = serialize_script(self, profile)
    return json.dumps(content, ensure_ascii=False, indent=4)


def _sha256(data: bytes):
    return hashlib.sha256(data).hexdigest()


def validate_project_name(name):
    if not isinstance(name, str) or not PROJECT_NAME_RE.match(name) or name.startswith(".") or name.strip() != name:
        raise ValueError(f"invalid project name {name!r}: use a folder name exactly as listed by list_projects")
    drafts = Path(DRAFTS_DIR)
    if not drafts.is_dir():
        raise RuntimeError(f"{DRAFTS_DIR} is not mounted")
    p = drafts / name
    if os.path.dirname(os.path.realpath(p)) != os.path.realpath(drafts):
        raise ValueError(f"invalid project name {name!r}")
    if not p.is_dir():
        raise ValueError(f"project {name!r} not found in the CapCut projects folder; call list_projects")
    if not (p / "draft_content.json").is_file():
        raise ValueError(f"{name!r} has no draft_content.json; it is not a CapCut project folder")
    return p


def _now_us():
    return int(time.time() * 1_000_000)


def _update_meta_file(project_dir: Path, **fields):
    p = project_dir / "draft_meta_info.json"
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    for k, v in fields.items():
        if callable(v):
            data[k] = v(data.get(k))
        else:
            data[k] = v
    p.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return True


def _update_settings_file(project_dir: Path, **fields):
    p = project_dir / "draft_settings"
    if not p.is_file():
        return
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    out = []
    seen = set()
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in fields:
            out.append(f"{key}={fields[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in fields.items():
        if k not in seen:
            out.append(f"{k}={v}")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def copy_project(src: Path, copy_name):
    drafts = Path(DRAFTS_DIR)
    base = slugify(copy_name) if copy_name else ""
    base = base or slugify(f"{src.name}-mcp") or f"{src.name}-mcp"
    folder, dst = base, drafts / base
    n = 2
    while dst.exists():
        folder = f"{base} ({n})"
        dst = drafts / folder
        n += 1
    started = time.time()
    try:
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.vectcut-bak-*"))
    except Exception:
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        raise
    win_root = CAPCUT_DRAFT_ROOT or DRAFTS_DIR
    rewritten = rewrite_paths(dst, join_fwd(win_root, src.name), join_fwd(win_root, folder))
    now = _now_us()
    _update_meta_file(dst, draft_id=str(uuid.uuid4()).upper(), draft_name=folder,
                      draft_fold_path=join_fwd(win_root, folder), tm_draft_create=now, tm_draft_modified=now,
                      tm_draft_removed=0)
    _update_settings_file(dst, draft_create_time=now // 1_000_000, draft_last_edit_time=now // 1_000_000)
    log.info("copied project %r -> %r (%d paths rewritten, %.1fs)", src.name, folder, rewritten, time.time() - started)
    return folder, dst, rewritten, round(time.time() - started, 1)


def load_project(project_dir: Path, folder: str, mode: str):
    content_path = project_dir / "draft_content.json"
    raw_bytes = content_path.read_bytes()
    content = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(content, dict) or "tracks" not in content or "materials" not in content:
        raise ValueError(f"{folder}: draft_content.json is not a CapCut draft (no tracks/materials)")

    script = Script_file(**pj_util.provide_ctor_defaults(Script_file))
    script.save_path = None
    script.content = content
    script.fps = content.get("fps", 30)
    script.duration = int(content.get("duration") or 0)
    cc = content.get("canvas_config") or {}
    script.width = int(cc.get("width") or 1080)
    script.height = int(cc.get("height") or 1920)
    script.imported_materials = deepcopy(content.get("materials") or {})
    script.imported_tracks = [RawTrack(t) for t in content.get("tracks") or []]
    script.dumps = types.MethodType(_dumps_override, script)

    tracks = content.get("tracks") or []
    tri = all(int(s.get("track_render_index", -1)) == i for i, t in enumerate(tracks) for s in t.get("segments") or [])
    draft_id = f"dfd_cat_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    meta = {
        "folder": folder, "dir": project_dir, "mode": mode, "content_id": content.get("id"),
        "sha256": _sha256(raw_bytes), "next_no": 1, "opened_at": time.time(),
        "edits": 0, "adds": False, "saves": 0, "tri_consistent": tri,
    }
    script._vectcut_opened = meta
    draft_cache.update_cache(draft_id, script)
    OPENED[draft_id] = meta
    log.info("opened %r (%s) as %s: %d tracks, %.1fs, tri_consistent=%s",
             folder, mode, draft_id, len(tracks), script.duration / 1e6, tri)
    return draft_id


def describe(draft_id):
    script = draft_cache.DRAFT_CACHE[draft_id]
    content, n_existing = serialize_script(script)
    meta = OPENED.get(draft_id)
    win_root = CAPCUT_DRAFT_ROOT or DRAFTS_DIR
    result = {
        "draft_id": draft_id,
        "source": ({"mode": meta["mode"], "project_name": meta["folder"],
                    "project_folder": win_folder_path(win_root, meta["folder"]),
                    "edits_applied": meta["edits"], "adds_started": meta["adds"], "saves": meta["saves"]}
                   if meta else "new draft"),
        "canvas": {"width": script.width, "height": script.height},
        "fps": script.fps,
        "duration_sec": round(content["duration"] / 1e6, 3),
        "tracks": te.summarize(content, n_existing=n_existing),
        "materials": {k: len(v) for k, v in content["materials"].items() if v},
    }
    if meta:
        result["hint"] = ("Existing tracks are editable with delete_range/retime_range/set_segment/edit_text/"
                          "set_track/blur_region/edit_mask (before any add_*); new content goes to new tracks via add_*; "
                          "finish with save_draft.")
    return result


def open_project(args):
    args = args or {}
    name = args.get("name")
    mode = (args.get("mode") or "copy").strip().lower()
    copy_name = args.get("copy_name")
    if mode not in ("copy", "in_place"):
        raise ValueError("mode must be 'copy' or 'in_place'")
    if copy_name is not None and not isinstance(copy_name, str):
        raise ValueError("copy_name must be a string")
    src = validate_project_name(name)
    result = {"mode": mode, "source_project": name}
    if mode == "copy":
        folder, target, rewritten, took = copy_project(src, copy_name)
        result.update({"paths_rewritten": rewritten, "copy_took_sec": took})
    else:
        folder, target = name, src
    already = [d for d, m in OPENED.items() if m["dir"] == target]
    if already:
        result["warning"] = (f"this project is already open as {already}; saving from two draft_ids will conflict "
                             "(the second save is rejected as 'changed on disk')")
    draft_id = load_project(target, folder, mode)
    win_root = CAPCUT_DRAFT_ROOT or DRAFTS_DIR
    result.update({
        "draft_id": draft_id,
        "project_name": folder,
        "project_folder": win_folder_path(win_root, folder),
        "summary": describe(draft_id),
        "hint": "Use describe_draft indices with the editing tools, then add_* for new content, then save_draft. "
                + ("Close this project in CapCut before save_draft." if mode == "in_place"
                   else "CapCut shows the copy after a restart."),
    })
    return result


def list_projects():
    drafts = Path(DRAFTS_DIR)
    if not drafts.is_dir():
        raise RuntimeError(f"{DRAFTS_DIR} is not mounted")
    win_root = CAPCUT_DRAFT_ROOT or DRAFTS_DIR
    items = []
    for p in drafts.iterdir():
        if not p.is_dir() or p.name.startswith("."):
            continue
        content = p / "draft_content.json"
        if not content.is_file():
            continue
        entry = {"name": p.name}
        try:
            st = content.stat()
            entry["content_kb"] = round(st.st_size / 1024, 1)
            modified_us = int(st.st_mtime * 1_000_000)
        except OSError:
            modified_us = 0
        meta_path = p / "draft_meta_info.json"
        if meta_path.is_file():
            try:
                m = json.loads(meta_path.read_text(encoding="utf-8"))
                if m.get("tm_draft_modified"):
                    modified_us = int(m["tm_draft_modified"])
                if m.get("tm_draft_create"):
                    entry["created"] = datetime.fromtimestamp(int(m["tm_draft_create"]) / 1e6, tz=timezone.utc).isoformat(timespec="seconds")
                if m.get("tm_duration") is not None:
                    entry["duration_sec"] = round(int(m["tm_duration"]) / 1e6, 3)
                if m.get("draft_timeline_materials_size_") is not None:
                    entry["media_mb"] = round(int(m["draft_timeline_materials_size_"]) / 1e6, 1)
                if m.get("draft_name") and m["draft_name"] != p.name:
                    entry["draft_name"] = m["draft_name"]
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                entry["meta"] = "unreadable"
        entry["modified"] = datetime.fromtimestamp(modified_us / 1e6, tz=timezone.utc).isoformat(timespec="seconds") if modified_us else None
        entry["_sort"] = modified_us
        opened = [d for d, mm in OPENED.items() if mm["dir"] == p]
        if opened:
            entry["opened_as"] = opened
        if DRAFT_ID_RE.match(p.name):
            entry["note"] = "leftover of a failed save (dfd_cat_*); safe to delete"
        items.append(entry)
    items.sort(key=lambda e: e.pop("_sort"), reverse=True)
    return {"folder": root_backslash(win_root), "count": len(items), "projects": items[:100]}


def run_edit(tool, clean):
    draft_id = clean["draft_id"]
    meta = OPENED.get(draft_id)
    if meta is None:
        raise ValueError("timeline editing tools work only on projects opened with open_project")
    if meta["adds"]:
        raise ValueError("timeline edits must be applied before add_* calls on this draft; "
                         "open the project again to edit it")
    script = draft_cache.DRAFT_CACHE[draft_id]
    work = {
        "tracks": deepcopy([t.raw_data for t in script.imported_tracks]),
        "materials": deepcopy(script.imported_materials),
        "duration": script.duration,
    }
    changed = te.apply_timeline_operation(work, tool, clean,
                                          cache_root=te.cache_root_from_draft_root(CAPCUT_DRAFT_ROOT))
    te.validate_timeline(work)
    dangling = te.dangling_refs(work)
    script.imported_materials = work["materials"]
    script.imported_tracks = [RawTrack(t, copy=False) for t in work["tracks"]]
    script.duration = int(work["duration"])
    meta["edits"] += 1
    summary = te.summarize(work)
    if tool in ("delete_range", "retime_range"):
        tracks = summary
    else:
        wanted = {clean.get("track")}
        if isinstance(changed, dict) and "overlay_track" in changed:
            wanted.add(changed["overlay_track"])
        tracks = [t for t in summary if t["index"] in wanted]
    log.info("%s on %s: %s", tool, draft_id, json.dumps(changed, ensure_ascii=False)[:300])
    out = {"changed": changed, "duration_sec": round(script.duration / 1e6, 3), "tracks": tracks,
           "edits_applied": meta["edits"]}
    if dangling:
        out["warning"] = f"{len(dangling)} unresolved material reference(s) (pre-existing, CapCut tolerates them)"
    return out


def _prune_backups(project_dir: Path, prefix: str):
    backups = sorted(project_dir.glob(f"{prefix}.vectcut-bak-*"))
    for old in backups[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def finalize_save_opened(args):
    draft_id = validate_draft_id(args.get("draft_id"))
    meta = OPENED[draft_id]
    script = draft_cache.DRAFT_CACHE[draft_id]
    project_dir: Path = meta["dir"]
    root = project_dir / "draft_content.json"
    if not root.is_file():
        return {"success": False, "error": f"{root} disappeared; the project was deleted or renamed"}
    old = root.read_bytes()
    if _sha256(old) != meta["sha256"]:
        return {"success": False,
                "error": "draft_content.json changed on disk since open_project (edited in CapCut?). "
                         "Call open_project again and redo the edits; nothing was written."}
    stage = Path(STAGE_DIR)
    stage.mkdir(parents=True, exist_ok=True)
    started = time.time()
    res = save_draft_impl_fn(draft_id=draft_id, draft_folder=STAGE_DIR)
    status = save_task_cache.get_task_status(draft_id)
    stage_dir = stage / draft_id
    if not (isinstance(res, dict) and res.get("success")) or status.get("status") != "completed":
        shutil.rmtree(stage_dir, ignore_errors=True)
        return {"success": False, "error": status.get("message") or (res or {}).get("error") or "save failed",
                "task": status}
    staged = stage_dir / "draft_content.json"
    if not staged.is_file():
        return {"success": False, "error": f"upstream reported success but {staged} is missing"}

    win_root = CAPCUT_DRAFT_ROOT or DRAFTS_DIR
    text = staged.read_text(encoding="utf-8")
    text, rewritten = rewrite_text(text, f"{STAGE_DIR}/{draft_id}", join_fwd(win_root, meta["folder"]))
    data = json.loads(text)
    new_bytes = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    added_bytes, copied = 0, []
    assets = stage_dir / "assets"
    if assets.is_dir():
        for p in assets.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(stage_dir)
            dst = project_dir / rel
            if dst.is_file() and dst.stat().st_size == p.stat().st_size:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
            added_bytes += p.stat().st_size
            copied.append(str(rel).replace(os.sep, "/"))

    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = project_dir / f"draft_content.json.vectcut-bak-{ts}"
    backup.write_bytes(old)
    meta_path = project_dir / "draft_meta_info.json"
    if meta_path.is_file():
        (project_dir / f"draft_meta_info.json.vectcut-bak-{ts}").write_bytes(meta_path.read_bytes())
    _prune_backups(project_dir, "draft_content.json")
    _prune_backups(project_dir, "draft_meta_info.json")

    root.write_bytes(new_bytes)
    mirrors = []
    for pattern in MIRROR_CANDIDATES:
        for p in project_dir.glob(pattern):
            if p.is_file() and p != root:
                try:
                    if p.read_bytes() == old:
                        p.write_bytes(new_bytes)
                        mirrors.append(str(p.relative_to(project_dir)).replace(os.sep, "/"))
                except OSError as exc:
                    log.warning("mirror %s not updated: %s", p, exc)
    now = _now_us()
    _update_meta_file(project_dir, tm_draft_modified=now, tm_duration=int(data.get("duration") or 0),
                      draft_timeline_materials_size_=lambda v: int(v or 0) + added_bytes)
    _update_settings_file(project_dir, draft_last_edit_time=now // 1_000_000)
    shutil.rmtree(stage_dir, ignore_errors=True)
    meta["sha256"] = _sha256(new_bytes)
    meta["saves"] += 1

    ok, failed = collect_assets(project_dir, script)
    n_existing = len(script.imported_tracks)
    added_tracks = [{"name": t["name"], "type": t["type"], "segments": len(t.get("segments") or [])}
                    for t in data["tracks"][n_existing:]]
    result = {
        "draft_id": draft_id,
        "mode": meta["mode"],
        "project_name": meta["folder"],
        "project_folder": win_folder_path(win_root, meta["folder"]),
        "duration_sec": round(int(data.get("duration") or 0) / 1e6, 3),
        "tracks": len(data["tracks"]),
        "edits_applied": meta["edits"],
        "added_tracks": added_tracks,
        "materials_ok": [{k: e[k] for k in ("type", "name", "bytes", "width", "height")} for e in ok],
        "materials_failed": [{k: e[k] for k in ("type", "name", "source")} for e in failed],
        "assets_copied": copied,
        "mirrors_updated": mirrors,
        "backup": backup.name,
        "paths_rewritten": rewritten,
        "took_sec": round(time.time() - started, 1),
        "next_step": "Restart CapCut (or open and close any project) to see the changes; "
                     f"to roll back, rename {backup.name} to draft_content.json.",
    }
    if args.get("draft_name"):
        result["note"] = "draft_name is ignored for opened projects (edits are written into the opened folder)"
    if failed:
        result["warning"] = f"{len(failed)} material(s) failed to download/copy; CapCut will show them as missing."
    log.info("saved opened %s -> %s (%d edits, %d new tracks, %d assets, %.1fs)",
             draft_id, result["project_folder"], meta["edits"], len(added_tracks), len(copied), result["took_sec"])
    return {"success": True, "result": result}


MASK_ALIASES = {
    "rectangle": "Rectangle", "rect": "Rectangle", "circle": "Circle", "ellipse": "Circle",
    "linear": "Split", "line": "Split", "split": "Split", "mirror": "Filmstrip", "filmstrip": "Filmstrip",
    "star": "Stars", "stars": "Stars", "pentagram": "Stars",
}


def normalize_mask_args(script, clean):
    """Map user-facing mask names/units onto upstream's CapCut_Mask_type + pixel-based add_mask()."""
    mt = clean.get("mask_type")
    if mt is None:
        return
    if not isinstance(mt, str) or not mt.strip():
        raise ValueError("mask_type must be a string: rectangle, circle, linear, mirror, star")
    key = mt.strip().lower()
    from pyJianYingDraft.metadata.capcut_mask_meta import CapCut_Mask_type  # noqa: WPS433
    members = {m.name.lower(): m.name for m in CapCut_Mask_type}
    name = MASK_ALIASES.get(key) or members.get(key)
    if name is None:
        raise ValueError(f"unknown mask_type {mt!r}; use rectangle, circle, linear, mirror or star")
    clean["mask_type"] = name
    # upstream add_mask() takes the centre as a pixel offset from the clip centre; our schema uses CapCut units (-1..1)
    for key_, dim in (("mask_center_x", script.width), ("mask_center_y", script.height)):
        v = clean.get(key_)
        if v is None:
            continue
        v = float(v)
        if not (-1.5 <= v <= 1.5):
            raise ValueError(f"{key_} must be within -1..1 (0 = clip centre)")
        clean[key_] = v * dim / 2.0
    if name != "Rectangle":
        for k in ("mask_rect_width", "mask_round_corner"):
            if clean.pop(k, None) is not None:
                log.warning("%s ignored for mask_type %s", k, name)


def _fix_text_refs(script):
    """Upstream text segments reference their Speed material, but add_segment() never exports a speeds
    entry for texts -> dangling ref. CapCut's own text segments carry no speed ref, so drop it."""
    fixed = 0
    for track in script.tracks.values():
        if getattr(track.track_type, "name", "") != "text":
            continue
        for seg in track.segments:
            sid = getattr(getattr(seg, "speed", None), "global_id", None)
            if sid and sid in seg.extra_material_refs and not any(sp.global_id == sid for sp in script.materials.speeds):
                seg.extra_material_refs = [r for r in seg.extra_material_refs if r != sid]
                fixed += 1
    return fixed


def _apply_segment_effect(script, track_name, effect, strength):
    members = {m.name.lower(): m for m in CapCut_Video_scene_effect_type}
    members.update({m.value.name.lower(): m for m in CapCut_Video_scene_effect_type})
    enum = members.get(str(effect).strip().lower())
    if enum is None:
        raise ValueError(f"unknown effect {effect!r}; examples: Blur, Blurry_Focus (see CapCut_Video_scene_effect_type)")
    track = script.tracks.get(track_name or "video")
    if track is None or not track.segments:
        raise ValueError("effect: could not locate the segment just added")
    seg = track.segments[-1]
    params = None
    if strength is not None:
        s = float(strength)
        if not (0 <= s <= 100):
            raise ValueError("effect_strength must be within 0..100")
        params = [s]
    seg.add_effect(enum, params)
    script.materials.video_effects.append(seg.effects[-1])
    return enum.value.name


# --------------------------------------------------------------------------- healthcheck

def healthcheck():
    checks = []

    def add(name, ok, detail, critical=True):
        checks.append({"name": name, "ok": bool(ok), "critical": critical, "detail": detail})

    add("wrapper", True,
        f"capcut-mcp {WRAPPER_VERSION}; upstream VectCutAPI {UPSTREAM_SHA[:8]}; python {sys.version.split()[0]}")
    prof = draft_profiles.get_draft_profile()
    platform = prof.platform or {}
    add("profile",
        PROFILE_MODE == "upstream" or (prof.content_file == "draft_content.json" and prof.is_capcut_env),
        f"mode={PROFILE_MODE} name={prof.name} template={prof.template_dir} content_file={prof.content_file} "
        f"is_capcut_env={prof.is_capcut_env} app_version={platform.get('app_version')} os={platform.get('os')}")
    tpl = Path(REPO) / prof.template_dir
    add("template_dir", tpl.is_dir(), str(tpl))

    drafts = Path(DRAFTS_DIR)
    if drafts.is_dir():
        probe = drafts / ".vectcut_probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            n = sum(1 for p in drafts.iterdir() if p.is_dir())
            add("drafts_mount", True, f"{DRAFTS_DIR} is writable; {n} project folder(s) present")
        except Exception as exc:  # noqa: BLE001
            add("drafts_mount", False, f"{DRAFTS_DIR} exists but is not writable: {exc}")
        try:
            projects = sum(1 for p in drafts.iterdir()
                           if p.is_dir() and not p.name.startswith(".") and (p / "draft_content.json").is_file())
            add("projects_found", True, f"{projects} CapCut project(s) openable with open_project", critical=False)
        except Exception as exc:  # noqa: BLE001
            add("projects_found", False, str(exc), critical=False)
    else:
        add("drafts_mount", False, f"{DRAFTS_DIR} is not mounted (-v <CapCut projects dir>:{DRAFTS_DIR})")

    add("capcut_draft_root", bool(CAPCUT_DRAFT_ROOT),
        CAPCUT_DRAFT_ROOT or "CAPCUT_DRAFT_ROOT is empty: embedded paths would be container paths")

    try:
        stage = Path(STAGE_DIR)
        stage.mkdir(parents=True, exist_ok=True)
        probe = stage / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("stage_dir", True, f"{STAGE_DIR} is writable (staging for opened projects)")
    except Exception as exc:  # noqa: BLE001
        add("stage_dir", False, f"{STAGE_DIR}: {exc}")

    media = Path(MEDIA_DIR)
    if media.is_dir():
        files = sorted(p.name for p in media.iterdir() if p.is_file())[:10]
        add("media_mount", True, f"{MEDIA_DIR} mounted"
            + (f" (= {MEDIA_WIN_ROOT})" if MEDIA_WIN_ROOT else "")
            + f"; files: {files or 'none'}", critical=False)
    else:
        add("media_mount", False, f"{MEDIA_DIR} is not mounted; only http(s) media URLs will work", critical=False)

    try:
        out = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=10)
        add("ffprobe", out.returncode == 0, (out.stdout or out.stderr).splitlines()[0][:120])
    except Exception as exc:  # noqa: BLE001
        add("ffprobe", False, str(exc))

    try:
        import requests  # noqa: WPS433 (upstream dependency)
        r = requests.head(EGRESS_PROBE_URL, timeout=5, allow_redirects=True)
        add("internet_egress", r.status_code < 500, f"HEAD {EGRESS_PROBE_URL} -> {r.status_code}", critical=False)
    except Exception as exc:  # noqa: BLE001
        add("internet_egress", False, f"{EGRESS_PROBE_URL}: {exc}", critical=False)

    add("drafts_in_memory", True,
        f"{len(draft_cache.DRAFT_CACHE)} draft(s) in this session, {len(OPENED)} opened project(s)", critical=False)
    return {"ok": all(c["ok"] for c in checks if c["critical"]), "checks": checks}


# --------------------------------------------------------------------------- dispatch

def execute_tool_wrapper(tool_name, arguments):
    try:
        if tool_name == "capcut_healthcheck":
            return {"success": True, "result": healthcheck()}
        if tool_name == "list_projects":
            return {"success": True, "result": list_projects()}
        if tool_name == "open_project":
            return {"success": True, "result": open_project(sanitize_arguments(tool_name, arguments))}
        if tool_name == "describe_draft":
            return {"success": True, "result": describe(sanitize_arguments(tool_name, arguments)["draft_id"])}
        if tool_name in EDIT_TOOLS:
            return {"success": True, "result": run_edit(tool_name, sanitize_arguments(tool_name, arguments))}
        if tool_name == "save_draft":
            clean = sanitize_arguments(tool_name, arguments)
            if clean["draft_id"] in OPENED:
                return finalize_save_opened(clean)
            return finalize_save(clean)
        clean = sanitize_arguments(tool_name, arguments)
        effect = clean.pop("effect", None) if tool_name == "add_video" else None
        effect_strength = clean.pop("effect_strength", None) if tool_name == "add_video" else None
        if tool_name in ("add_video", "add_image"):
            normalize_mask_args(draft_cache.DRAFT_CACHE[clean["draft_id"]], clean)
        res = ORIG_EXECUTE_TOOL(tool_name, clean)
        ok = isinstance(res, dict) and res.get("success")
        if ok and tool_name in TRACK_TOOLS and clean.get("draft_id") in OPENED:
            OPENED[clean["draft_id"]]["adds"] = True
        if ok and tool_name in ("add_text", "add_subtitle"):
            _fix_text_refs(draft_cache.DRAFT_CACHE[clean["draft_id"]])
        if tool_name == "create_draft" and ok:
            res["result"].pop("draft_url", None)
            res["result"]["hint"] = "Pass this draft_id to add_* calls, then call save_draft."
        elif tool_name == "get_video_duration" and ok:
            d = res["result"].get("duration")
            if isinstance(d, dict):  # upstream shape: {"success", "output", "error"}
                res["result"] = {"duration_sec": d.get("output"), "source": clean.get("video_url")}
                if not d.get("success"):
                    res = {"success": False, "error": d.get("error") or "ffprobe failed"}
        elif tool_name == "add_video" and ok and effect:
            try:
                name = _apply_segment_effect(draft_cache.DRAFT_CACHE[clean["draft_id"]], clean.get("track_name"),
                                             effect, effect_strength)
                res["result"]["effect"] = name
            except ValueError as exc:
                res["result"]["warning"] = f"clip added but effect not applied: {exc}"
        if ok and tool_name in TRACK_TOOLS and clean.get("draft_id") in OPENED and isinstance(res.get("result"), dict):
            res["result"]["track_name"] = clean.get("track_name")
        return res
    except ValueError as exc:
        log.warning("%s rejected: %s", tool_name, exc)
        return {"success": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.error("%s crashed: %s\n%s", tool_name, exc, traceback.format_exc())
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def _rpc(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    msg["error" if error else "result"] = error if error else result
    return json.dumps(msg, ensure_ascii=False)


def handle_line(line):
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return _rpc(None, error={"code": -32700, "message": f"parse error: {exc}"})
    method = req.get("method", "")
    rid = req.get("id")
    if method.startswith("notifications/"):
        return None
    if method == "ping":
        return _rpc(rid, {})
    if method == "resources/list":
        return _rpc(rid, {"resources": []})
    if method == "prompts/list":
        return _rpc(rid, {"prompts": []})
    if method == "initialize":
        data = json.loads(mcp_server.handle_request(line))
        if "result" in data:
            data["result"]["serverInfo"] = {"name": "capcut-mcp", "version": WRAPPER_VERSION}
        return json.dumps(data, ensure_ascii=False)
    return mcp_server.handle_request(line)


def main():
    bootstrap()
    patch_profile()
    patch_tools()
    mcp_server.execute_tool = execute_tool_wrapper
    log.info("ready: capcut-mcp %s, %d tools; drafts=%s media=%s stage=%s capcut_root=%r profile_mode=%s",
             WRAPPER_VERSION, len(mcp_server.TOOLS), DRAFTS_DIR, MEDIA_DIR, STAGE_DIR, CAPCUT_DRAFT_ROOT, PROFILE_MODE)
    while True:
        line = sys.stdin.readline()
        if not line:
            log.info("stdin closed, exiting")
            break
        if not line.strip():
            continue
        try:
            response = handle_line(line)
        except Exception as exc:  # noqa: BLE001
            log.error("unhandled: %s\n%s", exc, traceback.format_exc())
            response = _rpc(None, error={"code": -32603, "message": str(exc)})
        if response:
            REAL_STDOUT.write(response + "\n")
            REAL_STDOUT.flush()


if __name__ == "__main__":
    main()
