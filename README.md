# Smart Fast Forward for Movies and TV shows

This program takes a movie or TV episode and re-encodes it so silent parts (gaps between subtitles) play faster than dialogue parts. You watch quickly without missing any spoken lines.

Everything runs in a single `ffmpeg` invocation — the input is decoded once, subtitles are burned once, segments are stitched with the `concat` filter. There are no intermediate chunk files on disk.

## Requirements

- `ffmpeg` (with libass for the `subtitles` filter, libx264 for encoding)
- Python 3.8+
- `pysrt` (`pip install pysrt`)

## Usage

```sh
python splitspeedconcatV2.py -i <video> -s <subs.srt> -ds <dialog_speed> -ss <silence_speed> [-b] [--high_quality]
```

Common invocations:

```sh
# Use an external .srt, burn subtitles in, default (fast) quality
python splitspeedconcatV2.py -i episode.mkv -s episode.srt -ds 1.5 -ss 3.0 -b

# Pull the second embedded SRT track out of an mkv and use it
python splitspeedconcatV2.py -i episode.mkv -emkv --subtitle_track 1 -ds 1.5 -ss 3.0 -b

# Same, but encode at near-source quality (≈8× slower)
python splitspeedconcatV2.py -i episode.mkv -s episode.srt -ds 1.5 -ss 3.0 -b --high_quality

# Power-user: dial in your own libx264 settings
python splitspeedconcatV2.py -i episode.mkv -s episode.srt -ds 1.5 -ss 3.0 -b --crf 20 --preset veryfast
```

## Flags

| flag | what it does |
|---|---|
| `-i, --input_file` | input video |
| `-s, --subtitle_file` | external SRT (skip with `-emkv`) |
| `-emkv, --extract_subs_mkv` | extract an embedded SRT from the input mkv |
| `--subtitle_track N` | which subtitle stream to extract (0-indexed within subtitle streams, default 0) |
| `-ds, --dialogue_speed` | playback speed during subtitled segments (e.g. `1.5`) |
| `-ss, --silence_speed` | playback speed during gaps (e.g. `3.0`) |
| `-b, --burn_subtitles` | burn subtitles into the video |
| `-o, --output` | output path (default `<input>_output.mp4`) |
| `--crf` | libx264 CRF, lower = better quality (default 27) |
| `--preset` | libx264 preset (default `ultrafast`) |
| `--high_quality` | shorthand for `--crf 18 --preset medium` (near-source quality, much slower) |
| `--no_cleanup` | keep temp workdir for inspection |

## Approximate throughput

Tested on Apple M4 (10 cores) with a 24-minute HEVC 1080p episode (1431s source → 870s output):

| settings | wall | output size |
|---|---|---|
| default (crf 27, ultrafast) | ~100s | ~440 MB |
| `--high_quality` (crf 18, medium) | ~810s | ~470 MB |
