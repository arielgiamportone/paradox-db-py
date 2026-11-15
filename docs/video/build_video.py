"""Assemble the explainer video from guion.json, the narration audio and the demo transcript.

Usage::

    python build_video.py --audio-dir AUDIO --transcript transcript.txt --work WORK --out video.mp4

* ``AUDIO`` holds one ``scene01.mp3`` ... ``scene08.mp3`` per scene of ``guion.json``
  (generated with the author's cloned voice; not committed).
* ``transcript.txt`` is the captured output of running ``demo/*.py`` and the CLI
  (``$ command`` lines followed by their output), produced with ``run_demo.sh``.
* Requires ffmpeg and Node with Playwright (``render.js``).

Nothing here touches real data: the demo tables come from ``paradoxdb.synthetic``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sh(*args: str) -> None:
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return float(out.strip())


def parse_transcript(text: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    cmd: str | None = None
    for line in text.splitlines():
        if line.startswith("$ "):
            cmd = line[2:].strip()
            blocks[cmd] = []
        elif cmd is not None:
            blocks[cmd].append(line.rstrip())
    for out in blocks.values():
        while out and not out[-1]:
            out.pop()
    return blocks


def srt_time(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def chunk_text(text: str, limit: int = 84) -> list[str]:
    parts = re.split(r"(?<=[.;:])\s+", text)
    chunks: list[str] = []
    for part in parts:
        words = part.split()
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 > limit and cur:
                chunks.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            chunks.append(cur)
    return chunks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-dir", required=True, type=Path)
    ap.add_argument("--transcript", required=True, type=Path)
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    guion = json.loads((HERE / "guion.json").read_text(encoding="utf-8"))
    blocks = parse_transcript(args.transcript.read_text(encoding="utf-8"))
    work = args.work
    (work / "frames").mkdir(parents=True, exist_ok=True)
    (work / "clips").mkdir(exist_ok=True)

    clips: list[Path] = []
    srt: list[str] = []
    cursor = 0.0
    scenes = guion["scenes"]
    for n, scene in enumerate(scenes, start=1):
        audio = args.audio_dir / f"scene{n:02d}.mp3"
        dur = duration(audio) + 0.6  # short breath after each narration
        clip = work / "clips" / f"{scene['id']}.mp4"
        if scene["kind"] == "slide":
            spec = {**scene, "num": f"{n:02d} / {len(scenes):02d}"}
            spec_path = work / "frames" / f"{scene['id']}.json"
            spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            png = work / "frames" / f"{scene['id']}.png"
            sh("node", str(HERE / "render.js"), "slide", str(spec_path), str(png))
            sh(
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-framerate",
                "25",
                "-i",
                str(png),
                "-i",
                str(audio),
                "-vf",
                "fade=t=in:d=0.4,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-tune",
                "stillimage",
                "-r",
                "25",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-t",
                f"{dur:.3f}",
                str(clip),
            )
        else:
            steps = []
            for cmd in scene["steps"]:
                if cmd not in blocks:
                    raise SystemExit(f"command not found in transcript: {cmd}")
                steps.append({"cmd": cmd, "out": blocks[cmd]})
            timeline = {"title": scene["title"], "steps": steps}
            tl_path = work / "frames" / f"{scene['id']}.json"
            tl_path.write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
            sh(
                "node",
                str(HERE / "render.js"),
                "terminal",
                str(tl_path),
                f"{dur:.2f}",
                str(work / "frames"),
            )
            webm = work / "frames" / f"{scene['id']}.webm"
            sh(
                "ffmpeg",
                "-y",
                "-i",
                str(webm),
                "-i",
                str(audio),
                "-filter_complex",
                f"[0:v]fps=25,tpad=stop_mode=clone:stop_duration=30,trim=duration={dur:.3f},"
                "setpts=PTS-STARTPTS,fade=t=in:d=0.4,format=yuv420p[v]",
                "-map",
                "[v]",
                "-map",
                "1:a",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-r",
                "25",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-t",
                f"{dur:.3f}",
                str(clip),
            )
        clips.append(clip)

        # subtitles: proportional to character count within the narration
        chunks = chunk_text(scene["narration"])
        narr = dur - 0.6
        total_chars = sum(len(c) for c in chunks)
        t = cursor
        for c in chunks:
            span = narr * len(c) / total_chars
            srt.append(f"{len(srt) + 1}\n{srt_time(t)} --> {srt_time(t + span)}\n{c}\n")
            t += span
        cursor += dur
        print(f"{scene['id']:<18} {dur:6.1f}s  -> {clip.name}")

    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    joined = work / "joined.mp4"
    sh("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(joined))
    srt_path = args.out.with_suffix(".srt")
    srt_path.write_text("\n".join(srt), encoding="utf-8")
    sh(
        "ffmpeg",
        "-y",
        "-i",
        str(joined),
        "-i",
        str(srt_path),
        "-c",
        "copy",
        "-c:s",
        "mov_text",
        "-metadata:s:s:0",
        "language=spa",
        "-metadata",
        f"title={guion['title']}",
        str(args.out),
    )
    print(f"total {cursor:.1f}s -> {args.out} (+ {srt_path.name})")


if __name__ == "__main__":
    main()
