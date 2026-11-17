#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "numpy"]
# ///
"""
Convex-hull / per-title ladder analysis (Netflix Dynamic Optimizer style).

Per clip x codec: take all (bitrate, VMAF) points over resolutions x CRF,
build the upper convex hull in linear bitrate space, then for each target
VMAF (95/90/80/70/60/50) report
  - the interpolated hull bitrate that reaches the target
  - the cheapest actual hull encode reaching the target (res, CRF, bitrate)
Done for VMAF v1.0.16 at 5H viewing distance (primary, phone-like) and at 3H.
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from env import root

ROOT = root()
OUT = ROOT / "analysis"
TARGETS = [95, 90, 80, 70, 60, 50]
CODECS = ["x264", "x265", "vp9", "libaom", "svtav1"]


def load() -> pd.DataFrame:
    rows = [json.loads(p.read_text()) for p in (ROOT / "results").rglob("*.json")
            if not p.name.endswith(".frames.json")]
    return pd.DataFrame(rows)


def upper_hull(df: pd.DataFrame, q: str) -> pd.DataFrame:
    # For equal bitrates, consider the highest-quality encode first.
    d = df.sort_values(["bitrate_kbps", q], ascending=[True, False])
    # Pareto: quality strictly increasing with bitrate
    keep, best = [], -np.inf
    for idx, r in d.iterrows():
        if r[q] > best:
            keep.append(idx)
            best = r[q]
    p = d.loc[keep]
    # upper concave hull (monotone chain)
    hull = []
    for idx, r in p.iterrows():
        x, y = r["bitrate_kbps"], r[q]
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2][1:], hull[-1][1:]
            # A non-right turn makes the middle point redundant on the upper hull.
            if (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1) >= 0:  # not a right turn
                hull.pop()
            else:
                break
        hull.append((idx, x, y))
    return df.loc[[h[0] for h in hull]].sort_values("bitrate_kbps")


def ladder(hull: pd.DataFrame, q: str) -> list[dict]:
    xs, ys = hull["bitrate_kbps"].to_numpy(), hull[q].to_numpy()
    out = []
    for t in TARGETS:
        rec = {"target": t}
        if ys.max() < t:
            rec["status"] = "unreachable"
        elif ys.min() >= t:
            rec["status"] = "below_grid"  # lowest hull point already above target
            rec["bitrate_interp_kbps"] = xs[0]
        else:
            rec["status"] = "ok"
            # Interpolation estimates the target bitrate; selection below uses a real encode.
            rec["bitrate_interp_kbps"] = float(np.interp(t, ys, xs))
        sel = hull[hull[q] >= t]
        if len(sel):
            s = sel.iloc[0]
            rec.update(sel_res=int(s["res"]), sel_crf=int(s["crf"]),
                       sel_bitrate_kbps=s["bitrate_kbps"], sel_vmaf=s[q],
                       sel_width=int(s["width"]), sel_height=int(s["height"]))
        out.append(rec)
    return out


def main():
    OUT.mkdir(exist_ok=True)
    df = load()
    df.sort_values(["clip", "codec", "res", "crf"]).to_csv(OUT / "all_encodes.csv", index=False)
    print(f"{len(df)} encodes loaded")

    hull_rows, ladder_rows = [], []
    for q in ["vmaf_5h", "vmaf_3h"]:
        for (clip, codec), g in df.groupby(["clip", "codec"]):
            h = upper_hull(g, q)
            hull_rows.append(h.assign(metric=q))
            for rec in ladder(h, q):
                ladder_rows.append({"metric": q, "clip": clip, "codec": codec, **rec})
    hulls = pd.concat(hull_rows)
    hulls.to_csv(OUT / "hull_points.csv", index=False)
    lad = pd.DataFrame(ladder_rows)
    lad.to_csv(OUT / "ladder_per_title.csv", index=False)

    ok = lad[lad["status"].isin(["ok", "below_grid"])]
    summ = (ok.groupby(["metric", "codec", "target"])["bitrate_interp_kbps"]
              .agg(["count", "median", "min", "max"]).round(0).reset_index())
    summ.to_csv(OUT / "ladder_summary.csv", index=False)
    res_dist = (lad.dropna(subset=["sel_res"]).groupby(["metric", "codec", "target", "sel_res"])
                .size().rename("n").reset_index())
    res_dist.to_csv(OUT / "ladder_resolution_distribution.csv", index=False)
    status = lad.groupby(["metric", "codec", "target", "status"]).size().rename("n").reset_index()
    status.to_csv(OUT / "ladder_status.csv", index=False)

    # per-codec ladder table: medians over clips, per metric and target
    rows = []
    for (q, codec, t), g in ok.groupby(["metric", "codec", "target"]):
        sel = g.dropna(subset=["sel_res"])
        rows.append({
            "metric": q, "codec": codec, "target": t, "clips": len(g),
            "median_kbps": g["bitrate_interp_kbps"].median(),
            "q25_kbps": g["bitrate_interp_kbps"].quantile(.25),
            "q75_kbps": g["bitrate_interp_kbps"].quantile(.75),
            "most_common_res": sel["sel_res"].mode().min(),
            "median_crf": sel["sel_crf"].median(),
        })
    (pd.DataFrame(rows).round(0)
       .sort_values(["metric", "codec", "target"], ascending=[True, True, False])
       .to_csv(OUT / "ladder_per_codec.csv", index=False))

    # exact ladder encoding points per codec (primary metric, VMAF 5H)
    lad5 = lad[lad.metric == "vmaf_5h"].merge(
        df[["clip", "codec", "res", "crf", "fps", "vmaf_3h", "psnr", "ssim"]],
        left_on=["clip", "codec", "sel_res", "sel_crf"],
        right_on=["clip", "codec", "res", "crf"], how="left")
    lad5["keyint"] = (lad5["fps"] * 2).round().astype("Int64")
    for c in ["sel_res", "sel_width", "sel_height", "sel_crf"]:
        lad5[c] = lad5[c].astype("Int64")
    cols = ["clip", "target", "status", "bitrate_interp_kbps", "sel_res", "sel_width",
            "sel_height", "sel_crf", "keyint", "sel_bitrate_kbps", "sel_vmaf",
            "vmaf_3h", "psnr", "ssim"]
    for codec in CODECS:
        (lad5[lad5.codec == codec][cols]
            .rename(columns={"sel_vmaf": "vmaf_5h"})
            .sort_values(["clip", "target"], ascending=[True, False])
            .to_csv(OUT / f"ladder_points_{codec}.csv", index=False))

    # median bitrate tables codec x target
    for q in ["vmaf_5h", "vmaf_3h"]:
        piv = (summ[summ.metric == q]
               .pivot(index="target", columns="codec", values="median")
               .reindex(columns=[c for c in CODECS if c in summ.codec.unique()])
               .sort_index(ascending=False))
        piv.to_csv(OUT / f"median_bitrate_{q.removeprefix('vmaf_')}.csv")
        print(f"Median interpolated bitrate (kbps), {q}:\n", piv)

    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
