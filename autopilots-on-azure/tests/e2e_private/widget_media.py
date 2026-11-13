"""Encode only the current live browser recording into a short GIF."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frames = sorted(args.frames.glob("frame-*.png"))
    if not 2 <= len(frames) <= 80:
        raise ValueError("A live recording requires 2-80 current frames.")
    if [frame.name for frame in frames] != [f"frame-{index:04d}.png" for index in range(len(frames))]:
        raise ValueError("Live recording frames are not contiguous.")
    encoder = shutil.which("ffmpeg")
    if not encoder:
        raise RuntimeError("Install an approved ffmpeg on the private client; GIF creation is not certified.")
    subprocess.run([
        encoder, "-loglevel", "error", "-y", "-framerate", "5",
        "-i", str(args.frames / "frame-%04d.png"), "-frames:v", str(len(frames)),
        "-vf", "scale=960:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
        str(args.output),
    ], check=True)
    if not args.output.is_file() or args.output.stat().st_size == 0:
        raise RuntimeError("Live GIF was not produced.")
    print(json.dumps({"gif": str(args.output), "frames": len(frames), "source": "current-live-browser-screenshots"}))


if __name__ == "__main__":
    main()
