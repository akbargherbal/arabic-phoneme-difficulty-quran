#!/usr/bin/env python3
"""Read-only multi-decoder check on the 2 libsndfile-failing donor mp3s.

Task 10.  Nothing here modifies audio: each decoder is only asked to decode the
file.  For each target it records the libsndfile error, then attempts ffmpeg,
torchaudio and librosa decodes, compares decoded duration against ffprobe, and
verifies the file MD5 is unchanged.

Usage:
    python scripts/pron_corrupt_decode_check.py \
        --out data/pron/corrupt_file_decode_check.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess

TARGETS = [
    ("Hudhaify_128kbps", "023005"),
    ("aziz_alili_128kbps", "086008"),
]
# MD5 recorded for these files in Task 7 (fresh GCS re-download was identical).
TASK7_MD5 = {
    "Hudhaify_128kbps/023005": "285a9efbf0d12e573f9afd4b4e36cd78",
    "aziz_alili_128kbps/086008": "e7ef02c74964ab518d4ba99a91f3e93d",
}

TRAINING_LOADER = {
    "decoder": "torchaudio.load (torchaudio decodes via torchcodec/FFmpeg)",
    "file": "NOT PRESENT in either repo",
    "line": None,
    "notes": (
        "The ai-toolkit source that owns the production dataloader is not "
        "vendored in arabic-phoneme-difficulty-quran or "
        "maqamrock-yue2-lora-finetuning and is not on disk anywhere in the "
        "environment, so no line number for the audio-loading call can be "
        "given. maqamrock's verification.md:22 names the source of truth as "
        "ostris/ai-toolkit's toolkit/data_loader.py (AiToolkitDataset), "
        "toolkit/dataloader_mixins.py and "
        "extensions_built_in/audio_models/yue2/yue2_model.py; verification.md:27 "
        "states it is 'Loaded with torchaudio' and auto-resampled to 48k. The "
        "only audio-load call actually present in either repo is a bootstrap "
        "decode smoke test at bootstrap/setup.sh:364 "
        "(torchaudio.load(...)), asserted by bootstrap/setup.sh:362-367. "
        "Decoder identity is therefore taken from the repo's own documented "
        "references and bootstrap assertion (torchaudio), not guessed."
    ),
}


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels,duration",
         "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    duration = float(stream.get("duration") or data["format"]["duration"])
    return {
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
        "duration_s": round(duration, 6),
    }


def ffmpeg_null(path: str, level: str) -> dict:
    proc = subprocess.run(
        ["ffmpeg", "-v", level, "-i", path, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return {"returncode": proc.returncode, "stderr": proc.stderr.strip()}


def ffmpeg_frames(path: str, channels: int, sample_rate: int) -> dict:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-"],
        capture_output=True,
    )
    frames = len(proc.stdout) // (2 * channels)
    return {
        "decoded_frames": frames,
        "decoded_duration_s": round(frames / sample_rate, 6) if sample_rate else None,
        "stderr": proc.stderr.decode(errors="replace").strip(),
    }


def libsndfile(path: str) -> dict:
    import soundfile as sf

    info = None
    info_error = None
    try:
        got = sf.info(path)
        info = {
            "sample_rate": got.samplerate,
            "channels": got.channels,
            "frames": got.frames,
            "duration_s": round(got.duration, 6),
            "format": got.format,
            "subtype": got.subtype,
        }
    except Exception as exc:  # noqa: BLE001
        info_error = f"{type(exc).__name__}: {exc}"

    read_error = None
    try:
        sf.read(path)
    except Exception as exc:  # noqa: BLE001
        read_error = f"{type(exc).__name__}: {exc}"
    return {"info": info, "info_error": info_error, "read_error": read_error}


def torchaudio_decode(path: str) -> dict:
    import torchaudio

    try:
        waveform, sr = torchaudio.load(path)
        frames = int(waveform.shape[-1])
        return {
            "ok": True,
            "error": None,
            "sample_rate": int(sr),
            "channels": int(waveform.shape[0]),
            "frames": frames,
            "duration_s": round(frames / sr, 6),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def librosa_decode(path: str) -> dict:
    import warnings

    import librosa

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            y, sr = librosa.load(path, sr=None, mono=False)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        warnings_text = "; ".join(str(w.message) for w in caught)
    frames = int(y.shape[-1])
    fallback = "audioread" if "audioread" in warnings_text else None
    return {
        "ok": True,
        "error": None,
        "sample_rate": int(sr),
        "channels": int(y.shape[0]),
        "frames": frames,
        "duration_s": round(frames / sr, 6),
        "fallback_backend": fallback,
        "warnings": warnings_text or None,
    }


def diff(duration, ref):
    if duration is None or ref is None:
        return None, None
    d = duration - ref
    return round(d, 6), round(100.0 * d / ref, 5)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio-dir", default="data/pron/donor_audio")
    ap.add_argument("--out", default="data/pron/corrupt_file_decode_check.json")
    args = ap.parse_args(argv)

    doc = {
        "purpose": (
            "Determine whether the 2 libsndfile-failing donor files are "
            "unusable for training or only unusable for libsndfile."
        ),
        "read_only": True,
        "files": {},
        "training_loader": TRAINING_LOADER,
        "notes": {
            "manifest_duration": (
                "data/pron/donor_audio_manifest.json stores only per-reciter "
                "aggregates, not per-file ffprobe durations; the per-file ffprobe "
                "value here was obtained by running ffprobe directly on the same "
                "file (read-only)."
            ),
        },
    }

    for reciter, key in TARGETS:
        path = os.path.join(args.audio_dir, reciter, f"{key}.mp3")
        rel = f"{reciter}/{key}"
        before = md5(path)
        probe = ffprobe(path)

        ff_err = ffmpeg_null(path, "error")
        ff_warn = ffmpeg_null(path, "warning")
        ff_dec = ffmpeg_frames(path, probe["channels"], probe["sample_rate"])
        ff_diff_s, ff_diff_pct = diff(ff_dec["decoded_duration_s"],
                                      probe["duration_s"])

        ta = torchaudio_decode(path)
        if ta.get("ok"):
            ta["duration_diff_s"], ta["duration_diff_pct"] = diff(
                ta["duration_s"], probe["duration_s"])
        lib = librosa_decode(path)
        if lib.get("ok"):
            lib["duration_diff_s"], lib["duration_diff_pct"] = diff(
                lib["duration_s"], probe["duration_s"])

        after = md5(path)
        lf = libsndfile(path)
        libsndfile_error = (
            f"{lf['read_error']} (raised by soundfile.read; soundfile.info "
            f"still reports {lf['info']['sample_rate']} Hz, "
            f"{lf['info']['channels']} ch, {lf['info']['duration_s']} s)"
            if lf["read_error"] and lf["info"] else lf["read_error"]
        )

        doc["files"][rel] = {
            "path": os.path.abspath(path),
            "md5_before": before,
            "md5_after": after,
            "md5_unchanged": before == after,
            "task7_md5": TASK7_MD5[rel],
            "md5_matches_task7": before == TASK7_MD5[rel],
            "libsndfile_error": libsndfile_error,
            "libsndfile_info": lf["info"],
            "ffmpeg": {
                "null_v_error_returncode": ff_err["returncode"],
                "null_v_error_stderr": ff_err["stderr"],
                "null_v_warning_returncode": ff_warn["returncode"],
                "null_v_warning_stderr": ff_warn["stderr"],
                "decoded_frames": ff_dec["decoded_frames"],
                "decoded_duration_s": ff_dec["decoded_duration_s"],
                "duration_diff_s": ff_diff_s,
                "duration_diff_pct": ff_diff_pct,
                "decode_stderr": ff_dec["stderr"] or None,
            },
            "torchaudio": ta,
            "librosa": lib,
            "ffprobe": probe,
            "manifest_ffprobe_duration_s": probe["duration_s"],
        }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    for rel, f in doc["files"].items():
        print(f"{rel}:")
        print(f"  libsndfile   : {f['libsndfile_error']}")
        print(f"  ffmpeg       : rc(error)={f['ffmpeg']['null_v_error_returncode']} "
              f"warn={f['ffmpeg']['null_v_warning_stderr']!r} "
              f"dur={f['ffmpeg']['decoded_duration_s']}s "
              f"diff={f['ffmpeg']['duration_diff_s']}s "
              f"({f['ffmpeg']['duration_diff_pct']}%)")
        print(f"  torchaudio   : ok={f['torchaudio']['ok']} "
              f"dur={f['torchaudio'].get('duration_s')}s "
              f"diff={f['torchaudio'].get('duration_diff_s')}s "
              f"({f['torchaudio'].get('duration_diff_pct')}%)")
        print(f"  librosa      : ok={f['librosa']['ok']} "
              f"dur={f['librosa'].get('duration_s')}s "
              f"backend={f['librosa'].get('fallback_backend')} "
              f"diff={f['librosa'].get('duration_diff_s')}s "
              f"({f['librosa'].get('duration_diff_pct')}%)")
        print(f"  md5 unchanged: {f['md5_unchanged']} "
              f"(matches Task 7: {f['md5_matches_task7']})")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
