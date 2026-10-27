"""Local speech transcription and copy-to-audio word alignment for CapCut drafts."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any


_MODELS: dict[tuple[str, str], Any] = {}
WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


def tokens(text: str) -> list[tuple[str, str]]:
    """Return (original, normalized) word tokens, ignoring case and accents for matching."""
    out = []
    for match in WORD_RE.finditer(text or ""):
        original = match.group(0)
        folded = unicodedata.normalize("NFD", original.casefold())
        normalized = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
        out.append((original, normalized))
    return out


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def resolve_project_media_path(
    raw_path: str,
    *,
    project_dir: Path,
    drafts_dir: Path,
    media_dir: Path,
    capcut_win_root: str = "",
    media_win_root: str = "",
    fallback_names: tuple[str, ...] = (),
) -> Path:
    """Resolve a stored CapCut media path inside the project or mounted media root."""
    values = [raw_path, *fallback_names]
    for raw in values:
        if not raw or not isinstance(raw, str):
            continue
        normalized = raw.strip().replace("\\", "/")
        candidates: list[Path] = []

        if os.path.isfile(raw):
            candidates.append(Path(raw))
        if normalized.startswith("/"):
            candidates.append(Path(normalized))
        if not re.match(r"^[A-Za-z]:/", normalized):
            candidates.append(project_dir / normalized)

        for win_root, container_root in ((capcut_win_root, drafts_dir), (media_win_root, media_dir)):
            root_norm = win_root.replace("\\", "/").rstrip("/")
            if root_norm and normalized.casefold().startswith((root_norm + "/").casefold()):
                suffix = normalized[len(root_norm):].lstrip("/")
                candidates.append(container_root / Path(suffix))

        for candidate in candidates:
            if not candidate.is_file():
                continue
            if _under(candidate, project_dir) or _under(candidate, drafts_dir) or _under(candidate, media_dir):
                return candidate.resolve()

        basename = Path(normalized).name
        if basename:
            for search_root in (project_dir, media_dir):
                for candidate in search_root.rglob(basename):
                    if candidate.is_file() and _under(candidate, search_root):
                        return candidate.resolve()
    raise FileNotFoundError("The selected narration audio file is not available in the project or media mount")


def select_audio_segments(content: dict[str, Any], *, track_index: int | None = None) -> list[dict[str, Any]]:
    """Get audio-track segments and linked material metadata from serialized CapCut content."""
    materials = content.get("materials") or {}
    by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for category in ("audios", "videos"):
        for material in materials.get(category, []) or []:
            if isinstance(material, dict) and material.get("id"):
                by_id[material["id"]] = (category, material)

    choices = []
    for ti, track in enumerate(content.get("tracks") or []):
        if track.get("type") != "audio" or (track_index is not None and ti != track_index):
            continue
        for si, segment in enumerate(track.get("segments") or []):
            pair = by_id.get(segment.get("material_id"))
            if not pair:
                continue
            category, material = pair
            source = segment.get("source_timerange") or {}
            target = segment.get("target_timerange") or {}
            speed = float(segment.get("speed") or 1.0)
            if speed <= 0:
                speed = 1.0
            choices.append({
                "track_index": ti,
                "track_name": track.get("name", ""),
                "segment_index": si,
                "material_id": material.get("id"),
                "category": category,
                "material": material,
                "target_start_sec": int(target.get("start", 0)) / 1_000_000,
                "target_duration_sec": int(target.get("duration", 0)) / 1_000_000,
                "source_start_sec": int(source.get("start", 0)) / 1_000_000,
                "source_duration_sec": int(source.get("duration", 0)) / 1_000_000,
                "speed": speed,
            })
    return choices


def _model(model_name: str, model_cache: Path):
    key = (model_name, str(model_cache.resolve()))
    if key not in _MODELS:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Local transcription is unavailable: install the optional requirements-asr.lock dependencies"
            ) from exc
        model_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(model_cache))
        _MODELS[key] = WhisperModel(
            model_name, device="cpu", compute_type="int8", download_root=str(model_cache)
        )
    return _MODELS[key]


def transcribe_media(
    audio_path: Path,
    *,
    model_name: str = "small",
    language: str = "auto",
    model_cache: Path,
    transcript_cache: Path,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Transcribe with word timestamps; returns cached or fresh source-time words."""
    stat = audio_path.stat()
    cache_key = hashlib.sha256(
        f"{audio_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{model_name}|{language}".encode("utf-8")
    ).hexdigest()
    cache_file = transcript_cache / f"{cache_key}.json"
    if cache_file.is_file():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return data["words"], data.get("detected_language"), True

    model = _model(model_name, model_cache)
    lang = None if not language or language.casefold() == "auto" else language
    segments, info = model.transcribe(
        str(audio_path), language=lang, beam_size=1, word_timestamps=True, vad_filter=True
    )
    words = []
    for segment in segments:
        if not getattr(segment, "words", None):
            continue
        for word in segment.words:
            if word.start is None or word.end is None:
                continue
            words.append({"text": word.word, "start_sec": float(word.start),
                          "end_sec": float(word.end), "probability": getattr(word, "probability", None)})
    if not words:
        raise RuntimeError("Speech recognition returned no timestamped words for the narration audio")
    transcript_cache.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"detected_language": getattr(info, "language", None), "words": words},
                                     ensure_ascii=False), encoding="utf-8")
    return words, getattr(info, "language", None), False


