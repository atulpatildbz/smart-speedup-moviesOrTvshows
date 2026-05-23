#!/usr/bin/env python3
"""
One-off benchmark harness used to pick the splitspeedconcatV2 implementation.

NOT part of the main pipeline — splitspeedconcatV2.py already ships the
winning strategy ("single_libx264") wired in directly. This file is kept
around so the comparison can be re-run on different hardware, different
source codecs (e.g. AV1), or with new encoder options added later.

The methods below each produce a sped-up output equivalent to running the
original script with -b (subtitles burned). Same logical result, different
ffmpeg pipelines:

  base_libx264       classic burn -> split -> per-chunk speedup -> concat (libx264)
  hw_classic         same as base_libx264 but encoder = h264_videotoolbox
  merged_libx264     no burn pre-pass; subs filter folded into each chunk's encode
  merged_hw          merged + h264_videotoolbox
  par_libx264        classic flow with parallelized chunk speedup
  par_merged_libx264 merged + parallelized, libx264
  par_merged_hw      merged + parallelized + h264_videotoolbox
  single_libx264     one ffmpeg invocation with concat filter, libx264  <-- winner
  single_hw          one ffmpeg invocation with concat filter, h264_videotoolbox

Usage:
  bench.py --method <name> --input clip.mkv --srt clip.srt --dspeed 1.5 --sspeed 3.0 --out out.mp4

Results on Apple M4 with a 5-min HEVC 1080p clip (see commit b365670):
  single_libx264  17.5s   <-- 2.4x faster than base_libx264 (42.6s)
  base_libx264    42.6s
  hw_classic      62.1s   <-- HW encoder is bottlenecked by libass single-threaded
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pysrt


def time_to_secs(t):
    return t.hours * 3600 + t.minutes * 60 + t.seconds + t.milliseconds / 1000


def secs_to_srt_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return pysrt.SubRipTime(h, m, s, ms)


def clean_srt(content):
    content = re.sub(r"\d+\n[\d:, ->]+\n\[[\D]*\]\n\n", "", content)
    content = re.sub(r"[^A-Za-z\n\d: ->?]", "", content)
    return content


def compute_chunks(srt_path, video_duration):
    """Return list of (kind, start_sec, end_sec) where kind in {'s','d'}.

    Mirrors the logic in splitspeedconcatV2.mainSplitWithOffset for what segments
    get created, but expressed as a simple sequence covering the whole video.
    """
    # Mimic the preprocessing the original script does.
    with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    content = clean_srt(content)
    with tempfile.NamedTemporaryFile("w+", suffix=".srt", delete=False) as tf:
        tf.write(content)
        tf_path = tf.name
    subs = pysrt.open(tf_path, encoding="iso-8859-1")
    os.unlink(tf_path)

    list_of_times = []  # list of [start_time, end_time] SubRipTime, merged
    for idx, sub in enumerate(subs):
        if idx == 0:
            last_end = pysrt.SubRipTime(0, 0, 0, 0)
        else:
            last_end = list_of_times[-1][1]
        diff = sub.start - last_end
        if (diff.seconds == 0 and idx != 0) or (diff < pysrt.SubRipTime(0, 0, 0, 0)):
            list_of_times[-1][1] = sub.end
            continue
        list_of_times.append([sub.start, sub.end])

    chunks = []
    for idx, t in enumerate(list_of_times):
        start_s = time_to_secs(t[0])
        end_s = time_to_secs(t[1])
        prev_end_s = 0.0 if idx == 0 else time_to_secs(list_of_times[idx - 1][1])
        # silence chunk before this dialog (only if > 0 length)
        if start_s > prev_end_s:
            chunks.append(("s", prev_end_s, start_s))
        chunks.append(("d", start_s, end_s))
    # trailing silence to end of video
    if chunks and chunks[-1][2] < video_duration:
        chunks.append(("s", chunks[-1][2], video_duration))
    return chunks


def vf_setpts(speed):
    return f"setpts={1.0/speed}*PTS"


def af_atempo(speed):
    parts = []
    s = speed
    while s > 2:
        parts.append("atempo=2.0")
        s /= 2
    parts.append(f"atempo={float(s)}")
    return ",".join(parts)


def get_duration(path):
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(out.strip())


def encoder_args(encoder):
    if encoder == "libx264":
        return ["-vcodec", "libx264", "-crf", "27", "-preset", "ultrafast"]
    if encoder == "h264_videotoolbox":
        # VT doesn't honor -crf; use a constant quality with -q:v or a bitrate.
        # -q:v 60 is roughly comparable visual quality to libx264 crf 27 for 1080p.
        return ["-vcodec", "h264_videotoolbox", "-q:v", "60", "-realtime", "0"]
    raise ValueError(encoder)


def run(cmd, **kw):
    p = subprocess.run(cmd, **kw)
    if p.returncode != 0:
        print("CMD FAILED:", " ".join(str(c) for c in cmd), file=sys.stderr)
        raise SystemExit(p.returncode)
    return p


def burn_subtitles(in_path, srt_path, out_path, encoder):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(in_path)]
    cmd += encoder_args(encoder)
    cmd += ["-c:a", "aac", "-vf", f"subtitles={srt_path}", str(out_path)]
    run(cmd)


def split_chunk(in_path, kind, start, end, out_path, offset):
    """Stream-copy a chunk with a small extra prefix (offset)."""
    real_start = max(0.0, start - offset)
    duration = end - real_start
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{real_start}",
        "-i",
        str(in_path),
        "-t",
        f"{duration}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-map_chapters",
        "-1",
        "-c",
        "copy",
        str(out_path),
    ]
    run(cmd)


def speedup_chunk(in_path, out_path, speed, offset_in_chunk, encoder, srt_filter=None):
    """Re-encode a chunk with setpts/atempo. Optionally trim leading offset and
    optionally apply subtitles filter (used in 'merged' methods)."""
    vspeed = 1.0 / speed
    # trim offset (in original timebase) from start
    offset_after_seek = offset_in_chunk
    vf_parts = []
    if srt_filter:
        vf_parts.append(srt_filter)
    vf_parts.append(f"setpts={vspeed}*PTS")
    vf = ",".join(vf_parts)
    af = af_atempo(speed)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(in_path),
        "-ss",
        f"{offset_after_seek}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
    ]
    cmd += encoder_args(encoder)
    cmd += [
        "-filter:v",
        vf,
        "-filter:a",
        af,
        "-max_muxing_queue_size",
        "1024",
        str(out_path),
    ]
    run(cmd)


def concat_files(file_list, out_path):
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
        for p in file_list:
            f.write(f"file '{p}'\n")
        mylist = f.name
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        mylist,
        "-c",
        "copy",
        str(out_path),
    ]
    run(cmd)
    os.unlink(mylist)


def write_chunk_srt(chunks, full_srt_path, work_dir):
    """For 'merged' methods: write a per-chunk SRT shifted to chunk-local 0,
    that contains only the subs whose midpoint falls within the chunk."""
    # Use the cleaned SRT for parity with the original script's burn behavior.
    with open(full_srt_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    cleaned = clean_srt(content)
    tmp = work_dir / "_clean.srt"
    tmp.write_text(cleaned)
    subs = pysrt.open(str(tmp), encoding="iso-8859-1")

    chunk_srts = []
    for i, (kind, start, end) in enumerate(chunks):
        out_path = work_dir / f"chunk_{i:05d}.srt"
        sub_file = pysrt.SubRipFile()
        idx = 1
        for s in subs:
            ss = time_to_secs(s.start)
            se = time_to_secs(s.end)
            if se <= start or ss >= end:
                continue
            new = pysrt.SubRipItem()
            new.index = idx
            new.start = secs_to_srt_time(max(0.0, ss - start))
            new.end = secs_to_srt_time(max(0.05, se - start))
            new.text = s.text
            sub_file.append(new)
            idx += 1
        if len(sub_file) == 0:
            chunk_srts.append(None)
        else:
            sub_file.save(str(out_path), encoding="utf-8")
            chunk_srts.append(out_path)
    return chunk_srts


# ---- Methods ----

def method_classic(args, encoder):
    """burn subs (full re-encode) -> split -> speedup each chunk -> concat."""
    work = Path(args.work) / f"classic_{encoder}"
    work.mkdir(parents=True, exist_ok=True)
    burned = work / "burned.mp4"
    burn_subtitles(args.input, args.srt, burned, encoder)

    duration = get_duration(burned)
    chunks = compute_chunks(args.srt, duration)

    split_dir = work / "split"
    sped_dir = work / "sped"
    split_dir.mkdir(exist_ok=True)
    sped_dir.mkdir(exist_ok=True)

    sped_files = []
    for i, (kind, start, end) in enumerate(chunks):
        offset = args.offset if start >= args.offset else 0.0
        split_path = split_dir / f"{i:05d}_{kind}.mp4"
        split_chunk(burned, kind, start, end, split_path, offset)
        speed = args.sspeed if kind == "s" else args.dspeed
        sped_path = sped_dir / f"{i:05d}_{kind}.mp4"
        speedup_chunk(split_path, sped_path, speed, offset / speed, encoder)
        sped_files.append(sped_path.resolve())

    concat_files(sped_files, args.out)


def method_merged(args, encoder, parallel=False):
    """No burn pre-pass. Per-chunk speedup encode applies subtitles filter."""
    work = Path(args.work) / f"merged_{encoder}_{'par' if parallel else 'seq'}"
    work.mkdir(parents=True, exist_ok=True)
    duration = get_duration(args.input)
    chunks = compute_chunks(args.srt, duration)

    split_dir = work / "split"
    sped_dir = work / "sped"
    split_dir.mkdir(exist_ok=True)
    sped_dir.mkdir(exist_ok=True)

    chunk_srts = write_chunk_srt(chunks, args.srt, work)

    # split first (stream copy, very fast, sequential is fine)
    split_paths = []
    for i, (kind, start, end) in enumerate(chunks):
        offset = args.offset if start >= args.offset else 0.0
        sp = split_dir / f"{i:05d}_{kind}.mp4"
        split_chunk(args.input, kind, start, end, sp, offset)
        split_paths.append(sp)

    def do_speedup(i):
        kind, start, end = chunks[i]
        offset = args.offset if start >= args.offset else 0.0
        speed = args.sspeed if kind == "s" else args.dspeed
        sped_path = sped_dir / f"{i:05d}_{kind}.mp4"
        srt = chunk_srts[i]
        srt_filter = None
        if srt is not None:
            shifted_path = work / f"chunk_{i:05d}_shift.srt"
            sf = pysrt.open(str(srt), encoding="utf-8")
            shifted = pysrt.SubRipFile()
            for k, s in enumerate(sf, 1):
                ss = time_to_secs(s.start) + offset
                se = time_to_secs(s.end) + offset
                ns = pysrt.SubRipItem(index=k, start=secs_to_srt_time(ss), end=secs_to_srt_time(se), text=s.text)
                shifted.append(ns)
            shifted.save(str(shifted_path), encoding="utf-8")
            srt_filter = f"subtitles={shifted_path}"
        speedup_chunk(split_paths[i], sped_path, speed, offset / speed, encoder, srt_filter=srt_filter)
        return sped_path.resolve()

    if parallel:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            sped_files = list(ex.map(do_speedup, range(len(chunks))))
    else:
        sped_files = [do_speedup(i) for i in range(len(chunks))]

    concat_files(sped_files, args.out)


def method_parallel_classic(args, encoder):
    """Classic flow (burn + split + speedup + concat) but parallelize speedup."""
    work = Path(args.work) / f"parallel_classic_{encoder}"
    work.mkdir(parents=True, exist_ok=True)
    burned = work / "burned.mp4"
    burn_subtitles(args.input, args.srt, burned, encoder)

    duration = get_duration(burned)
    chunks = compute_chunks(args.srt, duration)

    split_dir = work / "split"
    sped_dir = work / "sped"
    split_dir.mkdir(exist_ok=True)
    sped_dir.mkdir(exist_ok=True)

    split_paths = []
    for i, (kind, start, end) in enumerate(chunks):
        offset = args.offset if start >= args.offset else 0.0
        sp = split_dir / f"{i:05d}_{kind}.mp4"
        split_chunk(burned, kind, start, end, sp, offset)
        split_paths.append(sp)

    def do_speedup(i):
        kind, start, end = chunks[i]
        offset = args.offset if start >= args.offset else 0.0
        speed = args.sspeed if kind == "s" else args.dspeed
        sped_path = sped_dir / f"{i:05d}_{kind}.mp4"
        speedup_chunk(split_paths[i], sped_path, speed, offset / speed, encoder)
        return sped_path.resolve()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        sped_files = list(ex.map(do_speedup, range(len(chunks))))

    concat_files(sped_files, args.out)


def method_single_pass(args, encoder):
    """Single ffmpeg invocation. Uses concat filter to stitch segments at
    different speeds, with subtitles burned in. No chunk files on disk."""
    work = Path(args.work) / f"single_{encoder}"
    work.mkdir(parents=True, exist_ok=True)
    duration = get_duration(args.input)
    chunks = compute_chunks(args.srt, duration)

    # Build a filtergraph that:
    # 1. burns subtitles on the whole video once
    # 2. trims into per-chunk video + audio
    # 3. applies setpts/atempo per chunk
    # 4. concats them back
    n = len(chunks)
    # Use original SRT (the script cleans it but we'll skip cleaning here; ffmpeg's
    # subtitles filter is more permissive than the regex-based cleaner anyway).
    # For parity, write the cleaned SRT to a temp file.
    with open(args.srt, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    cleaned = clean_srt(content)
    cleaned_srt = work / "cleaned.srt"
    cleaned_srt.write_text(cleaned)

    # Split the burned video into N copies so each chunk can trim independently.
    # Audio doesn't need splitting because each atrim consumes from a separate
    # input reference (ffmpeg duplicates the audio stream automatically).
    split_outs = "".join(f"[b{i}]" for i in range(n))
    parts = [f"[0:v]subtitles={cleaned_srt},split={n}{split_outs}"]
    asplit_outs = "".join(f"[ain{i}]" for i in range(n))
    parts.append(f"[0:a]asplit={n}{asplit_outs}")
    concat_inputs = []
    for i, (kind, start, end) in enumerate(chunks):
        speed = args.sspeed if kind == "s" else args.dspeed
        vspeed = 1.0 / speed
        parts.append(
            f"[b{i}]trim=start={start}:end={end},setpts=PTS-STARTPTS,setpts={vspeed}*PTS[v{i}]"
        )
        parts.append(
            f"[ain{i}]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,{af_atempo(speed)}[a{i}]"
        )
        concat_inputs.append(f"[v{i}][a{i}]")
    parts.append("".join(concat_inputs) + f"concat=n={n}:v=1:a=1[vout][aout]")

    filtergraph = ";".join(parts)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(args.input),
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
    ]
    cmd += encoder_args(encoder)
    cmd += ["-c:a", "aac", "-max_muxing_queue_size", "1024", str(args.out)]
    # write the filtergraph for debugging
    (work / "filtergraph.txt").write_text(filtergraph)
    run(cmd)


METHODS = {
    "base_libx264": lambda a: method_classic(a, "libx264"),
    "hw_classic": lambda a: method_classic(a, "h264_videotoolbox"),
    "merged_libx264": lambda a: method_merged(a, "libx264", parallel=False),
    "merged_hw": lambda a: method_merged(a, "h264_videotoolbox", parallel=False),
    "par_libx264": lambda a: method_parallel_classic(a, "libx264"),
    "par_merged_libx264": lambda a: method_merged(a, "libx264", parallel=True),
    "par_merged_hw": lambda a: method_merged(a, "h264_videotoolbox", parallel=True),
    "single_libx264": lambda a: method_single_pass(a, "libx264"),
    "single_hw": lambda a: method_single_pass(a, "h264_videotoolbox"),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--method", required=True, choices=list(METHODS.keys()))
    p.add_argument("--input", required=True)
    p.add_argument("--srt", required=True)
    p.add_argument("--dspeed", type=float, default=1.5)
    p.add_argument("--sspeed", type=float, default=3.0)
    p.add_argument("--offset", type=float, default=10.0)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--out", required=True)
    p.add_argument("--work", default="bench_work")
    args = p.parse_args()
    t0 = time.perf_counter()
    METHODS[args.method](args)
    t1 = time.perf_counter()
    print(f"METHOD={args.method} WALL={t1-t0:.3f}s")


if __name__ == "__main__":
    main()
