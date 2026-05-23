"""
Smart Fast Forward for movies and TV shows.

Speeds up the silent parts (gaps between subtitles) and the dialog parts
to user-specified rates, optionally burning subtitles in. The whole thing
runs as a single ffmpeg invocation with a concat filtergraph, so the input
is decoded once, the subtitles filter runs once, and there are no
intermediate chunk files on disk.
"""

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import timeit
from datetime import datetime

import pysrt


def time_to_secs(t):
    return t.hours * 3600 + t.minutes * 60 + t.seconds + t.milliseconds / 1000


def get_duration(path):
    out = subprocess.check_output([
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        path,
    ])
    return float(out.strip())


def clean_srt_content(content):
    content = re.sub(r'\d+\n[\d:, ->]+\n\[[\D]*\]\n\n', '', content)
    content = re.sub(r'[^A-Za-z\n\d: ->?]', '', content)
    return content


def end_subtitle_block(duration):
    """Final 0.5s dialog block at the very end of the video — preserves the
    original script's behavior of forcing the tail of the video into a dialog
    chunk rather than letting it become an open-ended trailing silence."""
    millis = ('%.3f' % (duration - int(duration)))[2:5]
    s_start = max(0.0, duration - 0.5)
    millis_start = ('%.3f' % (s_start - int(s_start)))[2:5]
    h_end = int(duration // 3600); rem = duration % 3600
    m_end = int(rem // 60); sec_end = int(rem % 60)
    h_start = int(s_start // 3600); rem = s_start % 3600
    m_start = int(rem // 60); sec_start = int(rem % 60)
    return "\n\n%02d:%02d:%02d,%s --> %02d:%02d:%02d,%s\n.\n" % (
        h_start, m_start, sec_start, millis_start,
        h_end, m_end, sec_end, millis,
    )


def compute_chunks(srt_path, duration):
    """Return [(kind, start_sec, end_sec)] covering the full video, where
    kind is 's' (silence/gap) or 'd' (dialog). Mirrors the merging rule from
    the original: if a sub starts <1 whole second after the previous one,
    merge into a single dialog block.
    """
    subs = pysrt.open(srt_path, encoding='iso-8859-1')

    list_of_times = []
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
        if start_s > prev_end_s:
            chunks.append(('s', prev_end_s, start_s))
        chunks.append(('d', start_s, end_s))
    if chunks and chunks[-1][2] < duration:
        chunks.append(('s', chunks[-1][2], duration))
    return chunks


def af_atempo(speed):
    parts = []
    s = speed
    while s > 2:
        parts.append('atempo=2.0')
        s /= 2
    parts.append('atempo=%s' % float(s))
    return ','.join(parts)


def build_filtergraph(chunks, srt_path, dspeed, sspeed, burn):
    n = len(chunks)
    vsrc = '[0:v]'
    if burn:
        vsrc += 'subtitles=%s,' % srt_path
    vsrc += 'split=%d' % n + ''.join('[b%d]' % i for i in range(n))
    parts = [
        vsrc,
        '[0:a]asplit=%d' % n + ''.join('[ain%d]' % i for i in range(n)),
    ]
    cat = []
    for i, (kind, start, end) in enumerate(chunks):
        speed = sspeed if kind == 's' else dspeed
        vspeed = 1.0 / speed
        parts.append(
            '[b%d]trim=start=%s:end=%s,setpts=PTS-STARTPTS,setpts=%s*PTS[v%d]'
            % (i, start, end, vspeed, i)
        )
        parts.append(
            '[ain%d]atrim=start=%s:end=%s,asetpts=PTS-STARTPTS,%s[a%d]'
            % (i, start, end, af_atempo(speed), i)
        )
        cat.append('[v%d][a%d]' % (i, i))
    parts.append(''.join(cat) + 'concat=n=%d:v=1:a=1[vout][aout]' % n)
    return ';'.join(parts)


def extract_subs_from_mkv(input_path, track, out_path):
    subprocess.check_call([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-i', input_path,
        '-map', '0:s:%d' % track,
        '-c', 'srt',
        out_path,
    ])


def main():
    parser = argparse.ArgumentParser(
        description='Modifies a video file to play at different speeds when there is sound vs. silence.'
    )
    parser.add_argument('-i', '--input_file', required=True, help='the video file you want modified')
    parser.add_argument('-s', '--subtitle_file', help='SRT file driving the speed map')
    parser.add_argument('-emkv', '--extract_subs_mkv', action='store_true', help='extract subs from input mkv')
    parser.add_argument('--subtitle_track', type=int, default=0, help='which embedded sub stream to use with -emkv (0-indexed within subtitle streams)')
    parser.add_argument('-ds', '--dialogue_speed', type=float, required=True, help='speed when someone is speaking')
    parser.add_argument('-ss', '--silence_speed', type=float, required=True, help='speed when there is silence')
    parser.add_argument('-b', '--burn_subtitles', action='store_true', help='burn subtitles into the video')
    parser.add_argument('-o', '--output', help='output path (default: <input>_output.mp4)')
    parser.add_argument('--no_cleanup', action='store_true', help='keep temp files after completion')
    parser.add_argument('--crf', type=int, default=27, help='libx264 CRF, lower = better quality (default 27)')
    parser.add_argument('--preset', default='ultrafast', help='libx264 preset (default ultrafast)')
    parser.add_argument('--high_quality', action='store_true', help='shorthand for --crf 18 --preset medium (near-source quality, much slower)')
    args = parser.parse_args()

    t0 = timeit.default_timer()
    raw = args.input_file
    out_file = args.output or (raw + '_output.mp4')
    logging.basicConfig(filename=raw + '.log', level=logging.INFO)
    logging.info('Start %s', datetime.now().isoformat())

    workdir = tempfile.mkdtemp(prefix='ssfwd_')
    try:
        if args.extract_subs_mkv:
            srt_in = os.path.join(workdir, 'subs.srt')
            extract_subs_from_mkv(raw, args.subtitle_track, srt_in)
        else:
            if not args.subtitle_file:
                sys.exit('--subtitle_file is required when -emkv is not given')
            srt_in = args.subtitle_file

        duration = get_duration(raw)
        with open(srt_in, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        content = clean_srt_content(content) + end_subtitle_block(duration)
        clean_srt_path = os.path.join(workdir, 'cleaned.srt')
        with open(clean_srt_path, 'w', encoding='utf-8') as f:
            f.write(content)

        chunks = compute_chunks(clean_srt_path, duration)
        n_d = sum(1 for k, _, _ in chunks if k == 'd')
        n_s = sum(1 for k, _, _ in chunks if k == 's')
        logging.info('Duration: %.2fs, chunks: %d (dialog=%d, silence=%d)', duration, len(chunks), n_d, n_s)
        print('Computed %d chunks (%d dialog, %d silence) over %.1fs of video' % (len(chunks), n_d, n_s, duration))

        filtergraph = build_filtergraph(
            chunks, clean_srt_path,
            args.dialogue_speed, args.silence_speed,
            burn=args.burn_subtitles,
        )

        if args.high_quality:
            crf, preset = 18, 'medium'
        else:
            crf, preset = args.crf, args.preset
        logging.info('Encoder: libx264 crf=%s preset=%s', crf, preset)
        print('Encoder: libx264 crf=%s preset=%s' % (crf, preset))

        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-stats', '-y',
            '-i', raw,
            '-filter_complex', filtergraph,
            '-map', '[vout]', '-map', '[aout]',
            '-dn', '-map_chapters', '-1',
            '-vcodec', 'libx264', '-crf', str(crf), '-preset', preset,
            '-c:a', 'aac',
            '-max_muxing_queue_size', '1024',
            out_file,
        ]
        logging.info('Running ffmpeg (%d-byte filtergraph)', len(filtergraph))
        subprocess.check_call(cmd)
    finally:
        if not args.no_cleanup:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print('Kept workdir:', workdir)

    elapsed = timeit.default_timer() - t0
    print('Completed! took %.2fs' % elapsed)
    logging.info('Completed in %.2fs', elapsed)


if __name__ == '__main__':
    main()
