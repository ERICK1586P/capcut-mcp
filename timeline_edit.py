"""
timeline_edit.py — pure operations on a CapCut ``draft_content.json``-like structure.

Everything here works on plain dicts (``{"tracks": [...], "materials": {...}, ...}``)
exactly as CapCut wrote them; nothing is reconstructed into library objects, so any
field we do not understand survives untouched.  Time unit is microseconds (int).

Used by entry.py for projects opened with ``open_project`` and by test_timeline.py
on the host without Docker.
"""

import json
import os
import re
import uuid
from copy import deepcopy

US = 1_000_000
PLACEHOLDER = "##_draftpath_placeholder_0E685133-18CE-45ED-8CB8-2904A212EC80_##"
MEDIA_TRACK_TYPES = ("video", "audio")
MAX_SUMMARY_SEGMENTS = 200

# Material templates captured from a CapCut 9.3 project in which the user built the
# blur overlay by hand (segment copy on an upper track + rectangle mask + "Blur").
BLUR_EFFECT_ID = "7399464929830423813"
BLUR_EFFECT_MD5 = "2db7bf49d9349e308ef0f46c39b14abf"
RECT_MASK_CACHE_ID = "1068046526"
RECT_MASK_MD5 = "02b8999168d121538a98ea59127483ef"

BLUR_EFFECT_TEMPLATE = {
    "effect_id": BLUR_EFFECT_ID, "resource_id": BLUR_EFFECT_ID, "name": "Blur",
    "type": "video_effect", "sub_type": 0, "bind_segment_id": "", "transparent_params": "",
    "path": "", "value": 1.0, "category_id": "1111", "category_name": "Video effects",
    "platform": "all", "apply_target_type": 0, "source_platform": 1, "version": "",
    "item_effect_type": 0,
    "adjust_params": [{"name": "effects_adjust_blur", "value": 0.5, "default_value": 0.5}],
    "time_range": None, "formula_id": "", "apply_time_range": None, "render_index": 11000,
    "track_render_index": 0, "common_keyframes": [], "request_id": "", "algorithm_artifact_path": "",
    "disable_effect_faces": [], "covering_relation_change": 0, "enable_mask": True, "effect_mask": [],
    "enable_video_mask_stroke": True, "enable_video_mask_shadow": True, "aigc_current_artifact_path": "",
    "aigc_current_artifact_cnt": 0, "aigc_current_artifact_freeze_time": -1,
    "aigc_current_artifact_freeze_progress": 0.0, "sdk_extra": "",
}

RECT_MASK_TEMPLATE = {
    "type": "mask", "category": "video", "category_name": "", "category_id": "", "panel": "",
    "is_old_version": False, "resource_id": "7374021450748924432",
    "constant_material_id": "6E71F7B9-C3B9-42d3-98E7-93F08F5B611A", "name": "Rectangle",
    "resource_type": "rectangle", "path": "", "position_info": "",
    "config": {"width": 1.0, "height": 0.5, "centerX": 0.0, "centerY": 0.0, "rotation": 0.0,
               "feather": 0.0, "expansion": 0.0, "roundCorner": 0.0, "invert": False, "aspectRatio": 1.0},
    "text_config": {"content": "", "font_name": "", "font_path": "", "font_resource_id": "", "font_size": 15.0,
                    "bold_width": 0.0, "italic_degree": 0, "has_underline": False, "line_gap": 0.0,
                    "char_spacing": 0.0, "align_type": 15, "scale": 1.0},
    "platform": "all", "loader_work_space": "", "track_segment": "", "contour_path": None, "source_platform": 0,
}

BLUR_TRACK_NAME = "blur_mcp"


class EditError(ValueError):
    """Raised for every user-facing problem (bad index, unsupported segment, overlap...)."""


# --------------------------------------------------------------------------- basics

def to_us(seconds):
    return int(round(float(seconds) * US))


def to_sec(us_value):
    return round(int(us_value) / US, 3)


def new_id(like=None):
    """Fresh id in the style of ``like`` (GUID with dashes or 32-hex)."""
    if like and "-" in str(like):
        return str(uuid.uuid4()).upper()
    return uuid.uuid4().hex