def align_copy(copy_text: str, audio_words: list[dict[str, Any]]) -> dict[str, Any]:
    """Map copy words to transcript words using normalized sequence alignment."""
    copy_tokens = tokens(copy_text)
    spoken = []
    for wi, item in enumerate(audio_words):
        for original, normalized in tokens(item.get("text", "")):
            spoken.append((original, normalized, wi, item))
    matcher = difflib.SequenceMatcher(
        None, [token[1] for token in copy_tokens], [token[1] for token in spoken], autojunk=False
    )
    mapped: dict[int, tuple[int, str]] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for ci, sj in zip(range(i1, i2), range(j1, j2)):
                mapped[ci] = (sj, "exact")
        elif tag == "replace":
            span = max(i2 - i1, j2 - j1)
            for offset in range(span):
                if i1 + offset >= i2 or j1 + offset >= j2:
                    continue
                left, right = copy_tokens[i1 + offset][1], spoken[j1 + offset][1]
                if left and right and difflib.SequenceMatcher(None, left, right).ratio() >= 0.78:
                    mapped[i1 + offset] = (j1 + offset, "approx")

    output_words = []
    for i, (original, _normalized) in enumerate(copy_tokens):
        entry = {"index": i, "word": original, "matched": False, "match_type": None,
                 "start_sec": None, "end_sec": None}
        if i in mapped:
            spoken_index, match_type = mapped[i]
            source = spoken[spoken_index][3]
            entry.update({"matched": True, "match_type": match_type,
                          "start_sec": source["start_sec"], "end_sec": source["end_sec"]})
        output_words.append(entry)
    matched = sum(word["matched"] for word in output_words)
    exact = sum(word["match_type"] == "exact" for word in output_words)
    return {
        "copy_word_count": len(output_words),
        "matched_word_count": matched,
        "exact_word_count": exact,
        "match_ratio": round(matched / max(1, len(output_words)), 4),
        "exact_match_ratio": round(exact / max(1, len(output_words)), 4),
        "words": output_words,
    }


def locate_copy_phrase(alignment: dict[str, Any], phrase: str) -> dict[str, Any]:
    phrase_tokens = tokens(phrase)
    words = alignment.get("words") or []
    normalized_copy = [tokens(item["word"])[0][1] for item in words if tokens(item["word"])]
    target = [item[1] for item in phrase_tokens]
    occurrences = []
    if not target:
        return {"phrase": phrase, "matched": False, "occurrences": []}
    for start in range(0, len(normalized_copy) - len(target) + 1):
        if normalized_copy[start:start + len(target)] != target:
            continue
        span = words[start:start + len(target)]
        aligned = [word for word in span if word.get("matched") and word.get("start_sec") is not None]
        occurrences.append({
            "start_sec": min((word["start_sec"] for word in aligned), default=None),
            "end_sec": max((word["end_sec"] for word in aligned), default=None),
            "aligned_word_count": len(aligned),
            "word_count": len(span),
            "complete": len(aligned) == len(span),
        })
    return {"phrase": phrase, "matched": bool(occurrences), "occurrences": occurrences}
