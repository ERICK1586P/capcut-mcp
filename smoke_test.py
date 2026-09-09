#!/usr/bin/env python3
"""
End-to-end smoke test for the capcut-mcp image.

Runs the container the same way Claude Desktop does (docker run -i --rm ...), but
with scratch folders instead of the real CapCut projects dir:

  .smoke/drafts  -> /drafts   (rw)
  .smoke/media   -> /media    (ro)  example.mp4 = VECTCUT_SAMPLE_MP4 or a 5 s clip synthesised by ffmpeg inside the image

Steps [1]-[8] cover new drafts, [9]-[12] cover opening/editing existing projects
(copy + in_place + negatives + a real CapCut-authored draft_content.json when available).

Usage:  python3 smoke_test.py [--image capcut-mcp:local] [--keep]
        VECTCUT_SAMPLE_MP4=<mp4 ~5 s>  VECTCUT_REAL_FIXTURE=<draft_content.json>  (optional, both)
        python3 smoke_test.py --drafts-dir "<real CapCut projects dir>" --capcut-root "<Windows path>" \
            --open-existing "<project name>" [--edit-demo]      # live test: only opens a COPY, edits it, saves
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timeline_edit as te  # noqa: E402

HERE = Path(__file__).resolve().parent
SMOKE = HERE / ".smoke"
DRAFTS = SMOKE / "drafts"
MEDIA = SMOKE / "media"
FAKE_ROOT = r"C:\Temp\Fake\com.lveditor.draft"
FAKE_MEDIA_ROOT = r"C:\Temp\Fake\Videos"
SAMPLE_MP4 = Path(os.environ["VECTCUT_SAMPLE_MP4"]) if os.environ.get("VECTCUT_SAMPLE_MP4") else None
PUBLIC_MP4 = "https://download.samplelib.com/mp4/sample-5s.mp4"
EXPECTED_TOOLS = {
    "create_draft", "add_video", "add_audio", "add_image", "add_text", "add_subtitle",
    "add_effect", "add_sticker", "add_video_keyframe", "get_video_duration", "save_draft",
    "capcut_healthcheck", "list_projects", "open_project", "describe_draft",
    "delete_range", "retime_range", "set_segment", "edit_text", "set_track", "blur_region", "edit_mask",
}
DRAFT_ID_RE = re.compile(r"^dfd_cat_\d+_[0-9a-f]{8}$")
REAL_FIXTURE = Path(os.environ["VECTCUT_REAL_FIXTURE"]) if os.environ.get("VECTCUT_REAL_FIXTURE") else None

FAILURES = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)
    return cond


class Rpc:
    def __init__(self, proc):
        self.proc = proc
        self.next_id = 0

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(self, method, params=None, timeout=300):
        self.next_id += 1
        rid = self.next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("server closed stdout")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                raise RuntimeError(f"non-JSON line on stdout (stdout hygiene broken): {line[:200]!r}")
            if msg.get("id") == rid:
                return msg
        raise TimeoutError(method)

    def tool(self, name, args=None, timeout=300):
        msg = self.call("tools/call", {"name": name, "arguments": args or {}}, timeout=timeout)
        if "error" in msg:
            return {"success": False, "error": msg["error"]}
        text = msg["result"]["content"][0]["text"]
        return json.loads(text)

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def track_ids(content):
    return [t["id"] for t in content["tracks"]]


def seg_ranges(content, ti):
    return [(te.to_sec(te.seg_start(x)), te.to_sec(te.seg_end(x))) for x in content["tracks"][ti]["segments"]]


def mirrors_equal(folder):
    root = (folder / "draft_content.json").read_bytes()
    out = []
    for pat in ("draft_content.json.bak", "template-2.tmp", "Timelines/*/draft_content.json",
                "Timelines/*/draft_content.json.bak", "Timelines/*/template-2.tmp"):
        for p in folder.glob(pat):
            out.append((str(p.relative_to(folder)), p.read_bytes() == root))
    return out


def existing_project_steps(rpc, args, first_folder, local_sample):
    root = args.capcut_root
    name = args.draft_name
    orig_content_bytes = (first_folder / "draft_content.json").read_bytes()
    orig_content = json.loads(orig_content_bytes)
    orig_meta = load_json(first_folder / "draft_meta_info.json")

    print("\n[9] open an existing project as a COPY and edit the timeline")
    lp = rpc.tool("list_projects")
    names = [p["name"] for p in (lp.get("result") or {}).get("projects", [])]
    check(lp.get("success") and name in names, f"list_projects -> {names}")
    op = rpc.tool("open_project", {"name": name, "mode": "copy"})
    check(op.get("success"), f"open_project copy -> {op.get('error') or 'ok'}")
    r = op["result"]
    did = r["draft_id"]
    copy_name = r["project_name"]
    check(bool(DRAFT_ID_RE.match(did)), f"draft_id {did}")
    check(copy_name == f"{name}-mcp" and (DRAFTS / copy_name).is_dir(), f"copy folder {copy_name}")
    copy_dir = DRAFTS / copy_name
    copy_meta = load_json(copy_dir / "draft_meta_info.json")
    check(copy_meta["draft_id"] != orig_meta["draft_id"] and copy_meta["draft_name"] == copy_name
          and copy_meta["draft_fold_path"].endswith("/" + copy_name), "copy meta: new draft_id, name, fold_path")
    check(load_json(copy_dir / "draft_content.json")["id"] == orig_content["id"], "copy keeps content id (like CapCut)")
    tracks = r["summary"]["tracks"]
    print("       tracks: " + ", ".join(f"[{t['index']}]{t['type']}:{t['name']!r}x{t['segment_count']}" for t in tracks))
    check(all(t["origin"] == "existing" for t in tracks) and len(tracks) == 3, "all tracks reported as existing")
    vt = next(t["index"] for t in tracks if t["type"] == "video" and t["segment_count"] == 2)
    tt = next(t["index"] for t in tracks if t["type"] == "text")
    d = rpc.tool("describe_draft", {"draft_id": did})
    check(d.get("success") and d["result"]["duration_sec"] == 7.0, f"describe_draft duration {d.get('result', {}).get('duration_sec')}")

    dr = rpc.tool("delete_range", {"draft_id": did, "start_sec": 1, "end_sec": 2})
    segs = {t["index"]: [(s["start_sec"], s["end_sec"]) for s in t["segments"]] for t in (dr.get("result") or {}).get("tracks", [])}
    check(dr.get("success") and segs.get(vt) == [(0, 1), (1, 4), (4, 6)] and segs.get(tt) == [(0, 1), (1, 2)],
          f"delete_range(1,2) -> video {segs.get(vt)} text {segs.get(tt)}")
    rt = rpc.tool("retime_range", {"draft_id": did, "start_sec": 0, "end_sec": 4, "speed": 2.0})
    segs = {t["index"]: [(s["start_sec"], s["end_sec"]) for s in t["segments"]] for t in (rt.get("result") or {}).get("tracks", [])}
    check(rt.get("success") and segs.get(vt) == [(0, 0.5), (0.5, 2), (2, 4)] and rt["result"]["duration_sec"] == 4.0,
          f"retime_range(0,4,x2) -> video {segs.get(vt)} duration {rt.get('result', {}).get('duration_sec')}")
    ss = rpc.tool("set_segment", {"draft_id": did, "track": vt, "segment": 0, "volume": 0})
    check(ss.get("success") and ss["result"]["changed"].get("volume") == 0, "set_segment volume 0")
    et = rpc.tool("edit_text", {"draft_id": did, "track": tt, "segment": 0, "text": "New", "transform_y": 0.67})
    check(et.get("success") and et["result"]["tracks"][0]["segments"][0]["label"] == "New", f"edit_text -> {et.get('error') or 'ok'}")
    br = rpc.tool("blur_region", {"draft_id": did, "track": vt, "segment": 1, "center_x": 0, "center_y": -0.6,
                                  "width": 1, "height": 0.4, "strength": 60})
    ov = (br.get("result") or {}).get("changed", {}).get("overlay_track")
    check(br.get("success") and ov == vt + 1, f"blur_region -> overlay track {ov}")
    ovseg = next((t for t in (br.get("result") or {}).get("tracks", []) if t["index"] == ov), {"segments": [{}]})["segments"][0]
    check(ovseg.get("effects") == ["Blur"] and ovseg.get("mask", {}).get("height") == 0.4, f"overlay segment {ovseg.get('mask')} {ovseg.get('effects')}")
    em = rpc.tool("edit_mask", {"draft_id": did, "track": ov, "segment": 0, "height": 0.3})
    check(em.get("success") and (em.get("result") or {}).get("changed", {}).get("config", {}).get("height") == 0.3, "edit_mask height 0.3")

    t1 = rpc.tool("add_text", {"draft_id": did, "text": "Added", "start": 0, "end": 2, "font_size": 40})
    t2 = rpc.tool("add_text", {"draft_id": did, "text": "More", "start": 2, "end": 3, "track_name": None})
    check(t1.get("success") and t2.get("success") and str(t1["result"].get("track_name", "")).startswith("text_mcp_")
          and str(t2["result"].get("track_name", "")).startswith("text_mcp_"),
          f"add_text auto tracks: {t1.get('result', {}).get('track_name')} / {t2.get('result', {}).get('track_name')}")
    late = rpc.tool("delete_range", {"draft_id": did, "start_sec": 0, "end_sec": 1})
    check(not late.get("success"), f"timeline edit after add_* rejected: {late.get('error', '')[:70]}")
    dup = rpc.tool("add_text", {"draft_id": did, "text": "x", "start": 0, "end": 1, "track_name": "text_main"})
    check(not dup.get("success"), f"existing track name rejected: {dup.get('error', '')[:70]}")

    sv = rpc.tool("save_draft", {"draft_id": did}, timeout=600)
    print("       " + json.dumps(sv, ensure_ascii=False)[:900])
    if not check(sv.get("success"), "save_draft (copy) succeeded"):
        raise SystemExit("save_draft on the copy failed")
    check((first_folder / "draft_content.json").read_bytes() == orig_content_bytes, "original project untouched")
    saved = load_json(copy_dir / "draft_content.json")
    check(saved["id"] == orig_content["id"] and (copy_dir / "Timelines" / saved["id"]).is_dir(), "content id kept, Timelines/<id> matches")
    orig_ids = track_ids(orig_content)
    saved_ids = track_ids(saved)
    check([i for i in saved_ids if i in orig_ids] == orig_ids, "existing tracks kept in order")
    check(len(saved["tracks"]) == len(orig_ids) + 3, f"{len(saved['tracks'])} tracks (= {len(orig_ids)} + blur + 2 text)")
    check(all(seg["track_render_index"] == i for i, t in enumerate(saved["tracks"]) if str(t["name"]).startswith("text_mcp_") for seg in t["segments"]),
          "track_render_index == track index for every NEW segment (source draft was upstream-made, not renumbered)")
    check(not te.dangling_refs(saved), f"no dangling material refs in the saved copy: {te.dangling_refs(saved)[:3]}")
    check(saved["duration"] == 4_000_000, f"saved duration {saved['duration']}")
    mm = mirrors_equal(copy_dir)
    check(mm and all(ok for _, ok in mm), f"mirrors updated: {mm}")
    check(list(copy_dir.glob("draft_content.json.vectcut-bak-*")), "backup file written")
    meta2 = load_json(copy_dir / "draft_meta_info.json")
    check(meta2["tm_draft_modified"] > copy_meta["tm_draft_modified"] and meta2["tm_duration"] == 4_000_000, "meta tm_draft_modified/tm_duration updated")
    check(te.validate_timeline(saved) == 4_000_000, "saved timeline validates")
    leaks = [str(p.relative_to(copy_dir)) for p in copy_dir.rglob("*.json") if "/tmp/vectcut-stage" in p.read_text(encoding="utf-8", errors="ignore")]
    check(not leaks, f"no stage paths left (leaks: {leaks})")

    print("\n[10] open the copy IN PLACE, add a masked+blurred clip, save twice")
    op2 = rpc.tool("open_project", {"name": copy_name, "mode": "in_place"})
    check(op2.get("success") and op2["result"]["mode"] == "in_place", f"open_project in_place -> {op2.get('error') or 'ok'}")
    did2 = op2["result"]["draft_id"]
    if local_sample:
        av = rpc.tool("add_video", {"draft_id": did2, "video_url": "/media/example.mp4", "start": 0, "end": 2,
                                    "target_start": 5, "width": 1080, "height": 1920, "mask_type": "rectangle",
                                    "mask_center_x": 0, "mask_center_y": -0.6, "mask_size": 0.4, "mask_rect_width": 1.0,
                                    "effect": "Blur", "effect_strength": 70})
        check(av.get("success") and av["result"].get("effect") == "Blur", f"add_video with mask+effect -> {av.get('error') or av.get('result')}")
    sv2 = rpc.tool("save_draft", {"draft_id": did2}, timeout=600)
    if not check(sv2.get("success"), f"save_draft in_place -> {sv2.get('error') or 'ok'}"):
        raise SystemExit("in-place save failed")
    saved2 = load_json(copy_dir / "draft_content.json")
    check([i for i in track_ids(saved2) if i in saved_ids] == saved_ids, "all previous tracks kept")
    if local_sample:
        newt = saved2["tracks"][-1]
        by_id = te.build_index(saved2["materials"])
        cats = sorted(by_id[r][0] for r in newt["segments"][0]["extra_material_refs"] if r in by_id)
        check(newt["type"] == "video" and "common_mask" in cats and "video_effects" in cats, f"new clip has mask+effect refs {cats}")
        check(list((copy_dir / "assets" / "video").glob("*.mp4")), "media copied into the project assets")
        check(saved2["duration"] == 7_000_000, f"duration after in-place add {saved2['duration']}")
    n_texts, n_videos = len(saved2["materials"]["texts"]), len(saved2["materials"]["videos"])
    sv3 = rpc.tool("save_draft", {"draft_id": did2}, timeout=600)
    saved3 = load_json(copy_dir / "draft_content.json")
    check(sv3.get("success") and len(saved3["materials"]["texts"]) == n_texts and len(saved3["materials"]["videos"]) == n_videos,
          f"second save does not duplicate materials (texts {n_texts}, videos {n_videos})")
    check(len(list(copy_dir.glob("draft_content.json.vectcut-bak-*"))) <= 3, "backups pruned to 3")

    print("\n[11] negatives for existing projects")
    (DRAFTS / ".recycle_bin").mkdir(exist_ok=True)
    for bad in ("../x", ".recycle_bin", "nope", "smoke test/../smoke test"):
        rr = rpc.tool("open_project", {"name": bad})
        check(not rr.get("success"), f"open_project({bad!r}) rejected: {rr.get('error', '')[:60]}")
    op3 = rpc.tool("open_project", {"name": copy_name, "mode": "in_place"})
    did3 = op3["result"]["draft_id"]
    before = (copy_dir / "draft_content.json").read_bytes()
    # the file belongs to the container's uid; touch it from a throwaway container (trailing newline keeps JSON valid)
    subprocess.run(["docker", "run", "--rm", "--entrypoint", "/bin/sh", "-v", f"{copy_dir}:/p", args.image,
                    "-c", "printf '\\n' >> /p/draft_content.json"], check=True)
    stale = rpc.tool("save_draft", {"draft_id": did3}, timeout=600)
    check(not stale.get("success") and "changed on disk" in stale.get("error", ""), f"save after external change rejected: {stale.get('error', '')[:60]}")
    check((copy_dir / "draft_content.json").read_bytes() == before + b"\n", "file untouched after the rejected save")
    kf = rpc.tool("set_segment", {"draft_id": did3, "track": 99, "segment": 0, "volume": 0})
    check(not kf.get("success"), f"bad track index rejected: {kf.get('error', '')[:60]}")

    print("\n[12] round-trip on a real CapCut-authored draft_content.json")
    if REAL_FIXTURE is None or not REAL_FIXTURE.is_file():
        print(f"       skipped: VECTCUT_REAL_FIXTURE not set or not found ({REAL_FIXTURE})")
        return
    fx = DRAFTS / "fixture-real"
    fx.mkdir(exist_ok=True)
    shutil.copyfile(REAL_FIXTURE, fx / "draft_content.json")
    meta_src = REAL_FIXTURE.parent / "draft_meta_info.json"
    if meta_src.is_file():
        shutil.copyfile(meta_src, fx / "draft_meta_info.json")
    os.chmod(fx, 0o777)
    real = load_json(fx / "draft_content.json")
    op4 = rpc.tool("open_project", {"name": "fixture-real", "mode": "copy", "copy_name": "fixture-real-mcp"})
    check(op4.get("success"), f"open real fixture -> {op4.get('error') or 'ok'}")
    did4 = op4["result"]["draft_id"]
    tr = op4["result"]["summary"]["tracks"]
    print("       tracks: " + ", ".join(f"[{t['index']}]{t['type']}x{t['segment_count']}" for t in tr))
    dr = rpc.tool("delete_range", {"draft_id": did4, "start_sec": 14.5, "end_sec": 25.0})
    check(dr.get("success") and abs(dr["result"]["duration_sec"] - 82.4) < 0.01, f"delete_range on real file -> {dr.get('result', {}).get('duration_sec')}")
    base_ti = next(t["index"] for t in tr if t["type"] == "video" and t["segment_count"] == 9)
    br = rpc.tool("blur_region", {"draft_id": did4, "track": base_ti, "segment": 6, "center_x": 0, "center_y": -0.55, "width": 1, "height": 0.6})
    check(br.get("success"), f"blur_region on real clip -> {br.get('error') or 'ok'}")
    tx = rpc.tool("add_text", {"draft_id": did4, "text": "MCP check", "start": 0, "end": 3, "font_size": 12})
    check(tx.get("success"), f"add_text -> {tx.get('error') or 'ok'}")
    sv4 = rpc.tool("save_draft", {"draft_id": did4}, timeout=600)
    check(sv4.get("success") and not sv4.get("result", {}).get("materials_failed"), f"save real copy -> {sv4.get('error') or 'ok'}")
    saved4 = load_json(DRAFTS / "fixture-real-mcp" / "draft_content.json")
    check(set(saved4.keys()) == set(real.keys()), "all top-level keys preserved")
    check(saved4["platform"] == real["platform"] and saved4["id"] == real["id"] and saved4["version"] == real["version"], "platform/id/version untouched")
    real_ids = track_ids(real)
    check([i for i in track_ids(saved4) if i in real_ids] == real_ids, "original track order preserved")
    try:
        check(te.validate_timeline(saved4, check_refs=True) > 0, "saved real timeline validates (all refs resolve)")
    except te.EditError as exc:
        check(False, f"saved real timeline validates: {exc}")
    check(all(seg["track_render_index"] == i for i, t in enumerate(saved4["tracks"]) for seg in t["segments"]), "track_render_index renumbered consistently")


def open_existing_flow(rpc, args):
    """Live test against the real CapCut folder: open NAME as a copy, edit, save. Nothing is deleted."""
    name = args.open_existing
    print(f"\n[live] open_project({name!r}, copy)")
    op = rpc.tool("open_project", {"name": name, "mode": "copy"})
    if not check(op.get("success"), f"open_project -> {op.get('error') or 'ok'}"):
        return
    did = op["result"]["draft_id"]
    tracks = op["result"]["summary"]["tracks"]
    print("       tracks: " + ", ".join(f"[{t['index']}]{t['type']}:{t['name']!r}x{t['segment_count']}" for t in tracks))
    print(f"       duration {op['result']['summary']['duration_sec']} s, copy {op['result']['project_folder']}")
    if args.edit_demo:
        dr = rpc.tool("delete_range", {"draft_id": did, "start_sec": 0, "end_sec": 2})
        check(dr.get("success"), f"delete_range(0,2) -> {dr.get('error') or dr['result']['changed']}")
        best = None
        for t in dr["result"]["tracks"]:
            for s in t["segments"]:
                if s["kind"] == "media" and (best is None or s["end_sec"] - s["start_sec"] > best[2]):
                    best = (t["index"], s["index"], s["end_sec"] - s["start_sec"], s["start_sec"])
        if best:
            br = rpc.tool("blur_region", {"draft_id": did, "track": best[0], "segment": best[1], "center_x": 0,
                                          "center_y": -0.6, "width": 1, "height": 0.4, "strength": 60,
                                          "start_sec": best[3], "end_sec": best[3] + min(2.0, best[2])})
            check(br.get("success"), f"blur_region on track {best[0]} seg {best[1]} -> {br.get('error') or br['result']['changed']}")
    tx = rpc.tool("add_text", {"draft_id": did, "text": "MCP check", "start": 0, "end": 3, "font_size": 12,
                               "font_color": "#000000", "background_color": "#FFD200", "background_alpha": 1})
    check(tx.get("success"), f"add_text -> {tx.get('error') or 'ok'}")
    sv = rpc.tool("save_draft", {"draft_id": did}, timeout=600)
    print("       " + json.dumps(sv, ensure_ascii=False)[:1200])
    check(sv.get("success"), "save_draft succeeded")
    if sv.get("success"):
        print(f"\nCOPY READY: {sv['result']['project_folder']} — restart CapCut and open it.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="capcut-mcp:local")
    ap.add_argument("--keep", action="store_true", help="keep .smoke output")
    ap.add_argument("--profile-mode", default="capcut_9", choices=["capcut_9", "upstream"])
    ap.add_argument("--with-url", action="store_true", help="also add a public sample video by URL")
    ap.add_argument("--drafts-dir", default=None,
                    help="host folder to mount at /drafts (default: scratch .smoke/drafts, which is wiped). "
                         "Pass the real CapCut projects dir to generate a project for a live CapCut test; "
                         "nothing is deleted there.")
    ap.add_argument("--capcut-root", default=FAKE_ROOT,
                    help="Windows path of --drafts-dir as CapCut sees it (CAPCUT_DRAFT_ROOT)")
    ap.add_argument("--draft-name", default="smoke test")
    ap.add_argument("--open-existing", default=None, metavar="NAME",
                    help="live test: open project NAME from --drafts-dir as a COPY, add a caption, save (skips [4]-[12])")
    ap.add_argument("--edit-demo", action="store_true",
                    help="with --open-existing: also delete_range(0, 2) and blur the lower half of the longest clip")
    args = ap.parse_args()

    global DRAFTS
    real_target = args.drafts_dir is not None
    if real_target:
        DRAFTS = Path(args.drafts_dir).resolve()
        if not DRAFTS.is_dir():
            sys.exit(f"--drafts-dir does not exist: {DRAFTS}")
        print(f"REAL TARGET: writing into {DRAFTS} (nothing is deleted there)")
    else:
        if DRAFTS.exists():
            try:
                shutil.rmtree(DRAFTS)
            except PermissionError:
                # previous output was written by the container's uid 10001; let the same uid wipe it
                subprocess.run(["docker", "run", "--rm", "--entrypoint", "/bin/sh", "-v", f"{DRAFTS}:/wipe",
                                args.image, "-c", "rm -rf /wipe/* /wipe/.[!.]* 2>/dev/null; true"], check=True)
                shutil.rmtree(DRAFTS)
        DRAFTS.mkdir(parents=True)
        os.chmod(DRAFTS, 0o777)  # container runs as uid 10001
    root = args.capcut_root
    MEDIA.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(MEDIA, 0o755)
    except OSError:
        pass
    local_sample = MEDIA / "example.mp4"
    if SAMPLE_MP4 is not None and SAMPLE_MP4.is_file():
        if local_sample.is_file() and local_sample.stat().st_size != SAMPLE_MP4.stat().st_size:
            # a previously synthesised clip is owned by the container's uid; let the same uid remove it
            os.chmod(MEDIA, 0o777)
            subprocess.run(["docker", "run", "--rm", "--entrypoint", "/bin/sh", "-v", f"{MEDIA}:/wipe",
                            args.image, "-c", "rm -f /wipe/example.mp4"], check=True)
            os.chmod(MEDIA, 0o755)
        if not local_sample.is_file():
            shutil.copy2(SAMPLE_MP4, local_sample)
        print(f"sample video: {SAMPLE_MP4}")
    elif not local_sample.is_file():
        # no sample on the host: synthesise a 5 s test clip with the ffmpeg shipped in the image
        os.chmod(MEDIA, 0o777)
        subprocess.run(["docker", "run", "--rm", "--entrypoint", "ffmpeg", "-v", f"{MEDIA}:/out", args.image,
                        "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "5.1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "/out/example.mp4"], check=True)
        os.chmod(MEDIA, 0o755)
        print(f"sample video: synthesised {local_sample}")

    cmd = [
        "docker", "run", "-i", "--rm", "--init", "--label", "app=capcut-mcp-smoke",
        "-v", f"{DRAFTS}:/drafts",
        "-v", f"{MEDIA}:/media:ro",
        "-e", f"CAPCUT_DRAFT_ROOT={root}",
        "-e", f"VECTCUT_MEDIA_WIN_ROOT={FAKE_MEDIA_ROOT}",
        "-e", f"VECTCUT_PROFILE_MODE={args.profile_mode}",
        "--memory", "2g", "--pids-limit", "256",
        args.image,
    ]
    print("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                            text=True, encoding="utf-8", bufsize=1)
    rpc = Rpc(proc)
    try:
        print("\n[1] handshake")
        init = rpc.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}})
        check(init["result"]["serverInfo"]["name"] == "capcut-mcp", f"serverInfo {init['result']['serverInfo']}")
        rpc.notify("notifications/initialized")
        check(rpc.call("ping")["result"] == {}, "ping")

        print("\n[2] tools/list")
        tools = {t["name"] for t in rpc.call("tools/list")["result"]["tools"]}
        check(tools == EXPECTED_TOOLS, f"{len(tools)} tools: {sorted(tools)}")

        print("\n[3] capcut_healthcheck")
        hc = rpc.tool("capcut_healthcheck")
        for c in hc.get("result", {}).get("checks", []):
            print(f"       {'ok ' if c['ok'] else 'BAD'} {c['name']}: {c['detail']}")
        check(hc.get("success") and hc["result"]["ok"], "healthcheck critical checks ok")

        if args.open_existing:
            open_existing_flow(rpc, args)
            raise SystemExit(0 if not FAILURES else 1)

        print("\n[4] negative cases (must be rejected)")
        check(not rpc.tool("add_video", {"video_url": "/etc/passwd", "draft_id": "dfd_cat_1_00000000"}).get("success"),
              "add_video with /etc/passwd rejected")
        check(not rpc.tool("add_video", {"video_url": "http://127.0.0.1/x.mp4", "draft_id": "dfd_cat_1_00000000"}).get("success"),
              "add_video with loopback URL rejected")
        check(not rpc.tool("save_draft", {"draft_id": "../../etc"}).get("success"), "save_draft with traversal id rejected")
        check(not rpc.tool("add_text", {"text": "x", "start": 0, "end": 1}).get("success"), "add_text without draft_id rejected")
        check(not rpc.tool("add_text", {"text": "x", "start": 0, "end": 1, "draft_id": "dfd_cat_1_00000000"}).get("success"),
              "add_text with unknown draft_id rejected")

        print("\n[5] build a draft")
        cd = rpc.tool("create_draft", {"width": 1080, "height": 1920})
        check(cd.get("success"), f"create_draft -> {cd.get('result')}")
        draft_id = cd["result"]["draft_id"]
        check("draft_url" not in cd["result"], "bogus draft_url stripped")

        t = rpc.tool("add_text", {"text": "MCP test", "start": 0, "end": 3, "draft_id": draft_id,
                                  "font_size": 48, "font_color": "#FFD700", "shadow_enabled": True})
        check(t.get("success"), f"add_text -> {t.get('error') or 'ok'}")

        if local_sample:
            d = rpc.tool("get_video_duration", {"video_url": "/media/example.mp4"})
            dur = (d.get("result") or {}).get("duration_sec")
            print(f"       get_video_duration -> {dur}")
            check(d.get("success") and isinstance(dur, (int, float)) and 4 < dur < 6, "get_video_duration on /media file (~5.1 s)")
            v = rpc.tool("add_video", {"video_url": "/media/example.mp4", "draft_id": draft_id, "start": 0, "end": 5,
                                       "width": 1080, "height": 1920})
            check(v.get("success"), f"add_video /media/example.mp4 -> {v.get('error') or 'ok'}")
            v2 = rpc.tool("add_video", {"video_url": FAKE_MEDIA_ROOT + r"\example.mp4", "draft_id": draft_id,
                                        "start": 0, "end": 2, "target_start": 5, "width": 1080, "height": 1920})
            check(v2.get("success"), f"add_video via Windows path {FAKE_MEDIA_ROOT}\\example.mp4 -> {v2.get('error') or 'ok'}")

        if args.with_url:
            u = rpc.tool("add_video", {"video_url": PUBLIC_MP4, "draft_id": draft_id, "start": 0, "end": 3,
                                       "target_start": 7, "width": 1080, "height": 1920})
            check(u.get("success"), f"add_video {PUBLIC_MP4} -> {u.get('error') or 'ok'}")

        print("\n[6] save_draft")
        sv = rpc.tool("save_draft", {"draft_id": draft_id, "draft_name": args.draft_name}, timeout=600)
        print("       " + json.dumps(sv, ensure_ascii=False)[:1500])
        if not check(sv.get("success"), "save_draft succeeded"):
            raise SystemExit("save_draft failed; skipping on-disk checks")
        res = sv.get("result", {})
        check(res.get("project_folder", "").startswith(root + "\\" + args.draft_name), f"project_folder {res.get('project_folder')}")
        check(not res.get("materials_failed"), f"materials_failed={res.get('materials_failed')}")

        print("\n[7] on-disk checks")
        folder = DRAFTS / res.get("draft_name", args.draft_name)
        check(folder.is_dir(), f"folder exists: {folder}")
        content = json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))
        check(content["version"] == 360000 and content["new_version"] == "183.0.0",
              f"version={content['version']} new_version={content['new_version']}")
        check(content["platform"].get("app_version") == "9.3.0" and content["platform"].get("os") == "windows",
              f"platform={content['platform'].get('app_source')} {content['platform'].get('app_version')} {content['platform'].get('os')}")
        check(content["canvas_config"]["width"] == 1080 and content["canvas_config"]["height"] == 1920, "canvas 1080x1920")
        check(len(content["tracks"]) >= 2, f"{len(content['tracks'])} tracks: {[t['type'] for t in content['tracks']]}")
        fwd_root = root.replace("\\", "/") + "/" + res.get("draft_name", args.draft_name) + "/assets/"
        vids = content["materials"]["videos"]
        check(vids and all(v["path"].startswith(fwd_root) for v in vids), f"material paths -> {[v['path'] for v in vids]}")
        check((folder / "Timelines" / content["id"] / "draft_content.json").is_file(), f"Timelines/{content['id']}/draft_content.json")
        leaks = [str(p.relative_to(folder)) for p in folder.rglob("*")
                 if p.is_file() and p.suffix in {".json", ".bak", ".tmp"} and "/drafts/" in p.read_text(encoding="utf-8", errors="ignore")]
        check(not leaks, f"no container paths left (leaks: {leaks})")
        assets = [p for p in (folder / "assets").rglob("*") if p.is_file()]
        check(assets and all(p.stat().st_size > 0 for p in assets), f"assets: {[p.name + ':' + str(p.stat().st_size) for p in assets]}")
        meta = json.loads((folder / "draft_meta_info.json").read_text(encoding="utf-8"))
        check(meta["draft_fold_path"] == root.replace("\\", "/") + "/" + res.get("draft_name"), f"draft_fold_path {meta['draft_fold_path']}")
        check(meta["draft_root_path"] == root and meta["tm_duration"] > 0 and meta["draft_id"],
              f"draft_root_path {meta['draft_root_path']} tm_duration {meta['tm_duration']}")
        check(len(meta["draft_materials"][0]["value"]) == len(assets), f"{len(meta['draft_materials'][0]['value'])} materials registered")
        check("Timeline 1" in (folder / "Timelines" / "project.json").read_text(encoding="utf-8"), "timeline renamed")

        print("\n[8] second save with the same name must not clobber the first")
        sv2 = rpc.tool("save_draft", {"draft_id": draft_id, "draft_name": args.draft_name}, timeout=600)
        name2 = (sv2.get("result") or {}).get("draft_name")
        check(sv2.get("success") and name2 == f"{args.draft_name} (2)", f"second save -> {name2}")
        check(folder.is_dir() and (DRAFTS / str(name2)).is_dir(), "both project folders exist")

        existing_project_steps(rpc, args, folder, local_sample)
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=20)
        except Exception:
            proc.kill()

    print()
    if FAILURES:
        print(f"SMOKE FAILED: {len(FAILURES)} check(s):")
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print(f"SMOKE PASSED. Project folder: {DRAFTS / res.get('draft_name', args.draft_name)}")


if __name__ == "__main__":
    main()
