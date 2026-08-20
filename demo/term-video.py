"""Render a text file as a scrolling terminal video (1920x1080, 30 fps)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def render_terminal_video(
    text_path: Path,
    out_path: Path,
    duration: float = 30.0,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
) -> None:
    font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 22)
    bold = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 22)
    margin = 40
    line_h = 26
    # Number of lines that fit on screen; keep a couple spare.
    visible_lines = (height - 2 * margin) // line_h - 2

    raw = text_path.read_text(encoding="utf-8", errors="replace")
    wrapped: list[tuple[str, bool]] = []
    for line in raw.splitlines():
        # Header/rule lines are already bounded; wrap others.
        if len(line) <= 180:
            chunks = [line]
        else:
            chunks = textwrap.wrap(
                line, width=180, replace_whitespace=False, drop_whitespace=False
            ) or [line]
        for i, chunk in enumerate(chunks):
            # Mark banner/header lines in bold.
            is_bold = (
                chunk.startswith("=") or chunk.startswith("-") or chunk.startswith("|")
            )
            wrapped.append((chunk, is_bold))

    # Show each line for a fraction of the requested duration.
    total_lines = max(len(wrapped), 1)
    seconds_per_line = duration / total_lines
    frames_per_line = max(int(round(seconds_per_line * fps)), 1)

    # Build frame buffer progressively so the terminal scrolls as lines appear.
    buf: list[tuple[str, bool]] = []

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")

    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{width}x{height}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(fps),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "fast",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    if proc.stdin is None:
        raise RuntimeError("failed to open ffmpeg stdin")

    bg = (30, 30, 35)
    fg = (220, 220, 220)
    green = (120, 220, 120)
    gray = (150, 150, 150)

    def draw_frame() -> None:
        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        view = buf[-visible_lines:] if len(buf) > visible_lines else buf
        y = margin
        for line, is_bold in view:
            color = green if line.startswith("$") or line.startswith(">") else fg
            if (
                line.startswith("wrote")
                or line.startswith("scenario:")
                or line.startswith("precision")
            ):
                color = gray
            f = bold if is_bold else font
            draw.text((margin, y), line, font=f, fill=color)
            y += line_h
        proc.stdin.write(img.tobytes())

    # Render frames line by line.
    for line, is_bold in wrapped:
        buf.append((line, is_bold))
        for _ in range(frames_per_line):
            draw_frame()

    # Hold the final view for a moment.
    for _ in range(int(fps * 1.5)):
        draw_frame()

    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with {proc.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()
    render_terminal_video(args.input, args.output, args.duration, args.fps)


if __name__ == "__main__":
    main()
