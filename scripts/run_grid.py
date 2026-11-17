#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Dynamic-Optimizer style encoding grid for the AOM CTC a2_2k set.

For every clip x codec x resolution x CRF:
  1. encode (8-bit 4:2:0, ~2 s GOP, lanczos downscale)
  2. compute video bitrate from packet sizes
  3. run ffmpeg-quality-metrics twice against the 1080p source
     (distorted upscaled bicubic to source resolution, both converted to
     10-bit as recommended for VMAF v1):
       a) VMAF v1.0.16 1080p at 3H viewing distance + PSNR + SSIM
       b) VMAF v1.0.16 1080p at 5H viewing distance (phone-like)
     Clips with >= 50 fps use the hfr variants of both models.
  4. write a per-job result JSON (resumable: existing results are skipped,
     existing encodes are reused, so deleting results/ only re-scores)
"""

import argparse
import concurrent.futures as cf
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from env import root

ROOT = root()
SRC = ROOT / "src"
ENC = ROOT / "encodes"
RES_DIR = ROOT / "results"
LOG = ROOT / "logs"

RESOLUTIONS = [1080, 720, 540, 480, 360, 270]  # short side
CRFS = {
    "x264": list(range(15, 52, 3)),  # 15..51
    "x265": list(range(15, 52, 3)),  # 15..51
    "svtav1": list(range(15, 64, 4)),  # 15..63
    "libaom": list(range(15, 64, 4)),  # 15..63
    "vp9": list(range(15, 64, 4)),  # 15..63
}
THREADS = 4
HFR_FPS = 50


def vmaf_models(fps: float) -> tuple[str, str]:
    # High-frame-rate clips need the matching HFR model for both viewing distances.
    hfr = "hfr_" if fps >= HFR_FPS else ""
    return f"vmaf_v1.0.16_{hfr}3d0h.json", f"vmaf_v1.0.16_{hfr}5d0h.json"


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-of", "json", str(path)],
        check=True, capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    return {"width": s["width"], "height": s["height"],
            "fps": float(Fraction(s["r_frame_rate"]))}


def codec_args(codec: str, crf: int, keyint: int, short_side: int) -> list[str]:
    t = str(THREADS)
    if codec == "x264":
        return ["-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
                "-g", str(keyint), "-keyint_min", str(keyint),
                "-x264-params", f"threads={t}"]
    if codec == "x265":
        return ["-c:v", "libx265", "-preset", "slow", "-crf", str(crf),
                "-x265-params",
                f"keyint={keyint}:min-keyint={keyint}:pools={t}:frame-threads=2:log-level=error"]
    if codec == "svtav1":
        return ["-c:v", "libsvtav1", "-preset", "4", "-crf", str(crf),
                "-g", str(keyint), "-svtav1-params", f"lp={t}"]
    if codec == "libaom":
        return ["-c:v", "libaom-av1", "-cpu-used", "4", "-crf", str(crf), "-b:v", "0",
                "-row-mt", "1", "-tile-columns", "1" if short_side >= 720 else "0",
                "-g", str(keyint), "-threads", t]
    if codec == "vp9":
        return ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "2",
                "-crf", str(crf), "-b:v", "0", "-row-mt", "1",
                "-tile-columns", "1" if short_side >= 720 else "0",
                "-g", str(keyint), "-threads", t]
    raise ValueError(codec)


def video_bytes(path: Path) -> int:
    # Sum video packet payloads so container overhead does not inflate bitrate.
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "packet=size", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True).stdout
    return sum(int(x) for x in out.split() if x.strip())


def fqm(dist: Path, ref: Path, tmp: Path, extra: list[str]) -> dict:
    out = tmp / "fqm.json"
    cmd = ["ffmpeg-quality-metrics", str(dist), str(ref), "--tmp-dir", str(tmp),
           "-s", "bicubic", "-t", str(THREADS), "--vmaf-threads", str(THREADS),
           "--no-summary", "-o", str(out), *extra]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    data = json.loads(out.read_text())
    out.unlink()
    return data


def run_job(clip: Path, info: dict, codec: str, res: int, crf: int) -> str:
    name = clip.stem
    tag = f"{codec}/{name}/{res}p_crf{crf}"
    result_file = RES_DIR / f"{tag}.json"
    if result_file.exists():
        return f"skip {tag}"
    enc_file = ENC / f"{tag}.mkv"
    enc_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.parent.mkdir(parents=True, exist_ok=True)

    portrait = info["height"] > info["width"]
    scale = f"scale={res}:-2" if portrait else f"scale=-2:{res}"
    vf = f"{scale}:flags=lanczos,format=yuv420p"
    keyint = max(1, round(info["fps"] * 2))

    t_enc = None
    # Reuse an encode when rescoring with new metrics; the result JSON controls skipping.
    if not enc_file.exists():
        t0 = time.time()
        tmp_enc = enc_file.with_suffix(".tmp.mkv")
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(clip), "-vf", vf, "-an",
               *codec_args(codec, crf, keyint, res), str(tmp_enc)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        tmp_enc.rename(enc_file)
        t_enc = time.time() - t0

    enc_info = probe(enc_file)
    nbytes = video_bytes(enc_file)
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as td:
        tmp = Path(td)
        t1 = time.time()
        model_3h, model_5h = vmaf_models(info["fps"])
        m_3h = fqm(enc_file, clip, tmp, ["-m", "vmaf", "psnr", "ssim",
                                         "--vmaf-model-path", model_3h, "--vmaf-10bit"])
        m_5h = fqm(enc_file, clip, tmp, ["-m", "vmaf",
                                         "--vmaf-model-path", model_5h, "--vmaf-10bit"])
        t_metrics = time.time() - t1

    n_frames = len(m_3h["vmaf"])
    duration = n_frames / info["fps"]
    g_3h, g_5h = m_3h["global"], m_5h["global"]
    result = {
        "clip": name, "codec": codec, "res": res, "crf": crf,
        "width": enc_info["width"], "height": enc_info["height"],
        "src_width": info["width"], "src_height": info["height"], "fps": info["fps"],
        "frames": n_frames, "duration_s": duration,
        "video_bytes": nbytes, "bitrate_kbps": nbytes * 8 / duration / 1000,
        "vmaf_3h": g_3h["vmaf"]["vmaf"]["average"],
        "vmaf_3h_min": g_3h["vmaf"]["vmaf"]["min"],
        "vmaf_5h": g_5h["vmaf"]["vmaf"]["average"],
        "vmaf_5h_min": g_5h["vmaf"]["vmaf"]["min"],
        "vmaf_model_3h": model_3h, "vmaf_model_5h": model_5h,
        "psnr": g_3h["psnr"]["psnr_avg"]["average"],
        "ssim": g_3h["ssim"]["ssim_avg"]["average"],
        "t_encode_s": t_enc, "t_metrics_s": t_metrics,
    }
    # keep per-frame data compressed
    with gzip.open(result_file.with_suffix(".frames.json.gz"), "wt") as f:
        json.dump({"3h": m_3h, "5h": m_5h["vmaf"]}, f)
    result_file.write_text(json.dumps(result, indent=1))
    enc_s = "reused" if t_enc is None else f"{t_enc:.0f}s"
    return (f"done {tag} {result['bitrate_kbps']:.0f}k 3h={result['vmaf_3h']:.1f} "
            f"5h={result['vmaf_5h']:.1f} enc={enc_s} met={t_metrics:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-j", "--jobs", type=int, default=8)
    ap.add_argument("--clips", nargs="*", help="limit to these clip stems")
    ap.add_argument("--codecs", nargs="*", default=list(CRFS))
    ap.add_argument("--res", nargs="*", type=int, default=RESOLUTIONS)
    ap.add_argument("--crfs", nargs="*", type=int, help="override CRF list (testing)")
    args = ap.parse_args()

    for d in (ENC, RES_DIR, LOG, ROOT / "tmp"):
        d.mkdir(parents=True, exist_ok=True)

    clips = sorted(SRC.glob("*.y4m"))
    if args.clips:
        clips = [c for c in clips if c.stem in args.clips]
    infos = {c: probe(c) for c in clips}

    jobs = []
    # interleave slow (libaom) jobs with fast ones by ordering clip-major
    for c in clips:
        for codec in args.codecs:
            for res in args.res:
                for crf in (args.crfs or CRFS[codec]):
                    jobs.append((c, infos[c], codec, res, crf))
    print(f"{len(jobs)} jobs, {args.jobs} workers", flush=True)

    failed = 0
    t0 = time.time()
    with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_job, *j): j for j in jobs}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            j = futs[fut]
            try:
                msg = fut.result()
            except subprocess.CalledProcessError as e:
                failed += 1
                msg = f"FAIL {j[2]}/{j[0].stem}/{j[3]}p_crf{j[4]}: {e.stderr[-500:] if e.stderr else e}"
            except Exception as e:  # noqa: BLE001
                failed += 1
                msg = f"FAIL {j[2]}/{j[0].stem}/{j[3]}p_crf{j[4]}: {e!r}"
            el = time.time() - t0
            print(f"[{i}/{len(jobs)} {el/60:.1f}min] {msg}", flush=True)
    print(f"finished, {failed} failed, {(time.time()-t0)/3600:.2f} h", flush=True)
    shutil.rmtree(ROOT / "tmp", ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
