"""
Desktop front-end for splitspeedconcatV2.py.

A pywebview window (the system WebKit, no bundled browser) showing
index.html. Python does the work: ffprobe for the track lists, the script's
own speed_map() for the dialogue/silence timeline, and the script itself as
a subprocess for previews and full runs. A tiny localhost server hands the
page its HTML, the poster frame and the preview clip (with Range support,
which WebKit needs to play video).
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import webview
from webview.dom import DOMEventHandler

import splitspeedconcatV2 as core

HERE = os.path.dirname(os.path.abspath(__file__))
QUALITY = {
    'fast': [],
    'balanced': ['--crf', '23', '--preset', 'veryfast'],
    'high': ['--high_quality'],
}
VIDEO_TYPES = ('Video files (*.mkv;*.mp4;*.m4v;*.mov;*.avi;*.webm)', 'All files (*.*)')


def describe(s):
    tags = s.get('tags', {})
    lang = tags.get('language', 'und')
    bits = [lang.upper() if lang != 'und' else 'Unknown', s.get('codec_name', '?')]
    if s.get('channels'):
        bits.append({1: 'mono', 2: 'stereo', 6: '5.1', 8: '7.1'}.get(s['channels'], '%dch' % s['channels']))
    if tags.get('title'):
        bits.append(tags['title'])
    disp = s.get('disposition', {})
    if disp.get('forced'):
        bits.append('forced')
    if disp.get('default'):
        bits.append('default')
    return ' · '.join(bits)


class Api:
    """Methods here are callable from the page as pywebview.api.<name>()."""

    def __init__(self, workdir):
        self._workdir = workdir
        self._window = None
        self._proc = None
        self._job = {}
        self._runs = 0

    # ── files ───────────────────────────────────────────────────────────

    def pick_video(self):
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=VIDEO_TYPES)
        return self.load(paths[0]) if paths else None

    def pick_srt(self, near):
        paths = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=os.path.dirname(near),
            file_types=('Subtitles (*.srt)', 'All files (*.*)'))
        return paths[0] if paths else None

    def pick_output(self, current):
        path = self._window.create_file_dialog(
            webview.FileDialog.SAVE, directory=os.path.dirname(current),
            save_filename=os.path.basename(current))
        if isinstance(path, (list, tuple)):
            path = path[0] if path else None
        if path and not path.lower().endswith('.mp4'):
            path += '.mp4'
        return path

    def load(self, path):
        out = subprocess.check_output([
            'ffprobe', '-v', 'error', '-of', 'json',
            '-show_entries', 'format=duration:stream=codec_type,codec_name,channels,width,height,avg_frame_rate'
                             ':stream_tags=language,title:stream_disposition=default,forced',
            path,
        ])
        info = json.loads(out)
        streams = info.get('streams', [])
        video = [s for s in streams if s['codec_type'] == 'video']
        audio = [s for s in streams if s['codec_type'] == 'audio']
        subs = [s for s in streams if s['codec_type'] == 'subtitle']
        if not video or not audio:
            raise ValueError('%s has no %s track.' % (os.path.basename(path), 'video' if not video else 'audio'))
        duration = float(info.get('format', {}).get('duration') or 0)
        num, _, den = video[0].get('avg_frame_rate', '24/1').partition('/')
        try:
            fps = float(num) / float(den or 1) or 24.0
        except ValueError:
            fps = 24.0

        # Poster frame from 10% in, skipping cold opens / logos.
        self._runs += 1
        thumb = os.path.join(self._workdir, 'thumb%d.jpg' % self._runs)
        subprocess.run([
            'ffmpeg', '-v', 'error', '-y', '-ss', str(duration * 0.1), '-i', path,
            '-frames:v', '1', '-vf', 'scale=480:-2', '-q:v', '4', thumb,
        ])
        self._thumb = thumb

        def default(lst, ok=lambda s: True):
            return next((i for i, s in enumerate(lst) if s.get('disposition', {}).get('default') and ok(s)),
                        next((i for i, s in enumerate(lst) if ok(s)), -1))

        return {
            'path': path,
            'name': os.path.basename(path),
            'duration': duration,
            'fps': fps,
            'size': '%dx%d' % (video[0].get('width', 0), video[0].get('height', 0)),
            'thumb': '/thumb.jpg?v=%d' % self._runs if os.path.exists(thumb) else None,
            'audio': [describe(s) for s in audio],
            'subs': [describe(s) for s in subs],
            'audio_default': max(default(audio), 0),
            # Forced tracks only cover foreign-language lines, so they'd
            # treat most dialogue as silence.
            'subs_default': default(subs, lambda s: not s.get('disposition', {}).get('forced')),
            'output': os.path.splitext(path)[0] + ' (Sped).mp4',
        }

    def analyze(self, path, track, srt):
        """Dialogue/silence map for the timeline and the length estimate."""
        work = tempfile.mkdtemp(dir=self._workdir)
        try:
            chunks, duration, is_pgs, _ = core.speed_map(path, work, srt or None, track)
        except subprocess.CalledProcessError:
            raise ValueError("Couldn't read that subtitle track.")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return {
            'dialog': [[round(s, 2), round(e, 2)] for k, s, e in chunks if k == 'd'],
            'dialog_secs': sum(e - s for k, s, e in chunks if k == 'd'),
            'silence_secs': sum(e - s for k, s, e in chunks if k == 's'),
            'pgs': is_pgs,
        }

    def exists(self, path):
        return os.path.exists(path)

    def reveal(self, path):
        subprocess.Popen(['open', '-R', path])

    def open_external(self, path):
        subprocess.Popen(['open', path or self._job.get('out')])

    # ── jobs ────────────────────────────────────────────────────────────

    def run(self, o):
        args = ['-i', o['path'], '-ds', str(o['ds']), '-ss', str(o['ss']), '--audio_track', str(o['audio'])]
        args += ['-s', o['srt']] if o['srt'] else ['-emkv', '--subtitle_track', str(o['sub'])]
        if o['burn']:
            args.append('-b')
        args += QUALITY[o['quality']]
        if o['preview']:
            self._runs += 1
            out = os.path.join(self._workdir, 'preview%d.mp4' % self._runs)
            args += ['--start', str(o['start']), '--duration', str(o['length'])]
        else:
            out = o['output']
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        args += ['-o', out]

        self._job = {'out': out, 'preview': o['preview'], 'fps': o['fps'], 'log': [],
                     'expected': None, 'frame': 0, 'started': time.time(), 'cancelled': False}
        # New session so cancel reaches ffmpeg too.
        self._proc = subprocess.Popen(
            [sys.executable, '-u', core.__file__] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        threading.Thread(target=self._read, args=(self._proc, self._job), daemon=True).start()

    def _read(self, proc, job):
        # ffmpeg rewrites its -stats line with \r, so split on both.
        buf = b''
        while True:
            chunk = proc.stdout.read1(4096)
            if not chunk:
                break
            *lines, buf = re.split(rb'[\r\n]', buf + chunk)
            for line in lines:
                self._line(job, line.decode(errors='replace').strip())
        self._line(job, buf.decode(errors='replace').strip())

    def _line(self, job, line):
        if not line:
            return
        m = re.match(r'frame=\s*(\d+)', line)
        if m:
            job['frame'] = int(m.group(1))
            return
        m = re.match(r'Expected output duration: ([\d.]+)s', line)
        if m:
            job['expected'] = float(m.group(1))
        job['log'].append(line)

    def poll(self, since):
        job, proc = self._job, self._proc
        elapsed = time.time() - job['started']
        frac = 0
        if job['expected']:
            frac = min(job['frame'] / (job['expected'] * job['fps']), 0.999)
        state = {'log': job['log'][since:], 'progress': frac, 'elapsed': elapsed,
                 'eta': elapsed / frac - elapsed if frac > 0.02 else None}
        code = proc.poll() if proc else -1
        if code is not None:
            self._proc = None
            state['done'] = {
                'ok': code == 0, 'cancelled': job['cancelled'], 'preview': job['preview'], 'out': job['out'],
                'url': '/preview.mp4?v=%d' % self._runs if job['preview'] and code == 0 else None,
            }
        return state

    def cancel(self):
        # SIGINT so the script's cleanup runs and ffmpeg exits cleanly.
        if self._proc:
            self._job['cancelled'] = True
            os.killpg(self._proc.pid, signal.SIGINT)


def serve(api):
    """Localhost server for the page, the poster frame and preview clips."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.split('?')[0]
            path, ctype = {
                '/': (os.path.join(HERE, 'index.html'), 'text/html; charset=utf-8'),
                '/thumb.jpg': (getattr(api, '_thumb', ''), 'image/jpeg'),
                '/preview.mp4': (api._job.get('out', '') if api._job.get('preview') else '', 'video/mp4'),
            }.get(name, ('', ''))
            if not path or not os.path.exists(path):
                return self.send_error(404)
            size = os.path.getsize(path)
            start, end = 0, size - 1
            m = re.match(r'bytes=(\d*)-(\d*)', self.headers.get('Range', ''))
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), end) if m.group(2) else end
                else:
                    start = max(size - int(m.group(2)), 0)
                self.send_response(206)
                self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
            else:
                self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                with open(path, 'rb') as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        data = f.read(min(1 << 16, left))
                        if not data:
                            break
                        self.wfile.write(data)
                        left -= len(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # WebKit drops range requests it no longer needs

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return 'http://127.0.0.1:%d/' % server.server_address[1]


def main():
    workdir = tempfile.mkdtemp(prefix='ssgui_')
    api = Api(workdir)
    window = webview.create_window(
        'Smart Speedup', serve(api), js_api=api,
        width=760, height=860, min_size=(620, 640), background_color='#151518')
    api._window = window

    def on_drop(e):
        files = e.get('dataTransfer', {}).get('files', [])
        path = files[0].get('pywebviewFullPath') if files else None
        if path:
            window.evaluate_js('loadPath(%s)' % json.dumps(path))

    def on_loaded():
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        if len(sys.argv) > 1:
            window.evaluate_js('loadPath(%s)' % json.dumps(os.path.abspath(sys.argv[1])))

    def on_closing():
        if api._proc and not window.create_confirmation_dialog(
                'Stop the running job?', 'Closing the window cancels the encode in progress.'):
            return False
        api.cancel()

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    try:
        webview.start()
    finally:
        api.cancel()
        shutil.rmtree(workdir, ignore_errors=True)
