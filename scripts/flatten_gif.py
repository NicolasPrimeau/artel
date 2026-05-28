#!/usr/bin/env python3
"""
Re-encode a GIF so every frame is a complete, fully-opaque, full-canvas image.

agg emits delta-encoded frames (partial tiles). Some viewers render the first
loop wrong because they build the canvas up as they go. Making every frame
self-contained removes all inter-frame dependency: every loop renders identically.

Short frames are merged and the palette is small to keep the file size sane.

Usage:
    python3 scripts/flatten_gif.py in.gif out.gif [colors] [max_width]
"""

import sys

from PIL import Image, ImageChops, ImageSequence

BG = (13, 17, 23)
CORNERS_OFFSETS = [(0, 0), (-1, 0), (0, -1), (-1, -1)]
MIN_FRAME_MS = 70


def flatten(src: str, dst: str, colors: int = 64, max_width: int = 820) -> tuple[int, int]:
    source = Image.open(src)
    rgb_frames, durations = [], []
    for frame in ImageSequence.Iterator(source):
        rgb_frames.append(frame.convert("RGB"))
        durations.append(frame.info.get("duration", 50))

    merged, merged_durations = [], []
    for image, duration in zip(rgb_frames, durations):
        identical = merged and ImageChops.difference(image, merged[-1]).getbbox() is None
        too_short = merged and duration < MIN_FRAME_MS
        if identical or too_short:
            merged_durations[-1] += duration
        else:
            merged.append(image)
            merged_durations.append(duration)

    width, height = merged[0].size
    if max_width and width > max_width:
        scale = max_width / width
        width, height = max_width, round(height * scale)
        merged = [im.resize((width, height), Image.LANCZOS) for im in merged]

    sample_count = 8
    picks = [
        merged[min(len(merged) - 1, i * len(merged) // sample_count)] for i in range(sample_count)
    ]
    montage = Image.new("RGB", (width, height * sample_count))
    for i, image in enumerate(picks):
        montage.paste(image, (0, height * i))
    master = montage.convert("P", palette=Image.ADAPTIVE, colors=colors)

    palette = master.getpalette()
    paletted = [image.quantize(palette=master, dither=Image.Dither.NONE) for image in merged]

    def dist_to_bg(index: int) -> int:
        color = palette[index * 3 : index * 3 + 3]
        return sum((color[j] - BG[j]) ** 2 for j in range(3))

    order = sorted(range(colors), key=dist_to_bg)
    marker_a = order[0]
    color_a = palette[marker_a * 3 : marker_a * 3 + 3]
    marker_b = next((i for i in order[1:] if palette[i * 3 : i * 3 + 3] != color_a), order[1])

    corners = [(x % width, y % height) for x, y in CORNERS_OFFSETS]
    for i, frame in enumerate(paletted):
        pixels = frame.load()
        marker = marker_a if i % 2 == 0 else marker_b
        for x, y in corners:
            pixels[x, y] = marker

    paletted[0].save(
        dst,
        save_all=True,
        append_images=paletted[1:],
        duration=merged_durations,
        loop=0,
        optimize=False,
        disposal=1,
    )
    return len(rgb_frames), len(merged)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    colors = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    max_width = int(sys.argv[4]) if len(sys.argv) > 4 else 820
    raw, kept = flatten(src, dst, colors, max_width)
    print(f"  {raw} frames -> {kept} kept, every frame full+opaque -> {dst}")
