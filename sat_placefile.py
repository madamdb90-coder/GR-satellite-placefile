#!/usr/bin/env python3
"""
Satellite placefile server for GR2Analyst / GRLevel3, with playback.

GR requests  http://localhost:8000/satellite.txt?lat=..&lon=..&version=..
(GR adds the lat/lon of the radar site you are viewing). This script then builds
a placefile holding a LOOP of GOES frames from NASA GIBS, cropped around that
radar. Every frame is preceded by a TimeRange: line, so as you step or loop
through radar frames GR shows the satellite frame that matches that radar time.

Modes
-----
Live (default): the last --frames frames, 10 minutes apart. The newest frame is
                --latency minutes old (GIBS runs behind real time).
Archive:        --start 2026-10-15T22:00Z --end 2026-10-16T02:00Z
                for replaying a past event next to archived radar.
All times are UTC.

Usage
-----
  python sat_placefile.py
  python sat_placefile.py --half-deg 4.35         (600 x 600 mile box)
  python sat_placefile.py --frames 18 --layer GOES-East_ABI_Band13_Clean_Infrared
  python sat_placefile.py --start 2026-10-15T22:00Z --end 2026-10-16T02:00Z

Then in GR: Windows -> Show Placefile Manager -> add each of these
  http://localhost:8000/geocolor.txt
  http://localhost:8000/visible.txt
  http://localhost:8000/ir.txt
and tick the one you want to see. (The old /satellite.txt address still works
and uses whatever --layer is set to.) Visible is dark at night.

Frames download in the background, so the first load shows few or no frames.
GR re-requests the placefile every 20 seconds while frames are still loading.

Standard library only (Python 3.8+). Pillow is optional, for --alpha.
"""
import argparse
import copy
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WMS_URL = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sat_cache")
IMG_RE = re.compile(r"^sat_[A-Za-z0-9-]+_-?\d+\.\d_-?\d+\.\d_\d{12}\.png$")
MIN_BYTES = 8000          # smaller than this = blank image (no data for that time)
RETRY_FAILED_SECS = 120   # wait this long before retrying a frame that failed
LOADING_REFRESH = 20      # placefile refresh while frames are still loading

# One placefile address per product. Add each to GR's Placefile Manager and
# tick the one you want. (GIBS layer names; East = the GOES-East satellite.)
LAYERS = {
    "geocolor": "GOES-East_ABI_GeoColor",
    "visible": "GOES-East_ABI_Band2_Red_Visible_1km",
    "ir": "GOES-East_ABI_Band13_Clean_Infrared",
}
LABELS = {"geocolor": "GeoColor", "visible": "Visible", "ir": "Infrared"}


def layer_label(layer):
    for key, name in LAYERS.items():
        if name == layer:
            return LABELS[key]
    return layer


LOCK = threading.Lock()
PENDING = set()           # frames being downloaded right now
FAILED = {}               # frame name -> time of last failure
POOL = ThreadPoolExecutor(max_workers=4)


# ---------------------------------------------------------------- geometry
def snap_center(lat, lon):
    """Snap to the nearest 0.5 deg so nearby requests share cached images."""
    return round(lat * 2) / 2.0, round(lon * 2) / 2.0


def bbox_for(clat, clon, half_deg):
    """(west, south, east, north). Longitude span is widened by 1/cos(lat)
    so the covered area is roughly square on the ground."""
    south = max(clat - half_deg, -85.0)
    north = min(clat + half_deg, 85.0)
    half_lon = half_deg / max(math.cos(math.radians(clat)), 0.1)
    return clon - half_lon, south, clon + half_lon, north


# ---------------------------------------------------------------- time
def parse_time(s):
    dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def floor_time(dt, step_min):
    return dt.replace(minute=dt.minute - dt.minute % step_min, second=0, microsecond=0)


