#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas"]
# ///
"""
Write the per-codec ladder tables into README.md, between the
<!-- tables:start --> and <!-- tables:end --> markers.

Reads results/ladder_per_codec.csv (written by analyze.py).
"""

import re
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
CODECS = {"x264": "x264", "x265": "x265", "vp9": "VP9", "libaom": "libaom (AV1)",
          "svtav1": "SVT-AV1"}
METRIC = "vmaf_5h"


def table(d: pd.DataFrame) -> str:
    lines = ["| VMAF 5H | Median kbit/s | IQR kbit/s | Most common resolution | Median CRF | Clips |",
             "| ------- | ------------- | ---------- | ---------------------- | ---------- | ----- |"]
    for _, r in d.sort_values("target", ascending=False).iterrows():
        lines.append(f"| {r.target:.0f} | {r.median_kbps:.0f} | {r.q25_kbps:.0f}–{r.q75_kbps:.0f} "
                     f"| {r.most_common_res:.0f}p | {r.median_crf:.0f} | {r.clips:.0f} |")
    return "\n".join(lines)


def main():
    df = pd.read_csv(REPO / "results" / "ladder_per_codec.csv")
    df = df[df.metric == METRIC]
    parts = []
    for codec, name in CODECS.items():
        parts.append(f"### {name}\n\n{table(df[df.codec == codec])}\n\n"
                     f"![{name} ladder](results/plots/ladder_{codec}_5h.png)")
    block = "\n\n".join(parts)
    readme = REPO / "README.md"
    s = readme.read_text()
    # Replace only the generated block; fail if the markers are missing or duplicated.
    s, n = re.subn(r"(<!-- tables:start -->).*?(<!-- tables:end -->)",
                   lambda m: m[1] + "\n\n" + block + "\n\n" + m[2], s, flags=re.S)
    if n != 1:
        raise SystemExit("markers not found in README.md")
    readme.write_text(s)
    print("updated README.md")


if __name__ == "__main__":
    main()
