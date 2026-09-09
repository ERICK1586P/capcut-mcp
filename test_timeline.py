#!/usr/bin/env python3
"""
Host-side tests for timeline_edit.py (no Docker needed):  python3 test_timeline.py

Part 1 runs on a synthetic CapCut-like content.  Part 2 runs generic checks on a real
CapCut 9.3 draft_content.json when VECTCUT_REAL_FIXTURE points to one (any project with at
least one video clip); it is skipped otherwise.
"""

import copy
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timeline_edit as te  # noqa: E402

REAL_FIXTURE = os.environ.get("VECTCUT_REAL_FIXTURE", "")
DRAFT_ROOT = r"C:\Users\user\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"
CACHE_ROOT = DRAFT_ROOT.replace("\\", "/").rsplit("/Projects/", 1)[0] + "/Cache"
S = te.US
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)
    return cond


def expect_error(fn, msg, *a, **kw):
    try:
        fn(*a, **kw)
    except te.EditError as exc:
        print(f"  ok   {msg} -> {str(exc)[:90]}")
        return True
    FAILS.append(msg)
    print(f"  FAIL {msg}: no EditError raised")
    return False


def gid():
    return str(uuid.uuid4()).upper()


# --------------------------------------------------------------------------- synthetic fixture

def mk_seg(material_id, start, dur, *, src_start=None, speed=1.0, refs=(), volume=1.0, keyframes=None):
    seg = {
        "id": gid(), "material_id": material_id,
        "target_timerange": {"start": start, "duration": dur},
        "source_timerange": None if src_start is None else {"start": src_start, "duration": int(round(dur * speed))},
        "speed": speed, "volume": volume, "last_nonzero_volume": 1.0,
        "extra_material_refs": list(refs), "common_keyframes": keyframes or [],
        "render_index": 0, "track_render_index": 0, "visible": True,
        "clip": {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0, "transform": {"x": 0.0, "y": 0.0},
                 "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
    }
    return seg


def make_content():
    mats = {k: [] for k in ("videos", "speeds", "canvases", "texts", "stickers", "video_effects", "common_mask",
                            "material_animations", "placeholder_infos", "transitions")}

    def speed_mat(v):
        m = {"id": gid(), "type": "speed", "mode": 0, "speed": v, "curve_speed": None}
        mats["speeds"].append(m)
        return m["id"]

    def canvas():
        m = {"id": gid(), "type": "canvas_color", "color": "", "blur": 0.0}
        mats["canvases"].append(m)
        return m["id"]

    vid_a = {"id": gid(), "type": "video", "material_name": "a.mp4", "path": te.PLACEHOLDER + "/assets/video/a.mp4",
             "duration": 60 * S, "width": 1080, "height": 1920}
    vid_b = {"id": gid(), "type": "video", "material_name": "b.mp4", "path": te.PLACEHOLDER + "/assets/video/b.mp4",
             "duration": 40 * S, "width": 1080, "height": 1920}
    photo = {"id": gid(), "type": "photo", "material_name": "cover.png", "path": "C:/x/cover.png",
             "duration": 10800 * S, "width": 1080, "height": 1920}
    mats["videos"] += [vid_a, vid_b, photo]
    txt = {"id": gid(), "type": "text", "base_content": "",
           "content": json.dumps({"styles": [{"bold": True, "range": [0, 5], "size": 15}], "text": "Hello"})}
    mats["texts"].append(txt)
    stk = {"id": gid(), "type": "sticker", "name": "Arrow"}
    mats["stickers"].append(stk)
    eff = {"id": gid(), "type": "video_effect", "name": "Blur", "effect_id": te.BLUR_EFFECT_ID,
           "adjust_params": [{"name": "effects_adjust_blur", "value": 0.5}]}
    mats["video_effects"].append(eff)
    mask = {"id": gid(), "type": "mask", "resource_type": "rectangle",
            "config": {"width": 1.0, "height": 0.5, "centerX": 0.0, "centerY": -0.5, "rotation": 0.0,
                       "feather": 0.0, "expansion": 0.0, "roundCorner": 0.0, "invert": False, "aspectRatio": 1.0}}
    mats["common_mask"].append(mask)
    anim = {"id": gid(), "type": "sticker_animation", "animations": []}
    mats["material_animations"].append(anim)

    base = {"id": gid(), "type": "video", "name": "", "flag": 2, "attribute": 0, "is_default_name": True, "segments": [
        mk_seg(vid_a["id"], 0, 10 * S, src_start=0, speed=1.0, refs=[speed_mat(1.0), canvas()]),
        mk_seg(vid_b["id"], 10 * S, 5 * S, src_start=20 * S, speed=2.0, refs=[speed_mat(2.0), canvas()]),
        mk_seg(vid_a["id"], 15 * S, 5 * S, src_start=0, speed=1.0, refs=[speed_mat(1.0), canvas()]),
    ]}
    photo_track = {"id": gid(), "type": "video", "name": "", "flag": 2, "attribute": 0, "is_default_name": True,
                   "segments": [mk_seg(photo["id"], 0, 5 * S, src_start=0, speed=1.0, refs=[speed_mat(1.0)])]}
    overlay = {"id": gid(), "type": "video", "name": "", "flag": 2, "attribute": 0, "is_default_name": True,
               "segments": [mk_seg(vid_a["id"], 3 * S, 4 * S, src_start=3 * S, speed=1.0,
                                   refs=[speed_mat(1.0), eff["id"], mask["id"], anim["id"]])]}
    text_track = {"id": gid(), "type": "text", "name": "", "flag": 0, "attribute": 0, "is_default_name": True,
                  "segments": [mk_seg(txt["id"], 2 * S, 6 * S)]}
    text_track["segments"][0]["source_timerange"] = {"start": 0, "duration": 0}
    text_track["segments"][0]["clip"]["transform"]["y"] = 0.69
    sticker_track = {"id": gid(), "type": "sticker", "name": "", "flag": 0, "attribute": 0, "is_default_name": True,
                     "segments": [mk_seg(stk["id"], 12 * S, 2 * S)]}
    content = {"id": gid(), "duration": 20 * S, "fps": 30.0, "canvas_config": {"width": 1080, "height": 1920},
               "materials": mats, "tracks": [base, photo_track, overlay, text_track, sticker_track],
               "platform": {"app_version": "9.3.0"}, "keyframes": {}}
    te.validate_timeline(content)
    return content


def segs(content, ti):
    return [(te.to_sec(te.seg_start(s)), te.to_sec(te.seg_end(s))) for s in content["tracks"][ti]["segments"]]


def all_ids(content):
    out = set()
    for t in content["tracks"]:
        out.add(t["id"])
        for s in t["segments"]:
            out.add(s["id"])
    return out


# --------------------------------------------------------------------------- part 1

def part1():
    print("\n[1] split arithmetic")
    c = make_content()
    by_id = te.build_index(c["materials"])
    base = c["tracks"][0]
    right = te.split_segment(c, base, 0, 4 * S, by_id)
    left = base["segments"][0]
    check(left["target_timerange"] == {"start": 0, "duration": 4 * S} and left["source_timerange"] == {"start": 0, "duration": 4 * S},
          f"left part 0-4 s src 0-4: {left['target_timerange']} {left['source_timerange']}")
    check(right["target_timerange"] == {"start": 4 * S, "duration": 6 * S} and right["source_timerange"] == {"start": 4 * S, "duration": 6 * S},
          f"right part 4-10 s src 4-10: {right['target_timerange']} {right['source_timerange']}")
    check(right["id"] != left["id"] and right["material_id"] == left["material_id"], "new segment id, shared video material")
    check(set(right["extra_material_refs"]).isdisjoint(left["extra_material_refs"]) and
          all(r in te.build_index(c["materials"]) for r in right["extra_material_refs"]), "extra materials duplicated with new ids")
    # speed 2.0 segment (now index 2): 10-15 s, src 20-30
    right2 = te.split_segment(c, base, 2, 12 * S, te.build_index(c["materials"]))
    left2 = base["segments"][2]
    check(left2["source_timerange"] == {"start": 20 * S, "duration": 4 * S} and right2["source_timerange"] == {"start": 24 * S, "duration": 6 * S},
          f"speed-2 split: src left {left2['source_timerange']} right {right2['source_timerange']}")
    # text split duplicates the text material
    tt = c["tracks"][3]
    rt = te.split_segment(c, tt, 0, 5 * S, te.build_index(c["materials"]))
    check(rt["material_id"] != tt["segments"][0]["material_id"] and len(c["materials"]["texts"]) == 2, "text split duplicates the text material")
    check(te.validate_timeline(c, check_refs=True) == 20 * S, "timeline valid after splits")
    expect_error(te.split_segment, "split outside segment rejected", c, base, 0, 30 * S)

    print("\n[2] delete_range(3, 7) across all tracks")
    c = make_content()
    st = te.delete_range(c, 3 * S, 7 * S)
    check(segs(c, 0) == [(0, 3), (3, 6), (6, 11), (11, 16)], f"base -> {segs(c, 0)}")
    check(segs(c, 1) == [(0, 3)], f"photo -> {segs(c, 1)}")
    check(segs(c, 2) == [], f"overlay fully removed -> {segs(c, 2)}")
    check(segs(c, 3) == [(2, 3), (3, 4)], f"text -> {segs(c, 3)}")
    check(segs(c, 4) == [(8, 10)], f"sticker -> {segs(c, 4)}")
    check(c["duration"] == 16 * S and st["removed"] == 4 and st["split"] == 5, f"duration {te.to_sec(c['duration'])} stats {st}")
    seg_after = c["tracks"][0]["segments"][1]
    check(seg_after["source_timerange"] == {"start": 7 * S, "duration": 3 * S}, f"tail of split clip keeps source offset {seg_after['source_timerange']}")
    check(c["tracks"][1]["segments"][0]["source_timerange"]["duration"] == 3 * S, "photo source duration follows target")
    te.validate_timeline(c)

    print("\n[3] retime_range(0, 10, x2)")
    c = make_content()
    st = te.retime_range(c, 0, 10 * S, 2.0)
    check(segs(c, 0) == [(0, 5), (5, 10), (10, 15)], f"base -> {segs(c, 0)}")
    a = c["tracks"][0]["segments"][0]
    by_id = te.build_index(c["materials"])
    sp = [by_id[r][1] for r in a["extra_material_refs"] if by_id[r][0] == "speeds"][0]
    check(abs(a["speed"] - 2.0) < 1e-9 and abs(sp["speed"] - 2.0) < 1e-9 and a["source_timerange"]["duration"] == 10 * S,
          f"clip speed {a['speed']} / material {sp['speed']} / source kept")
    ov = c["tracks"][2]["segments"][0]
    check(segs(c, 2) == [(1.5, 3.5)] and abs(ov["speed"] - 2.0) < 1e-9, f"overlay retimed in sync -> {segs(c, 2)} speed {ov['speed']}")
    check(segs(c, 3) == [(1, 4)] and segs(c, 4) == [(7, 9)] and segs(c, 1) == [(0, 2.5)],
          f"text {segs(c, 3)} sticker {segs(c, 4)} photo {segs(c, 1)}")
    check(c["duration"] == 15 * S, f"duration {te.to_sec(c['duration'])}")
    te.validate_timeline(c)
    c = make_content()
    te.retime_range(c, 12 * S, 14 * S, 0.5)
    check(segs(c, 0) == [(0, 10), (10, 12), (12, 16), (16, 17), (17, 22)], f"slow-down splits and shifts -> {segs(c, 0)}")
    te.validate_timeline(c)

    print("\n[4] set_segment / edit_text / set_track")
    c = make_content()
    r = te.set_segment(c, 0, 0, volume=0)
    check(c["tracks"][0]["segments"][0]["volume"] == 0.0 and r["volume"] == 0.0, "volume 0")
    r = te.set_segment(c, 0, 1, speed=1.0, ripple=True)
    check(segs(c, 0) == [(0, 10), (10, 20), (20, 25)], f"speed 2->1 with ripple -> {segs(c, 0)}")
    r = te.set_segment(c, 0, 2, start=21 * S, end=24 * S)
    s2 = c["tracks"][0]["segments"][2]
    check(s2["source_timerange"] == {"start": 1 * S, "duration": 3 * S}, f"trim moves source {s2['source_timerange']}")
    expect_error(te.set_segment, "extending past the source rejected", c, 0, 2, end=90 * S)
    r = te.set_segment(c, 0, 0, delete=True, ripple=True)
    check(segs(c, 0) == [(0, 10), (11, 14)], f"delete with ripple -> {segs(c, 0)}")
    te.validate_timeline(c)
    c = make_content()
    r = te.edit_text(c, 3, 0, text="Новый текст", transform_y=0.67, scale=0.8, start=1 * S, end=4 * S)
    m = c["materials"]["texts"][0]
    cc = json.loads(m["content"])
    check(cc["text"] == "Новый текст" and cc["styles"][0]["range"] == [0, len("Новый текст")], f"text + style range {cc['styles'][0]['range']}")
    sg = c["tracks"][3]["segments"][0]
    check(sg["clip"]["transform"]["y"] == 0.67 and sg["clip"]["scale"] == {"x": 0.8, "y": 0.8} and segs(c, 3) == [(1, 4)],
          "transform/scale/time applied")
    expect_error(te.edit_text, "edit_text on a video segment rejected", c, 0, 0, text="x")
    r = te.set_track(c, 0, mute=True)
    check(all(s["volume"] == 0.0 for s in c["tracks"][0]["segments"]) and r["muted"] == 3, "track muted")
    r = te.set_track(c, 4, delete=True)
    check(len(c["tracks"]) == 4 and c["duration"] == 20 * S, "sticker track deleted")
    expect_error(te.set_track, "mute on text track rejected", c, 3, mute=True)

    print("\n[5] blur_region / edit_mask")
    c = make_content()
    cache = te.cache_root_from_draft_root(DRAFT_ROOT)
    check(cache == CACHE_ROOT, f"cache root {cache}")
    r = te.blur_region(c, 0, 0, center_x=0.0, center_y=-0.7, width=1.0, height=0.3, strength=60,
                       start=2 * S, end=6 * S, cache_root=cache)
    ti = r["overlay_track"]
    check(ti == 3 and c["tracks"][3]["name"] == te.BLUR_TRACK_NAME and c["tracks"][4]["type"] == "text",
          f"overlay track inserted after the last video track (index {ti})")
    ov = c["tracks"][ti]["segments"][0]
    by_id = te.build_index(c["materials"])
    cats = sorted(by_id[x][0] for x in ov["extra_material_refs"])
    check("common_mask" in cats and "video_effects" in cats and cats.count("speeds") == 1, f"refs {cats}")
    mask = [by_id[x][1] for x in ov["extra_material_refs"] if by_id[x][0] == "common_mask"][0]
    effm = [by_id[x][1] for x in ov["extra_material_refs"] if by_id[x][0] == "video_effects"][0]
    check(mask["config"]["centerY"] == -0.7 and mask["config"]["height"] == 0.3 and mask["path"].endswith(te.RECT_MASK_MD5), f"mask {mask['config']}")
    check(effm["adjust_params"][0]["value"] == 0.6 and effm["effect_id"] == te.BLUR_EFFECT_ID and effm["path"].startswith(cache),
          f"blur effect {effm['adjust_params']} path {effm['path'][:60]}")
    check(segs(c, ti) == [(2, 6)] and ov["source_timerange"] == {"start": 2 * S, "duration": 4 * S} and ov["volume"] == 0.0
          and ov["material_id"] == c["tracks"][0]["segments"][0]["material_id"], "overlay trimmed, muted, shares the source")
    r2 = te.blur_region(c, 0, 1, center_x=0, center_y=0, width=0.5, height=0.5, cache_root=cache)
    check(r2["overlay_track"] == ti and len(c["tracks"][ti]["segments"]) == 2, "second blur reuses the overlay track")
    r3 = te.blur_region(c, 0, 0, center_x=0, center_y=0, width=0.5, height=0.5, cache_root=cache)
    check(r3["overlay_track"] == ti + 1, "overlapping blur gets its own track")
    e = te.edit_mask(c, ti, 0, center_y=-0.6, height=0.24)
    check(mask["config"]["centerY"] == -0.6 and mask["config"]["height"] == 0.24 and mask["config"]["width"] == 1.0, f"edit_mask {e['config']}")
    expect_error(te.edit_mask, "edit_mask without mask rejected", c, 0, 0, center_y=0.1)
    expect_error(te.blur_region, "blur_region on text rejected", c, 5, 0, center_x=0, center_y=0, width=1, height=1)
    te.validate_timeline(c)
    summ = te.summarize(c, n_existing=5)
    ent = summ[ti]["segments"][0]
    check(ent.get("mask", {}).get("center_y") == -0.6 and ent.get("effects") == ["Blur"], f"summary shows mask/effects: {ent}")

    print("\n[6] refusals and validation")
    c = make_content()
    c["tracks"][0]["segments"][0]["common_keyframes"] = [{"id": "k"}]
    expect_error(te.delete_range, "keyframes refused on split", c, 3 * S, 7 * S)
    c = make_content()
    by_id = te.build_index(c["materials"])
    sp = [by_id[r][1] for r in c["tracks"][0]["segments"][0]["extra_material_refs"] if by_id[r][0] == "speeds"][0]
    sp["curve_speed"] = {"x": 1}
    expect_error(te.retime_range, "curve speed refused", c, 0, 10 * S, 2.0)
    c = make_content()
    tr = {"id": gid(), "type": "transition"}
    c["materials"]["transitions"].append(tr)
    c["tracks"][0]["segments"][0]["extra_material_refs"].append(tr["id"])
    expect_error(te.split_segment, "transition refused", c, c["tracks"][0], 0, 5 * S, te.build_index(c["materials"]))
    c = make_content()
    expect_error(te.delete_range, "start >= end refused", c, 5 * S, 5 * S)
    expect_error(te.delete_range, "range after the end refused", c, 30 * S, 40 * S)
    expect_error(te.retime_range, "factor 0 refused", c, 0, 5 * S, 0)
    expect_error(te.set_segment, "bad track index", c, 9, 0, volume=0)
    expect_error(te.set_segment, "bad segment index", c, 0, 9, volume=0)
    expect_error(te.delete_range, "bad tracks list", c, 0, 1 * S, [7])
    c["tracks"][0]["segments"][1]["target_timerange"]["start"] = 9 * S
    expect_error(te.validate_timeline, "overlap detected", c)
    c = make_content()
    c["tracks"][0]["segments"][0]["extra_material_refs"].append("missing-id")
    expect_error(te.validate_timeline, "dangling ref detected", c, True)
    check(te.new_id("ABCD-1").count("-") == 4 and "-" not in te.new_id("abcd"), "id style follows the original")
    s = te.summarize(make_content(), n_existing=3)
    check([t["origin"] for t in s] == ["existing"] * 3 + ["new"] * 2 and s[3]["segments"][0]["kind"] == "text"
          and s[3]["segments"][0]["label"] == "Hello", "summary origin/kind/label")


# --------------------------------------------------------------------------- part 2: real file

def load_real():
    if not REAL_FIXTURE:
        print("\n[real] VECTCUT_REAL_FIXTURE not set, skipping part 2")
        return None
    p = Path(REAL_FIXTURE)
    if not p.is_file():
        print(f"\n[real] fixture not found, skipping: {p}")
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def find_media(content):
    """(track index, segment index) of the first video clip with a source range, or None."""
    by_id = te.build_index(content["materials"])
    for ti, t in enumerate(content["tracks"]):
        if t.get("type") != "video":
            continue
        for i, s in enumerate(t["segments"]):
            if te.classify(t, s, by_id) == "media" and not s.get("common_keyframes"):
                return ti, i
    return None


def part2(orig):
    total = te.to_sec(orig["duration"])
    media = find_media(orig)
    if media is None or total < 4:
        print(f"\n[real] no editable video clip or too short ({total} s), skipping part 2")
        return
    a, b = round(total * 0.25, 3), round(total * 0.4, 3)

    print(f"\n[7] real project ({total} s): delete_range({a}, {b})")
    c = copy.deepcopy(orig)
    d0 = c["duration"]
    keys0 = set(c.keys())
    keep = {s["id"] for t in c["tracks"] for s in t["segments"]
            if te.seg_end(s) <= te.to_us(a) or te.seg_start(s) >= te.to_us(b)}
    n0 = sum(len(t["segments"]) for t in c["tracks"])
    st = te.delete_range(c, te.to_us(a), te.to_us(b))
    check(abs(c["duration"] - (d0 - te.to_us(b - a))) <= 2, f"duration {te.to_sec(d0)} -> {te.to_sec(c['duration'])}")
    check(st["removed"] + st["split"] + st["shifted"] > 0, f"stats {st}")
    check(set(c.keys()) == keys0, "top-level keys preserved")
    check(keep <= all_ids(c), f"segments outside the range keep their ids ({st['removed']} removed)")
    check(all(te.seg_start(x) >= te.seg_end(y) for t in c["tracks"] for y, x in zip(t["segments"], t["segments"][1:])), "segments stay sorted")
    te.validate_timeline(c)
    check(all(te.seg_end(s) <= c["duration"] for t in c["tracks"] for s in t["segments"]), "nothing ends after the new duration")
    by_id = te.build_index(c["materials"])
    check(all(x in by_id for t in c["tracks"] for s in t["segments"] for x in (s.get("extra_material_refs") or [])),
          "all extra_material_refs resolve after the cut")
    print(f"       segments {n0} -> {sum(len(t['segments']) for t in c['tracks'])}")

    print(f"\n[8] real project: retime_range({a}, {b}, x1.5)")
    c = copy.deepcopy(orig)
    te.retime_range(c, te.to_us(a), te.to_us(b), 1.5)
    by_id = te.build_index(c["materials"])
    check(abs(c["duration"] - (orig["duration"] - te.to_us((b - a) - (b - a) / 1.5))) <= 2, f"duration {te.to_sec(c['duration'])}")
    in_sync = True
    for t in c["tracks"]:
        for s in t["segments"]:
            if te.classify(t, s, by_id) != "media":
                continue
            for r in s.get("extra_material_refs") or []:
                cat, m = by_id.get(r, (None, None))
                if cat == "speeds" and abs(float(m.get("speed", 1.0)) - float(s.get("speed", 1.0))) > 1e-9:
                    in_sync = False
    check(in_sync, "segment.speed and speeds material agree everywhere")
    te.validate_timeline(c)

    print("\n[9] real project: blur_region / edit_mask")
    c = copy.deepcopy(orig)
    ti, si = media
    last_video = max(i for i, t in enumerate(c["tracks"]) if t.get("type") == "video")
    seg = c["tracks"][ti]["segments"][si]
    s0, s1 = te.to_sec(te.seg_start(seg)), te.to_sec(te.seg_end(seg))
    r = te.blur_region(c, ti, si, center_x=0.0, center_y=-0.55, width=1.0, height=0.6, strength=50,
                       start=te.to_us(s0), end=te.to_us(min(s1, s0 + 2)), cache_root=te.cache_root_from_draft_root(DRAFT_ROOT))
    check(r["overlay_track"] == last_video + 1 and c["tracks"][r["overlay_track"]]["name"] == te.BLUR_TRACK_NAME,
          f"overlay track inserted right after the last video track (index {r['overlay_track']})")
    ov = c["tracks"][r["overlay_track"]]["segments"][0]
    by_id = te.build_index(c["materials"])
    check(all(x in by_id for x in ov["extra_material_refs"]) and ov["material_id"] == seg["material_id"],
          "overlay refs resolve, source shared")
    e = te.edit_mask(c, r["overlay_track"], 0, center_y=-0.45, height=0.35)
    check(e["config"]["centerY"] == -0.45 and e["config"]["height"] == 0.35, f"new mask edited {e['config']}")
    te.validate_timeline(c)

    print("\n[10] real project: mute + text edit round-trip")
    c = copy.deepcopy(orig)
    for i, t in enumerate(c["tracks"]):
        if t.get("type") == "video" and t["segments"]:
            te.set_track(c, i, mute=True)
    by_id = te.build_index(c["materials"])
    check(all(s["volume"] == 0.0 for t in c["tracks"] if t.get("type") == "video"
              for s in t["segments"] if te.classify(t, s, by_id) == "media"), "all video clips muted")
    texts = [(i, j) for i, t in enumerate(c["tracks"]) if t.get("type") == "text" for j in range(len(t["segments"]))]
    if texts:
        e = te.edit_text(c, texts[0][0], texts[0][1], text="Edited by capcut-mcp", transform_y=0.86)
        check(te.summarize(c)[texts[0][0]]["segments"][texts[0][1]]["label"] == "Edited by capcut-mcp", "text edited")
    else:
        print("       (no text track, edit_text skipped)")
    dur = te.validate_timeline(c)
    check(dur == orig["duration"], "duration unchanged by mute/text edits")
    check(set(c.keys()) == set(orig.keys()) and c["platform"] == orig["platform"], "top-level keys and platform intact")
    summ = te.summarize(c)
    print("       tracks: " + ", ".join(f"[{t['index']}]{t['type']}x{t['segment_count']}" for t in summ))


def main():
    part1()
    real = load_real()
    if real is not None:
        part2(real)
    print()
    if FAILS:
        print(f"TIMELINE TESTS FAILED: {len(FAILS)}")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("TIMELINE TESTS PASSED")


if __name__ == "__main__":
    main()