def frame_times(args):
    step = timedelta(minutes=args.step)
    if args.start:
        t = floor_time(parse_time(args.start), args.step)
        end = parse_time(args.end) if args.end else t + step * args.frames
        times = []
        while t <= end and len(times) < args.max_frames:
            times.append(t)
            t += step
        return times
    newest = floor_time(datetime.now(timezone.utc) - timedelta(minutes=args.latency), args.step)
    return [newest - step * i for i in range(args.frames - 1, -1, -1)]


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- frames
def frame_name(args, clat, clon, t):
    layer = re.sub(r"[^A-Za-z0-9]+", "-", args.layer)
    return f"sat_{layer}_{clat:.1f}_{clon:.1f}_{t:%Y%m%d%H%M}.png"


def download_png(args, bbox, t):
    west, south, east, north = bbox
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": args.layer,
        "STYLES": "",
        "SRS": "EPSG:4326",
        "BBOX": f"{west:.5f},{south:.5f},{east:.5f},{north:.5f}",
        "WIDTH": args.size,
        "HEIGHT": args.size,
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
        "TIME": iso(t),
    }
    url = WMS_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        ctype = r.headers.get("Content-Type", "")
        data = r.read()
    if not ctype.startswith("image/"):
        # WMS errors come back as XML text, not an image
        raise RuntimeError("GIBS did not return an image: " + data[:300].decode("utf-8", "replace"))
    return data


def apply_alpha(path, alpha):
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed, so --alpha is ignored (pip install pillow)")
        return
    im = Image.open(path).convert("RGBA")
    a = im.getchannel("A").point(lambda v: int(v * alpha))
    im.putalpha(a)
    im.save(path)


def _worker(args, clat, clon, t, name):
    path = os.path.join(CACHE_DIR, name)
    try:
        data = download_png(args, bbox_for(clat, clon, args.half_deg), t)
        if len(data) < MIN_BYTES:
            raise RuntimeError("image looks blank (GIBS probably has no data for that time yet)")
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        if args.alpha < 1.0:
            apply_alpha(tmp, args.alpha)
        os.replace(tmp, path)
        print(f"Frame {t:%Y-%m-%d %H:%MZ} ready")
    except Exception as e:
        print(f"Frame {t:%Y-%m-%d %H:%MZ} failed: {e}")
        with LOCK:
            FAILED[name] = time.time()
    finally:
        with LOCK:
            PENDING.discard(name)


def prune_cache(keep_hours):
    cutoff = time.time() - keep_hours * 3600
    try:
        for fn in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, fn)
            if fn.startswith("sat_") and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except OSError:
        pass


