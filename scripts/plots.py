#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "numpy", "plotnine"]
# ///
"""
Plots for the a2_2k Dynamic Optimizer grid (plotnine).

Reads the CSVs written by analyze.py (default: <repo>/results) and writes PNGs
to <results>/plots.

Usage: scripts/plots.py [RESULTS_DIR]
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from plotnine import (
    aes,
    geom_label,
    geom_linerange,
    element_text,
    facet_grid,
    facet_wrap,
    geom_boxplot,
    geom_col,
    geom_hline,
    geom_line,
    geom_point,
    geom_ribbon,
    ggplot,
    labs,
    position_dodge,
    scale_color_brewer,
    scale_fill_brewer,
    scale_color_manual,
    scale_fill_manual,
    scale_x_continuous,
    scale_x_log10,
    scale_y_continuous,
    scale_y_log10,
    theme,
    theme_bw,
)

RESULTS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "results"
PLOTS = RESULTS / "plots"
CODECS = ["x264", "x265", "vp9", "libaom", "svtav1"]
TARGETS = [95, 90, 80, 70, 60, 50]
METRIC_LABEL = {"vmaf_5h": "VMAF v1 5H", "vmaf_3h": "VMAF v1 3H"}
W, H, DPI = 10, 7.5, 200
RES_LEVELS = ["270p", "360p", "480p", "540p", "720p", "1080p"]
# Okabe-Ito (color-blind safe), one clearly distinct hue per resolution
RES_COLORS = ["#E69F00", "#56B4E9", "#009E73", "#CC79A7", "#0072B2", "#D55E00"]
RATE_BREAKS = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000]


def rate_label(breaks):
    # kbit/s -> "500k", "1M", "2.5M"
    return [f"{b / 1000:g}M" if b >= 1000 else f"{b:g}k" for b in breaks]


def rate_scale(axis: str = "x", **kw):
    f = scale_x_log10 if axis == "x" else scale_y_log10
    return f(breaks=RATE_BREAKS, labels=rate_label, **kw)

BASE = theme_bw() + theme(figure_size=(W, H), legend_position="bottom")


def short(clip: str) -> str:
    # strip the resolution/fps suffix, keep e.g. "Vertical_bees"
    return re.split(r"_(?=\d)", clip, maxsplit=1)[0]


def cat(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["codec"] = pd.Categorical(df["codec"], CODECS)
    if "clip" in df:
        df["clip_s"] = df["clip"].map(short)
    return df


def save(p, name: str, w: float = W, h: float = H):
    p.save(PLOTS / name, width=w, height=h, dpi=DPI, verbose=False)
    print(f"wrote {PLOTS / name}")


def main():
    PLOTS.mkdir(parents=True, exist_ok=True)
    for old in PLOTS.glob("*.png"):
        old.unlink()

    enc = cat(pd.read_csv(RESULTS / "all_encodes.csv"))
    hull = cat(pd.read_csv(RESULTS / "hull_points.csv"))
    lad = cat(pd.read_csv(RESULTS / "ladder_per_title.csv"))
    lpc = pd.read_csv(RESULTS / "ladder_per_codec.csv")

    for metric in ["vmaf_5h", "vmaf_3h"]:
        mlabel = METRIC_LABEL[metric]
        suffix = metric.removeprefix("vmaf_")
        h = hull[hull.metric == metric]
        lm = lad[(lad.metric == metric) & lad.status.isin(["ok", "below_grid"])]

        # 1) convex hulls of all clips, one facet per clip
        p = (
            ggplot(h, aes("bitrate_kbps", metric, color="codec"))
            + geom_line()
            + geom_point(size=0.6)
            + geom_hline(yintercept=TARGETS, linetype="dotted", color="gray", size=0.3)
            + facet_wrap("clip_s", ncol=6)
            + rate_scale("x")
            + scale_y_continuous(limits=(0, 100))
            + scale_color_brewer(type="qual", palette="Set1")
            + labs(x="Bitrate (bit/s, log)", y=f"{mlabel} @1080p", color="Codec",
                   title=f"Convex hulls per clip and codec ({mlabel})")
            + BASE
            + theme(strip_text=element_text(size=7), axis_text=element_text(size=6))
        )
        save(p, f"hulls_all_clips_{suffix}.png", 14, 10.5)

        # 2) ladder: bitrate needed per target (median and IQR over clips)
        agg = (lm.groupby(["codec", "target"], observed=True)["bitrate_interp_kbps"]
                 .agg(median="median", q25=lambda s: s.quantile(.25), q75=lambda s: s.quantile(.75))
                 .reset_index())
        agg["target_s"] = pd.Categorical(agg["target"].astype(str), [str(t) for t in sorted(TARGETS)])
        dodge = position_dodge(width=0.8)
        p = (
            ggplot(agg, aes("target_s", "median", color="codec"))
            + geom_linerange(aes(ymin="q25", ymax="q75"), position=dodge, size=1.2, alpha=0.6)
            + geom_point(position=dodge, size=3)
            + rate_scale("y")
            + scale_color_brewer(type="qual", palette="Set1")
            + labs(x=f"Target {mlabel}", y="Bitrate on convex hull (bit/s, log)",
                   color="Codec",
                   title=f"Bitrate needed to reach target {mlabel}",
                   subtitle="Dot = median over clips, line = interquartile range")
            + BASE
        )
        save(p, f"ladder_bitrate_{suffix}.png")

        # 2b) one plot per codec: median rate-quality curve per resolution
        # (band = IQR of VMAF over clips at each CRF), with the ladder rungs on top
        for codec in CODECS:
            e = enc[enc.codec == codec]
            curves = (e.groupby(["res", "crf"])
                       .agg(kbps=("bitrate_kbps", "median"), q=(metric, "median"),
                            q25=(metric, lambda v: v.quantile(.25)),
                            q75=(metric, lambda v: v.quantile(.75)))
                       .reset_index())
            curves["res_s"] = pd.Categorical(curves["res"].astype(str) + "p", RES_LEVELS)
            rungs = lpc[(lpc.metric == metric) & (lpc.codec == codec)].copy()
            rungs["res_s"] = pd.Categorical(rungs["most_common_res"].astype(int).astype(str) + "p",
                                            RES_LEVELS)
            rungs["label"] = [f"{r:.0f}p @ {k / 1000:.1f}M" if k >= 1000 else f"{r:.0f}p @ {k:.0f}k"
                              for r, k in zip(rungs["most_common_res"], rungs["median_kbps"])]
            p = (
                ggplot(curves, aes("kbps", "q"))
                + geom_hline(yintercept=TARGETS, linetype="dotted", color="gray", size=0.3)
                + geom_ribbon(aes(ymin="q25", ymax="q75", fill="res_s"), alpha=0.10)
                + geom_line(aes(color="res_s"), size=0.9)
                + geom_point(aes(color="res_s"), size=1.2)
                + geom_point(data=rungs, mapping=aes("median_kbps", "target", fill="res_s"),
                             color="black", size=4, shape="D", show_legend=False)
                + geom_label(data=rungs, mapping=aes("median_kbps", "target", label="label"),
                             ha="right", nudge_x=-0.06, size=8, label_size=0,
                             fill="white", alpha=0.85)
                + rate_scale("x", limits=(30, 20000))
                + scale_y_continuous(limits=(0, 100), breaks=range(0, 101, 10))
                + scale_color_manual(values=RES_COLORS, drop=False)
                + scale_fill_manual(values=RES_COLORS, drop=False)
                + labs(x="Bitrate (bit/s, log)", y=f"{mlabel} @1080p",
                       color="Resolution", fill="Resolution",
                       title=f"{codec}: bitrate ladder ({mlabel})",
                       subtitle="Lines: median over clips per resolution and CRF, band = IQR over clips.\n"
                                "Diamonds: ladder rungs (median hull bitrate per target, most common resolution).")
                + BASE
            )
            save(p, f"ladder_{codec}_{suffix}.png")

        # 3) savings relative to x264 per clip and target
        # Pivot first so each codec is compared with x264 for the same clip and target.
        piv = lm.pivot_table(index=["clip", "target"], columns="codec",
                             values="bitrate_interp_kbps", observed=True)
        rel = (piv.div(piv["x264"], axis=0).drop(columns="x264") - 1) * 100
        rel = rel.reset_index().melt(id_vars=["clip", "target"], var_name="codec",
                                     value_name="delta").dropna()
        rel["codec"] = pd.Categorical(rel["codec"], CODECS[1:])
        rel["target"] = pd.Categorical(rel["target"], TARGETS)
        p = (
            ggplot(rel, aes("target", "delta", fill="codec"))
            + geom_hline(yintercept=0, color="black", size=0.4)
            + geom_boxplot(outlier_size=0.8, position=position_dodge(width=0.85), width=0.75)
            + scale_fill_brewer(type="qual", palette="Set1")
            + labs(x=f"Target {mlabel}", y="Bitrate change vs. x264 (%)", fill="Codec",
                   title=f"Bitrate savings vs. x264 at equal {mlabel}",
                   subtitle="Per clip, interpolated on the convex hull")
            + BASE
        )
        save(p, f"savings_vs_x264_{suffix}.png")

        # 4) resolutions selected by the ladder
        sel = lm.dropna(subset=["sel_res"]).copy()
        sel["sel_res"] = pd.Categorical(sel["sel_res"].astype(int).astype(str) + "p", RES_LEVELS)
        sel["target"] = pd.Categorical(sel["target"], TARGETS)
        cnt = sel.groupby(["codec", "target", "sel_res"], observed=False).size().rename("n").reset_index()
        p = (
            ggplot(cnt, aes("target", "n", fill="sel_res"))
            + geom_col()
            + facet_wrap("codec", nrow=1)
            + scale_fill_manual(values=RES_COLORS)
            + labs(x=f"Target {mlabel}", y="Number of clips", fill="Resolution",
                   title=f"Resolution chosen by the per-title ladder ({mlabel})")
            + BASE
        )
        save(p, f"ladder_resolutions_{suffix}.png")

    # 5) VMAF vs. CRF at 1080p: CRF values are not comparable across encoders
    e1080 = enc[enc.res == 1080]
    agg = (e1080.groupby(["codec", "crf"], observed=True)[["vmaf_3h", "vmaf_5h"]]
               .median().reset_index()
               .melt(id_vars=["codec", "crf"], var_name="metric", value_name="score"))
    agg["metric"] = agg["metric"].map(METRIC_LABEL)
    p = (
        ggplot(agg, aes("crf", "score", color="codec"))
        + geom_line(size=1)
        + geom_point(size=1.5)
        + facet_wrap("metric")
        + scale_y_continuous(limits=(0, 100))
        + scale_color_brewer(type="qual", palette="Set1")
        + labs(x="CRF", y="VMAF @1080p (median over clips)", color="Codec",
               title="VMAF vs. CRF at 1080p")
        + BASE
    )
    save(p, "vmaf_vs_crf_1080p.png")

    # 6) all grid points for one example clip, faceted by codec, colored by resolution
    ex = enc[enc["clip"].str.startswith("Aerial3200")].copy()
    ex["res_s"] = pd.Categorical(ex["res"].astype(str) + "p", RES_LEVELS)
    hx = hull[(hull.metric == "vmaf_5h") & hull["clip"].str.startswith("Aerial3200")]
    p = (
        ggplot(ex, aes("bitrate_kbps", "vmaf_5h"))
        + geom_line(aes(color="res_s"), size=0.6)
        + geom_point(aes(color="res_s"), size=1)
        + geom_line(data=hx, color="black", size=0.9, linetype="dashed")
        + facet_wrap("codec", nrow=2)
        + rate_scale("x")
        + scale_y_continuous(limits=(0, 100))
        + scale_color_manual(values=RES_COLORS)
        + labs(x="Bitrate (bit/s, log)", y="VMAF v1 5H @1080p", color="Resolution",
               title="Aerial3200: resolution x CRF grid and convex hull (dashed)")
        + BASE
    )
    save(p, "example_grid_Aerial3200.png")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