def build_index(materials):
    by_id = {}
    for cat, lst in (materials or {}).items():
        if not isinstance(lst, list):
            continue
        for m in lst:
            if isinstance(m, dict) and isinstance(m.get("id"), str):
                by_id[m["id"]] = (cat, m)
    return by_id


def seg_start(seg):
    return int((seg.get("target_timerange") or {}).get("start", 0))


def seg_dur(seg):
    return int((seg.get("target_timerange") or {}).get("duration", 0))


def seg_end(seg):
    return seg_start(seg) + seg_dur(seg)


def _speed(seg):
    try:
        v = float(seg.get("speed") or 1.0)
    except (TypeError, ValueError):
        v = 1.0
    return v if v > 0 else 1.0


def classify(track, seg, by_id):
    """media (video/audio with a source range), photo (still image), static (everything else)."""
    if track.get("type") not in MEDIA_TRACK_TYPES:
        return "static"
    cat, m = by_id.get(seg.get("material_id"), (None, None))
    if m is not None and m.get("type") == "photo":
        return "photo"
    if seg.get("source_timerange"):
        return "media"
    return "static"


def _refs(seg):
    return list(seg.get("extra_material_refs") or [])


def _material_duration(seg, by_id):
    cat, m = by_id.get(seg.get("material_id"), (None, None))
    if m is None:
        return None
    try:
        d = int(m.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    return d if d > 0 else None


def check_editable(seg, by_id, what="edit"):
    if seg.get("common_keyframes"):
        raise EditError(f"{what}: segment {seg.get('id')} has keyframes; not supported, edit it in CapCut")
    for r in _refs(seg):
        cat, m = by_id.get(r, (None, None))
        if cat == "speeds" and m.get("curve_speed"):
            raise EditError(f"{what}: segment {seg.get('id')} uses curve speed; not supported")
        if cat == "transitions":
            raise EditError(f"{what}: segment {seg.get('id')} has a transition; remove it in CapCut first")


def _set_speed(seg, by_id, speed):
    seg["speed"] = float(speed)
    for r in _refs(seg):
        cat, m = by_id.get(r, (None, None))
        if cat == "speeds":
            m["speed"] = float(speed)


def _dup_refs(seg, materials, by_id, dup_primary):
    """Give ``seg`` its own copies of every per-segment material (as CapCut does on split)."""
    new_refs = []
    for r in _refs(seg):
        entry = by_id.get(r)
        if not entry:
            new_refs.append(r)
            continue
        cat, m = entry
        c = deepcopy(m)
        c["id"] = new_id(m["id"])
        materials.setdefault(cat, []).append(c)
        by_id[c["id"]] = (cat, c)
        new_refs.append(c["id"])
    seg["extra_material_refs"] = new_refs
    if dup_primary:
        entry = by_id.get(seg.get("material_id"))
        if entry and entry[0] not in ("videos", "audios"):
            cat, m = entry
            c = deepcopy(m)
            c["id"] = new_id(m["id"])
            materials.setdefault(cat, []).append(c)
            by_id[c["id"]] = (cat, c)
            seg["material_id"] = c["id"]


def _tracks(content, indices=None):
    tracks = content.get("tracks") or []
    if indices is None:
        return list(range(len(tracks)))
    out = []
    for i in indices:
        if not isinstance(i, int) or i < 0 or i >= len(tracks):
            raise EditError(f"track index {i!r} out of range (0..{len(tracks) - 1})")
        out.append(i)
    return out


def _get_track(content, ti):
    tracks = content.get("tracks") or []
    if not isinstance(ti, int) or ti < 0 or ti >= len(tracks):
        raise EditError(f"track index {ti!r} out of range (0..{len(tracks) - 1})")
    return tracks[ti]


def _get_segment(track, idx):
    segs = track.get("segments") or []
    if not isinstance(idx, int) or idx < 0 or idx >= len(segs):
        raise EditError(f"segment index {idx!r} out of range (0..{len(segs) - 1})")
    return segs[idx]


# --------------------------------------------------------------------------- split / trim

def _trim(seg, kind, new_s, new_e, by_id, what="trim"):
    """Move a segment's edges on the timeline, keeping the source in sync (speed-aware)."""
    old_s, old_e = seg_start(seg), seg_end(seg)
    if new_e - new_s <= 0:
        raise EditError(f"{what}: resulting duration must be positive")
    if kind == "media":
        spd = _speed(seg)
        src = seg["source_timerange"]
        src_start = int(src.get("start", 0)) + int(round((new_s - old_s) * spd))
        src_dur = int(round((new_e - new_s) * spd))
        if src_start < 0:
            raise EditError(f"{what}: cannot extend before the start of the source clip")
        mat_dur = _material_duration(seg, by_id)
        if mat_dur is not None and src_start + src_dur > mat_dur + 1000:
            raise EditError(f"{what}: cannot extend past the end of the source clip "
                            f"({to_sec(mat_dur)} s available)")
        seg["source_timerange"] = {"start": src_start, "duration": src_dur}
    elif kind == "photo":
        src = seg.get("source_timerange") or {"start": 0}
        seg["source_timerange"] = {"start": int(src.get("start", 0)), "duration": new_e - new_s}
    seg["target_timerange"] = {"start": new_s, "duration": new_e - new_s}


def split_segment(content, track, idx, at, by_id=None):
    """Split ``track.segments[idx]`` at timeline time ``at``; returns the new right part."""
    materials = content.setdefault("materials", {})
    by_id = by_id if by_id is not None else build_index(materials)
    seg = _get_segment(track, idx)
    s, e = seg_start(seg), seg_end(seg)
    if not (s < at < e):
        raise EditError(f"split: {to_sec(at)} s is not inside segment [{to_sec(s)}, {to_sec(e)}]")
    check_editable(seg, by_id, "split")
    kind = classify(track, seg, by_id)
    right = deepcopy(seg)
    right["id"] = new_id(seg.get("id"))
    left_dur, right_dur = at - s, e - at
    seg["target_timerange"] = {"start": s, "duration": left_dur}
    right["target_timerange"] = {"start": at, "duration": right_dur}
    if kind == "media":
        src = seg["source_timerange"]
        total = int(src.get("duration", 0))
        left_src = min(int(round(left_dur * _speed(seg))), total)
        seg["source_timerange"] = {"start": int(src.get("start", 0)), "duration": left_src}
        right["source_timerange"] = {"start": int(src.get("start", 0)) + left_src, "duration": max(total - left_src, 0)}
    elif kind == "photo":
        seg["source_timerange"] = {"start": int((seg.get("source_timerange") or {}).get("start", 0)), "duration": left_dur}
        right["source_timerange"] = {"start": 0, "duration": right_dur}
    _dup_refs(right, materials, by_id, dup_primary=True)
    track["segments"].insert(idx + 1, right)
    return right


def _split_at(content, track, at, by_id):
    n = 0
    for idx, seg in enumerate(list(track.get("segments") or [])):
        if seg_start(seg) < at < seg_end(seg):
            split_segment(content, track, track["segments"].index(seg), at, by_id)
            n += 1
    return n


# --------------------------------------------------------------------------- range operations

def _check_range(content, a, b):
    if a < 0 or b <= a:
        raise EditError(f"range must satisfy 0 <= start < end (got {to_sec(a)}..{to_sec(b)} s)")
    total = max([seg_end(s) for t in content.get("tracks") or [] for s in t.get("segments") or []] or [0])
    if a >= total:
        raise EditError(f"range starts after the end of the project ({to_sec(total)} s)")


def delete_range(content, a, b, tracks=None):
    """Ripple-delete timeline range [a, b) on the selected tracks (default: all)."""
    _check_range(content, a, b)
    by_id = build_index(content.get("materials") or {})
    stats = {"split": 0, "removed": 0, "shifted": 0, "trimmed_tracks": []}
    for ti in _tracks(content, tracks):
        track = _get_track(content, ti)
        stats["split"] += _split_at(content, track, a, by_id) + _split_at(content, track, b, by_id)
        kept = []
        touched = False
        for seg in track.get("segments") or []:
            s, e = seg_start(seg), seg_end(seg)
            if e <= a:
                kept.append(seg)
            elif s >= b:
                seg["target_timerange"]["start"] = s - (b - a)
                stats["shifted"] += 1
                touched = True
                kept.append(seg)
            else:
                stats["removed"] += 1
                touched = True
        track["segments"] = kept
        if touched:
            stats["trimmed_tracks"].append(ti)
    stats["new_duration"] = recompute_duration(content)
    return stats


def retime_range(content, a, b, factor, tracks=None):
    """Change the speed of everything inside [a, b) by ``factor`` (2.0 = twice as fast) with ripple."""
    _check_range(content, a, b)
    try:
        factor = float(factor)
    except (TypeError, ValueError):
        raise EditError("speed must be a number")
    if not (0.05 <= factor <= 100):
        raise EditError("speed factor must be between 0.05 and 100")
    by_id = build_index(content.get("materials") or {})
    length = b - a
    new_length = int(round(length / factor))
    if new_length <= 0:
        raise EditError("range becomes shorter than 1 microsecond")
    delta = length - new_length

    def f(t):
        return a + int(round((t - a) / factor))

    stats = {"split": 0, "retimed": 0, "shifted": 0}
    for ti in _tracks(content, tracks):
        track = _get_track(content, ti)
        stats["split"] += _split_at(content, track, a, by_id) + _split_at(content, track, b, by_id)
        for seg in track.get("segments") or []:
            s, e = seg_start(seg), seg_end(seg)
            if e <= a:
                continue
            if s >= b:
                seg["target_timerange"]["start"] = s - delta
                stats["shifted"] += 1
                continue
            kind = classify(track, seg, by_id)
            ns, ne = f(s), f(e)
            if ne - ns <= 0:
                raise EditError(f"retime: segment {seg.get('id')} would vanish")
            if kind == "media":
                check_editable(seg, by_id, "retime")
                src_dur = int(seg["source_timerange"].get("duration", 0))
                _set_speed(seg, by_id, src_dur / (ne - ns) if src_dur > 0 else _speed(seg) * factor)
            elif kind == "photo":
                seg["source_timerange"] = {"start": int((seg.get("source_timerange") or {}).get("start", 0)),
                                           "duration": ne - ns}
            seg["target_timerange"] = {"start": ns, "duration": ne - ns}
            stats["retimed"] += 1
    stats["new_duration"] = recompute_duration(content)
    return stats


# --------------------------------------------------------------------------- single segment / track

def _shift_following(track, idx, delta):
    n = 0
    for seg in (track.get("segments") or [])[idx:]:
        seg["target_timerange"]["start"] = seg_start(seg) + delta
        n += 1
    return n


def set_segment(content, ti, idx, *, volume=None, speed=None, start=None, end=None, delete=False, ripple=False):
    by_id = build_index(content.get("materials") or {})
    track = _get_track(content, ti)
    seg = _get_segment(track, idx)
    old_s, old_e = seg_start(seg), seg_end(seg)
    changed = {}
    if delete:
        del track["segments"][idx]
        changed["deleted"] = seg.get("id")
        if ripple:
            changed["shifted"] = _shift_following(track, idx, -(old_e - old_s))
        changed["new_duration"] = recompute_duration(content)
        return changed
    kind = classify(track, seg, by_id)
    if volume is not None:
        try:
            v = float(volume)
        except (TypeError, ValueError):
            raise EditError("volume must be a number")
        if v < 0 or v > 10:
            raise EditError("volume must be between 0 and 10 (1 = original)")
        seg["volume"] = v
        if v > 0:
            seg["last_nonzero_volume"] = v
        changed["volume"] = v
    if speed is not None:
        if kind != "media":
            raise EditError("speed can only be changed on video/audio clips")
        try:
            sp = float(speed)
        except (TypeError, ValueError):
            raise EditError("speed must be a number")
        if not (0.05 <= sp <= 100):
            raise EditError("speed must be between 0.05 and 100")
        check_editable(seg, by_id, "speed")
        src_dur = int(seg["source_timerange"].get("duration", 0))
        new_dur = int(round(src_dur / sp))
        if new_dur <= 0:
            raise EditError("speed makes the clip shorter than 1 microsecond")
        seg["target_timerange"] = {"start": seg_start(seg), "duration": new_dur}
        _set_speed(seg, by_id, sp)
        changed["speed"] = sp
    if start is not None or end is not None:
        cur_s, cur_e = seg_start(seg), seg_end(seg)
        new_s = cur_s if start is None else int(start)
        new_e = cur_e if end is None else int(end)
        if new_s < 0:
            raise EditError("start must be >= 0")
        check_editable(seg, by_id, "trim")
        _trim(seg, kind, new_s, new_e, by_id)
        changed["start"], changed["end"] = to_sec(new_s), to_sec(new_e)
    new_e = seg_end(seg)
    if ripple and new_e != old_e:
        changed["shifted"] = _shift_following(track, idx + 1, new_e - old_e)
    changed["new_duration"] = recompute_duration(content)
    return changed


def edit_text(content, ti, idx, *, text=None, start=None, end=None, transform_x=None, transform_y=None, scale=None):
    by_id = build_index(content.get("materials") or {})
    track = _get_track(content, ti)
    seg = _get_segment(track, idx)
    cat, m = by_id.get(seg.get("material_id"), (None, None))
    if cat != "texts":
        raise EditError(f"segment {idx} on track {ti} is not a text segment (material category: {cat})")
    changed = {}
    if text is not None:
        if not isinstance(text, str) or not text.strip():
            raise EditError("text must be a non-empty string")
        try:
            c = json.loads(m.get("content") or "{}")
        except json.JSONDecodeError:
            c = {}
        c["text"] = text
        styles = c.get("styles") or []
        if styles:
            first = deepcopy(styles[0])
            first["range"] = [0, len(text)]
            c["styles"] = [first]
        m["content"] = json.dumps(c, ensure_ascii=False)
        if m.get("base_content"):
            m["base_content"] = text
        changed["text"] = text
    clip = seg.setdefault("clip", {})
    if transform_x is not None or transform_y is not None:
        tr = clip.setdefault("transform", {"x": 0.0, "y": 0.0})
        if transform_x is not None:
            tr["x"] = float(transform_x)
            changed["transform_x"] = tr["x"]
        if transform_y is not None:
            tr["y"] = float(transform_y)
            changed["transform_y"] = tr["y"]
    if scale is not None:
        sc = float(scale)
        if sc <= 0:
            raise EditError("scale must be positive")
        clip["scale"] = {"x": sc, "y": sc}
        changed["scale"] = sc
    if start is not None or end is not None:
        changed.update(set_segment(content, ti, idx, start=start, end=end))
    else:
        changed["new_duration"] = recompute_duration(content)
    return changed


def set_track(content, ti, *, mute=None, delete=False):
    track = _get_track(content, ti)
    changed = {"track": ti, "type": track.get("type"), "name": track.get("name")}
    if delete:
        del content["tracks"][ti]
        changed["deleted"] = True
        changed["new_duration"] = recompute_duration(content)
        return changed
    if mute is not None:
        if track.get("type") not in MEDIA_TRACK_TYPES:
            raise EditError("mute applies to video/audio tracks only")
        n = 0
        for seg in track.get("segments") or []:
            if mute:
                seg["volume"] = 0.0
            else:
                seg["volume"] = float(seg.get("last_nonzero_volume") or 1.0)
            n += 1
        changed["muted" if mute else "unmuted"] = n
    changed["new_duration"] = recompute_duration(content)
    return changed


# --------------------------------------------------------------------------- blur / masks

def cache_root_from_draft_root(draft_root):
    """'C:\\...\\CapCut\\User Data\\Projects\\com.lveditor.draft' -> 'C:/.../CapCut/User Data/Cache'."""
    if not draft_root:
        return ""
    root = draft_root.replace("\\", "/").rstrip("/")
    m = re.match(r"^(.*)/Projects/com\.lveditor\.draft$", root, re.IGNORECASE)
    return (m.group(1) + "/Cache") if m else ""


def _mask_category(materials):
    return "masks" if ("masks" in materials and "common_mask" not in materials) else "common_mask"


def _mask_config(**kw):
    out = {}
    names = {"center_x": "centerX", "center_y": "centerY", "width": "width", "height": "height",
             "feather": "feather", "round_corner": "roundCorner", "invert": "invert", "rotation": "rotation"}
    for k, v in kw.items():
        if v is None:
            continue
        if k == "invert":
            out[names[k]] = bool(v)
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise EditError(f"{k} must be a number")
        if k in ("width", "height") and fv <= 0:
            raise EditError(f"{k} must be positive (fraction of the clip)")
        if k in ("center_x", "center_y") and not (-1.5 <= fv <= 1.5):
            raise EditError(f"{k} must be within -1..1 (0 = clip centre)")
        if k in ("feather",) and not (0 <= fv <= 1):
            raise EditError("feather must be within 0..1")
        if k == "round_corner" and not (0 <= fv <= 1):
            raise EditError("round_corner must be within 0..1")
        out[names[k]] = fv
    return out


def blur_region(content, ti, idx, *, center_x, center_y, width, height, strength=50, feather=0.0,
                round_corner=0.0, invert=False, start=None, end=None, cache_root=""):
    """Copy a clip onto an overlay track, mask it to a rectangle and blur it (CapCut's manual recipe)."""
    materials = content.setdefault("materials", {})
    by_id = build_index(materials)
    track = _get_track(content, ti)
    if track.get("type") != "video":
        raise EditError("blur_region works on video tracks only")
    seg = _get_segment(track, idx)
    kind = classify(track, seg, by_id)
    if kind == "static":
        raise EditError("blur_region needs a video or image clip")
    check_editable(seg, by_id, "blur_region")
    try:
        strength = float(strength)
    except (TypeError, ValueError):
        raise EditError("strength must be a number 0..100")
    if not (0 <= strength <= 100):
        raise EditError("strength must be within 0..100")
    cfg = _mask_config(center_x=center_x, center_y=center_y, width=width, height=height,
                       feather=feather, round_corner=round_corner, invert=invert)

    copy = deepcopy(seg)
    copy["id"] = new_id(seg.get("id"))
    copy["volume"] = 0.0
    _dup_refs(copy, materials, by_id, dup_primary=False)
    mask_cat = _mask_category(materials)
    copy["extra_material_refs"] = [r for r in _refs(copy)
                                   if by_id.get(r, (None, None))[0] not in (mask_cat, "video_effects")]
    if start is not None or end is not None:
        s, e = seg_start(seg), seg_end(seg)
        new_s = s if start is None else int(start)
        new_e = e if end is None else int(end)
        if new_s < s or new_e > e or new_s >= new_e:
            raise EditError(f"start/end must lie inside the clip [{to_sec(s)}, {to_sec(e)}] s")
        _trim(copy, kind, new_s, new_e, by_id, "blur_region")

    style = seg.get("id")
    mask = deepcopy(RECT_MASK_TEMPLATE)
    mask["id"] = new_id(style)
    mask["config"].update(cfg)
    mask["path"] = f"{cache_root}/effect/{RECT_MASK_CACHE_ID}/{RECT_MASK_MD5}" if cache_root else ""
    effect = deepcopy(BLUR_EFFECT_TEMPLATE)
    effect["id"] = new_id(style)
    effect["adjust_params"][0]["value"] = strength / 100.0
    effect["path"] = f"{cache_root}/effect/{BLUR_EFFECT_ID}/{BLUR_EFFECT_MD5}" if cache_root else ""
    materials.setdefault(mask_cat, []).append(mask)
    materials.setdefault("video_effects", []).append(effect)
    copy["extra_material_refs"] += [mask["id"], effect["id"]]

    tracks = content["tracks"]
    overlay_ti = None
    for i, t in enumerate(tracks):
        if t.get("type") == "video" and t.get("name") == BLUR_TRACK_NAME:
            if all(seg_end(x) <= seg_start(copy) or seg_start(x) >= seg_end(copy) for x in t.get("segments") or []):
                overlay_ti = i
                break
    if overlay_ti is None:
        last_video = max([i for i, t in enumerate(tracks) if t.get("type") == "video"] or [-1])
        overlay = {"attribute": 0, "flag": 2, "id": new_id(track.get("id")), "is_default_name": False,
                   "name": BLUR_TRACK_NAME, "segments": [], "type": "video"}
        overlay_ti = last_video + 1
        tracks.insert(overlay_ti, overlay)
    overlay = tracks[overlay_ti]
    overlay["segments"].append(copy)
    overlay["segments"].sort(key=seg_start)
    recompute_duration(content)
    return {"overlay_track": overlay_ti, "segment": overlay["segments"].index(copy), "segment_id": copy["id"],
            "start_sec": to_sec(seg_start(copy)), "end_sec": to_sec(seg_end(copy)),
            "mask": mask["config"], "blur": strength}


def edit_mask(content, ti, idx, **kw):
    by_id = build_index(content.get("materials") or {})
    track = _get_track(content, ti)
    seg = _get_segment(track, idx)
    mask = None
    for r in _refs(seg):
        cat, m = by_id.get(r, (None, None))
        if cat in ("common_mask", "masks"):
            mask = m
            break
    if mask is None:
        raise EditError("segment has no mask; use blur_region or add_video with mask_type")
    cfg = _mask_config(**kw)
    if not cfg:
        raise EditError("nothing to change: pass center_x/center_y/width/height/feather/round_corner/invert")
    mask.setdefault("config", {}).update(cfg)
    return {"mask": mask.get("resource_type"), "config": mask["config"]}


# --------------------------------------------------------------------------- validation / summary

def recompute_duration(content):
    total = 0
    for t in content.get("tracks") or []:
        for s in t.get("segments") or []:
            total = max(total, seg_end(s))
    content["duration"] = total
    return total


def dangling_refs(content):
    """Segment -> material references that do not resolve (upstream-generated drafts may have a few;
    CapCut tolerates them, so they are reported, not rejected)."""
    by_id = build_index(content.get("materials") or {})
    out = []
    for ti, t in enumerate(content.get("tracks") or []):
        for i, s in enumerate(t.get("segments") or []):
            if s.get("material_id") and s["material_id"] not in by_id:
                out.append(f"track {ti} segment {i}: material {s['material_id']} missing")
            for r in _refs(s):
                if r not in by_id:
                    out.append(f"track {ti} segment {i}: extra material {r} missing")
    return out


def validate_timeline(content, check_refs=False):
    """Sort segments, check overlaps/durations/speeds (and refs when check_refs); raises EditError."""
    problems = []
    for ti, t in enumerate(content.get("tracks") or []):
        segs = t.get("segments") or []
        segs.sort(key=seg_start)
        prev_end = -1
        for i, s in enumerate(segs):
            if seg_dur(s) <= 0:
                problems.append(f"track {ti} segment {i}: non-positive duration")
            if seg_start(s) < prev_end:
                problems.append(f"track {ti} segment {i}: overlaps previous segment")
            prev_end = seg_end(s)
            if _speed(s) <= 0:
                problems.append(f"track {ti} segment {i}: speed <= 0")
    if check_refs:
        problems += dangling_refs(content)
    if problems:
        raise EditError("timeline is inconsistent: " + "; ".join(problems[:10]))
    return recompute_duration(content)


def _label(cat, m):
    if m is None:
        return ""
    if cat == "texts":
        try:
            return (json.loads(m.get("content") or "{}").get("text") or "")[:120]
        except json.JSONDecodeError:
            return str(m.get("content", ""))[:120]
    for key in ("material_name", "name"):
        if m.get(key):
            return str(m[key])[:120]
    if m.get("path"):
        return os.path.basename(str(m["path"]))[:120]
    return str(m.get("type", ""))[:120]


def summarize(content, n_existing=None, max_segments=MAX_SUMMARY_SEGMENTS):
    by_id = build_index(content.get("materials") or {})
    tracks_out = []
    for ti, t in enumerate(content.get("tracks") or []):
        segs = t.get("segments") or []
        origin = "existing" if (n_existing is None or ti < n_existing) else "new"
        seg_out = []
        for i, s in enumerate(segs[:max_segments]):
            cat, m = by_id.get(s.get("material_id"), (None, None))
            kind = classify(t, s, by_id)
            if kind == "static":
                kind = {"texts": "text", "stickers": "sticker", "video_effects": "effect",
                        "effects": "filter"}.get(cat, t.get("type", "static"))
            entry = {
                "index": i,
                "start_sec": to_sec(seg_start(s)),
                "end_sec": to_sec(seg_end(s)),
                "kind": kind,
                "label": _label(cat, m),
            }
            if kind in ("media", "photo"):
                entry["speed"] = round(_speed(s), 4)
                entry["volume"] = s.get("volume")
                src = s.get("source_timerange") or {}
                entry["source_start_sec"] = to_sec(src.get("start", 0))
            if s.get("common_keyframes"):
                entry["keyframes"] = len(s["common_keyframes"])
            clip = s.get("clip") or {}
            if isinstance(clip.get("transform"), dict) and kind in ("text", "sticker", "media", "photo"):
                entry["transform_y"] = clip["transform"].get("y")
            masks, effects = None, []
            for r in _refs(s):
                rcat, rm = by_id.get(r, (None, None))
                if rcat in ("common_mask", "masks") and rm is not None:
                    c = rm.get("config") or {}
                    masks = {"type": rm.get("resource_type"),
                             "center_x": c.get("centerX"), "center_y": c.get("centerY"),
                             "width": c.get("width"), "height": c.get("height"),
                             "feather": c.get("feather"), "round_corner": c.get("roundCorner"),
                             "invert": c.get("invert")}
                elif rcat == "video_effects" and rm is not None:
                    effects.append(rm.get("name"))
            if masks:
                entry["mask"] = masks
            if effects:
                entry["effects"] = effects
            seg_out.append(entry)
        tracks_out.append({
            "index": ti,
            "type": t.get("type"),
            "name": t.get("name", ""),
            "origin": origin,
            "editable": origin == "existing",
            "segment_count": len(segs),
            "segments": seg_out,
            **({"truncated": True} if len(segs) > max_segments else {}),
        })
    return tracks_out


def apply_timeline_operation(content, tool, args, *, cache_root=""):
    """Dispatch used by entry.py: ``tool`` is the MCP tool name, ``args`` the sanitised arguments."""
    a = dict(args)
    if tool == "delete_range":
        return delete_range(content, to_us(a["start_sec"]), to_us(a["end_sec"]), a.get("tracks"))
    if tool == "retime_range":
        return retime_range(content, to_us(a["start_sec"]), to_us(a["end_sec"]), a["speed"], a.get("tracks"))
    if tool == "set_segment":
        return set_segment(content, a["track"], a["segment"], volume=a.get("volume"), speed=a.get("speed"),
                           start=None if a.get("start_sec") is None else to_us(a["start_sec"]),
                           end=None if a.get("end_sec") is None else to_us(a["end_sec"]),
                           delete=bool(a.get("delete")), ripple=bool(a.get("ripple")))
    if tool == "edit_text":
        return edit_text(content, a["track"], a["segment"], text=a.get("text"),
                         start=None if a.get("start_sec") is None else to_us(a["start_sec"]),
                         end=None if a.get("end_sec") is None else to_us(a["end_sec"]),
                         transform_x=a.get("transform_x"), transform_y=a.get("transform_y"), scale=a.get("scale"))
    if tool == "set_track":
        return set_track(content, a["track"], mute=a.get("mute"), delete=bool(a.get("delete")))
    if tool == "blur_region":
        return blur_region(content, a["track"], a["segment"], center_x=a["center_x"], center_y=a["center_y"],
                           width=a["width"], height=a["height"], strength=a.get("strength", 50),
                           feather=a.get("feather", 0.0), round_corner=a.get("round_corner", 0.0),
                           invert=bool(a.get("invert", False)),
                           start=None if a.get("start_sec") is None else to_us(a["start_sec"]),
                           end=None if a.get("end_sec") is None else to_us(a["end_sec"]),
                           cache_root=cache_root)
    if tool == "edit_mask":
        return edit_mask(content, a["track"], a["segment"], center_x=a.get("center_x"), center_y=a.get("center_y"),
                         width=a.get("width"), height=a.get("height"), feather=a.get("feather"),
                         round_corner=a.get("round_corner"), invert=a.get("invert"))
    raise EditError(f"unknown timeline operation {tool!r}")