def collect_frames(args, clat, clon):
    """Return ([(time, filename), ...] for frames already on disk, number still loading).
    Missing frames are queued for background download."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    prune_cache(args.keep_hours)
    ready, waiting = [], 0
    for t in frame_times(args):
        name = frame_name(args, clat, clon, t)
        if os.path.exists(os.path.join(CACHE_DIR, name)):
            ready.append((t, name))
            continue
        with LOCK:
            recently_failed = time.time() - FAILED.get(name, 0) < RETRY_FAILED_SECS
            if name not in PENDING and not recently_failed:
                PENDING.add(name)
                POOL.submit(_worker, args, clat, clon, t, name)
            if name in PENDING:
                waiting += 1
    return ready, waiting


# ---------------------------------------------------------------- placefile
def build_placefile(args, host, clat, clon, ready, waiting):
    west, south, east, north = bbox_for(clat, clon, args.half_deg)
    n = args.grid
    live = not args.start
    if waiting:
        refresh = LOADING_REFRESH
    else:
        refresh = args.refresh if live else 3600
    step = timedelta(minutes=args.step)

    lines = [
        f"Title: Satellite loop - {layer_label(args.layer)}",
        f"RefreshSeconds: {refresh}",
        "Threshold: 999",
        f"; {len(ready)} frame(s) ready, {waiting} still loading",
        "",
    ]

    def vtx(i, j):
        lat = north - (north - south) * j / n
        lon = west + (east - west) * i / n
        return f"  {lat:.5f}, {lon:.5f}, {i / n:.5f}, {j / n:.5f}"

    for k, (t, name) in enumerate(ready):
        # Each frame is shown from its own time until the next frame's time.
        # In live mode the newest frame stays up for newer radar scans.
        if k + 1 < len(ready):
            end = ready[k + 1][0]
        else:
            end = t + (timedelta(hours=6) if live else step)
        lines.append(f"TimeRange: {iso(t)} {iso(end)}")
        lines.append(f"Image: http://{host}/{name}")
        # Many small triangles keep the image lined up with GR's map projection
        for j in range(n):
            for i in range(n):
                tl, tr, bl, br = vtx(i, j), vtx(i + 1, j), vtx(i, j + 1), vtx(i + 1, j + 1)
                lines += [tl, tr, bl, tr, br, bl]
        lines.append("End:")
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    args = None

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        a = self.args
        is_placefile = u.path == "/satellite.txt"   # default product (--layer)
        m = re.match(r"^/([a-z]+)\.txt$", u.path)
        if m and m.group(1) in LAYERS:
            a = copy.copy(self.args)
            a.layer = LAYERS[m.group(1)]
            is_placefile = True
        try:
            if is_placefile:
                lat = float(q.get("lat", [a.lat])[0])
                lon = float(q.get("lon", [a.lon])[0])
                clat, clon = snap_center(lat, lon)
                ready, waiting = collect_frames(a, clat, clon)
                host = self.headers.get("Host", f"localhost:{a.port}")
                self._send(200, build_placefile(a, host, clat, clon, ready, waiting))
            elif IMG_RE.match(u.path.lstrip("/")):
                path = os.path.join(CACHE_DIR, u.path.lstrip("/"))
                if not os.path.exists(path):
                    self._send(404, "not found")
                    return
                with open(path, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, "not found")
        except Exception as e:
            print("Error:", e)
            self._send(502, f"error: {e}")

    def log_message(self, fmt, *a):
        print(time.strftime("%H:%M:%S"), fmt % a)


def build_parser():
    p = argparse.ArgumentParser(description="Satellite placefile server with radar-matched playback")
    p.add_argument("--layer", default="GOES-East_ABI_GeoColor",
                   help="GIBS layer for the old /satellite.txt address (default %(default)s)")
    p.add_argument("--frames", type=int, default=12, help="live mode: number of frames (default %(default)s)")
    p.add_argument("--step", type=int, default=10, help="minutes between frames (default %(default)s)")
    p.add_argument("--latency", type=int, default=40,
                   help="live mode: age in minutes of the newest frame (default %(default)s)")
    p.add_argument("--start", help="archive mode start, e.g. 2026-10-15T22:00Z")
    p.add_argument("--end", help="archive mode end, e.g. 2026-10-16T02:00Z")
    p.add_argument("--max-frames", type=int, default=72, help="archive mode frame cap (default %(default)s)")
    p.add_argument("--half-deg", type=float, default=4.0,
                   help="half-height of each image in degrees latitude; 4.35 = a 600 mile box (default %(default)s)")
    p.add_argument("--size", type=int, default=1024, help="image size in pixels (default %(default)s)")
    p.add_argument("--grid", type=int, default=10, help="triangle grid per side (default %(default)s)")
    p.add_argument("--refresh", type=int, default=300, help="live placefile RefreshSeconds (default %(default)s)")
    p.add_argument("--keep-hours", type=float, default=24, help="delete cached frames older than this (default %(default)s)")
    p.add_argument("--alpha", type=float, default=1.0, help="0-1 image opacity, needs Pillow if < 1")
    p.add_argument("--lat", type=float, default=35.0, help="fallback center lat if GR sends none")
    p.add_argument("--lon", type=float, default=-97.0, help="fallback center lon if GR sends none")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--bind", default="127.0.0.1", help="use 0.0.0.0 to allow other machines")
    return p


def main():
    args = build_parser().parse_args()
    Handler.args = args
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    mode = f"archive {args.start} to {args.end or 'auto'}" if args.start else f"live, last {args.frames} frames"
    print(f"Mode: {mode}")
    print("Add these to GR's Placefile Manager (tick one at a time):")
    for key in LAYERS:
        print(f"  http://localhost:{args.port}/{key}.txt   ({LABELS[key]})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()
