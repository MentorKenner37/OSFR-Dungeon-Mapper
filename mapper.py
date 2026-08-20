#!/usr/bin/env python3
"""OSFR Dungeon Mapper - evidence-first room graph reconstruction."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from collections import Counter
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "mapper.db"
WEB = ROOT / "web"
VIDEO_DIR = DATA / "videos"
FRAME_DIR = DATA / "frames"
EXPORT_DIR = ROOT / "exports"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

PLAYLISTS = [
    ("The Bat Cave", "PLB0ObTILrzho"), ("Hot Springs Haven", "PLDXBvu308lMc"),
    ("Misty Mountain", "PLA_hL4CfWIhI"), ("Cray Caves", "PLUkTkLCCw9AY"),
    ("Arachnia's Lair", "PLJRLodK-5hww"), ("Bixie Hive", "PLVPElNac5wT8"),
    ("Forest Troll Fort", "PLMWIi6-Mszfg"), ("Bone Bog Cemetery", "PLJ6pGyX6kY-M"),
    ("Sweetwater Climb", "PLAL8zO1mbOWE"), ("Robgoblin Treasure Trove", "PLD1H1GkY7l5M"),
    ("Tavern Cellar", "PLRW1D_CTeHJ4"), ("Haunted Mines", "PLAivkERlT_Yo"),
    ("Sheep Watch", "PLTD4igWHVKKQ"), ("Snowy Canyon", "PLA5APGAyOXCc"),
    ("Necronomicus Unleashed", "PLYvVzaHdT9ag"), ("Darvon's Descent", "PLDxypj112AcA"),
    ("Briarheart Caverns", "PLUf-Lf2JtJRQ"), ("Floren Forest", "PLFmsrTpz99eY"),
    ("Den of Secrets", "PLecFboqk23O8"), ("Bristlewood Glade", "PLZQOBBa1QCSg"),
    ("Trail of Betrayal", "PLZoGqeoZ7Rh8"), ("Howling Hills", "PLce-Ko7lHqeM"),
    ("Bandit Hideout", "PLZCKJFb-4gHk"), ("Tanglewood Fort", "PLJjM8vEbuBSU"),
    ("Croaking Vale", "PLPQxCTo5_GC4"), ("Hiroad Hijinx", "PLZRxeuqZP2xc"),
    ("Cracked Claw Caverns", "PLTGI1EVlYhEw"), ("Danger Peaks", "PLNqqnJGaUhUQ"),
]


def db() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    for p in (DATA, VIDEO_DIR, FRAME_DIR, EXPORT_DIR): p.mkdir(exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS dungeons(id INTEGER PRIMARY KEY, name TEXT UNIQUE, playlist_id TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS videos(id INTEGER PRIMARY KEY, dungeon_id INTEGER NOT NULL REFERENCES dungeons(id),
          youtube_id TEXT, title TEXT NOT NULL, path TEXT, duration REAL, status TEXT DEFAULT 'listed', UNIQUE(dungeon_id,youtube_id));
        CREATE TABLE IF NOT EXISTS rooms(id INTEGER PRIMARY KEY, dungeon_id INTEGER NOT NULL REFERENCES dungeons(id),
          label TEXT NOT NULL, signature TEXT, status TEXT DEFAULT 'candidate', confidence REAL DEFAULT .5,
          representative_frame TEXT, color_signature TEXT, x REAL, y REAL, floor INTEGER DEFAULT 1,
          kind TEXT DEFAULT 'room', notes TEXT DEFAULT '', UNIQUE(dungeon_id,label));
        CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES rooms(id),
          video_id INTEGER NOT NULL REFERENCES videos(id), timestamp REAL NOT NULL, frame_path TEXT NOT NULL,
          signature TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY, dungeon_id INTEGER NOT NULL REFERENCES dungeons(id),
          from_room INTEGER NOT NULL REFERENCES rooms(id), to_room INTEGER NOT NULL REFERENCES rooms(id),
          video_id INTEGER NOT NULL REFERENCES videos(id), timestamp REAL NOT NULL, status TEXT DEFAULT 'candidate');
        """)
        # Safe migrations for databases created by v0.1.
        existing = {r[1] for r in c.execute("PRAGMA table_info(rooms)")}
        for name, declaration in {
            "color_signature": "TEXT", "x": "REAL", "y": "REAL", "floor": "INTEGER DEFAULT 1",
            "kind": "TEXT DEFAULT 'room'", "notes": "TEXT DEFAULT ''",
        }.items():
            if name not in existing: c.execute(f"ALTER TABLE rooms ADD COLUMN {name} {declaration}")
        c.executemany("INSERT OR IGNORE INTO dungeons(name,playlist_id) VALUES(?,?)", PLAYLISTS)


def require(binary: str) -> None:
    if not shutil.which(binary): raise RuntimeError(f"Missing required program: {binary}")


def ppm_hash(path: Path) -> int:
    raw = path.read_bytes()
    m = re.match(br"P6\s+(?:#[^\n]*\n\s*)?(\d+)\s+(\d+)\s+255\s", raw)
    if not m: raise ValueError(f"Unsupported frame: {path}")
    pixels = raw[m.end():]
    grey = [(pixels[i] * 30 + pixels[i+1] * 59 + pixels[i+2] * 11) // 100 for i in range(0, len(pixels), 3)]
    avg = sum(grey) / len(grey)
    # 576-bit signature represented as an integer.
    return sum((v >= avg) << i for i, v in enumerate(grey))


def ppm_fingerprint(path: Path) -> tuple[int, tuple[int, ...]]:
    """Return a structural hash plus an 8-bin histogram for each RGB channel."""
    raw = path.read_bytes()
    m = re.match(br"P6\s+(?:#[^\n]*\n\s*)?(\d+)\s+(\d+)\s+255\s", raw)
    if not m: raise ValueError(f"Unsupported frame: {path}")
    pixels = raw[m.end():]
    hist = [0] * 24
    for i in range(0, len(pixels), 3):
        hist[pixels[i] // 32] += 1
        hist[8 + pixels[i + 1] // 32] += 1
        hist[16 + pixels[i + 2] // 32] += 1
    total = max(1, len(pixels) // 3)
    normalized = tuple(round(v * 1000 / total) for v in hist)
    return ppm_hash(path), normalized


def fingerprint_distance(a_hash: int, a_color: tuple[int, ...], b_hash: int, b_color: tuple[int, ...]) -> float:
    structure = distance(a_hash, b_hash) / 576
    color = sum(abs(a - b) for a, b in zip(a_color, b_color)) / 6000
    return structure * .72 + min(1.0, color) * .28


def distance(a: int, b: int) -> int: return (a ^ b).bit_count()


def add_local_video(dungeon: str, path: str) -> int:
    p = Path(path).expanduser().resolve()
    if not p.is_file(): raise FileNotFoundError(p)
    require("ffprobe")
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(p)
    ], text=True).strip())
    with db() as c:
        did = c.execute("SELECT id FROM dungeons WHERE name=?", (dungeon,)).fetchone()
        if not did: raise ValueError(f"Unknown dungeon: {dungeon}")
        cur = c.execute("INSERT INTO videos(dungeon_id,title,path,duration,status) VALUES(?,?,?,?,?)",
                        (did[0], p.stem, str(p), duration, "ready"))
        return cur.lastrowid


def import_playlist(dungeon: str, download: bool = False) -> dict:
    require("yt-dlp")
    with db() as c:
        row = c.execute("SELECT id,playlist_id FROM dungeons WHERE name=?", (dungeon,)).fetchone()
        if not row: raise ValueError("Unknown dungeon")
        url = f"https://www.youtube.com/playlist?list={row['playlist_id']}"
        meta = json.loads(subprocess.check_output(["yt-dlp", "--flat-playlist", "--dump-single-json", url], text=True))
        for entry in meta.get("entries", []):
            c.execute("INSERT OR IGNORE INTO videos(dungeon_id,youtube_id,title,status) VALUES(?,?,?,?)",
                      (row["id"], entry.get("id"), entry.get("title") or entry.get("id"), "listed"))
    if download:
        target = VIDEO_DIR / re.sub(r"[^A-Za-z0-9._-]+", "_", dungeon)
        target.mkdir(exist_ok=True)
        subprocess.run(["yt-dlp", "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]",
                        "--merge-output-format", "mp4", "-o", str(target / "%(id)s.%(ext)s"), url], check=True)
        with db() as c:
            for f in target.glob("*.mp4"):
                c.execute("UPDATE videos SET path=?,status='ready' WHERE youtube_id=?", (str(f), f.stem))
    return {"title": meta.get("title", dungeon), "count": len(meta.get("entries", []))}


def analyze_video(video_id: int, interval: float = 5.0, threshold: float = .24) -> dict:
    require("ffmpeg")
    with db() as c:
        v = c.execute("SELECT v.*,d.name dungeon FROM videos v JOIN dungeons d ON d.id=v.dungeon_id WHERE v.id=?", (video_id,)).fetchone()
        if not v or not v["path"]: raise ValueError("Video has no local file")
        out = FRAME_DIR / str(video_id)
        if out.exists(): shutil.rmtree(out)
        out.mkdir(parents=True)
        # Tiny PPMs are deterministic input for perceptual hashes; JPGs are human evidence.
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", v["path"],
                        "-vf", f"fps=1/{interval},scale=32:18", str(out / "hash-%06d.ppm")], check=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", v["path"],
                        "-vf", f"fps=1/{interval},scale=640:-2", "-q:v", "4", str(out / "frame-%06d.jpg")], check=True)
        c.execute("DELETE FROM transitions WHERE video_id=?", (video_id,))
        c.execute("DELETE FROM evidence WHERE video_id=?", (video_id,))
        rooms = c.execute("SELECT * FROM rooms WHERE dungeon_id=? AND status!='rejected'", (v["dungeon_id"],)).fetchall()
        signatures = [(r["id"], int(r["signature"], 16), tuple(json.loads(r["color_signature"] or "[]")))
                      for r in rooms if r["signature"] and r["color_signature"]]
        previous = None; created = 0; observations = 0
        for idx, ppm in enumerate(sorted(out.glob("hash-*.ppm")), 1):
            sig, colors = ppm_fingerprint(ppm)
            best = min(((fingerprint_distance(sig, colors, s, color), rid) for rid, s, color in signatures),
                       default=(10**9, None))
            jpg = out / f"frame-{idx:06d}.jpg"; timestamp = (idx - 1) * interval
            if best[0] > threshold:
                number = c.execute("SELECT COUNT(*)+1 FROM rooms WHERE dungeon_id=?", (v["dungeon_id"],)).fetchone()[0]
                label = f"Candidate room {number}"
                while c.execute("SELECT 1 FROM rooms WHERE dungeon_id=? AND label=?", (v["dungeon_id"], label)).fetchone():
                    number += 1
                    label = f"Candidate room {number}"
                angle = len(signatures) * 2.399
                x, y = 600 + 250 * __import__("math").cos(angle), 350 + 250 * __import__("math").sin(angle)
                cur = c.execute("""INSERT INTO rooms(dungeon_id,label,signature,color_signature,representative_frame,x,y)
                                 VALUES(?,?,?,?,?,?,?)""",
                                (v["dungeon_id"], label, hex(sig), json.dumps(colors), str(jpg), x, y))
                rid = cur.lastrowid; signatures.append((rid, sig, colors)); created += 1
            else: rid = best[1]
            c.execute("INSERT INTO evidence(room_id,video_id,timestamp,frame_path,signature) VALUES(?,?,?,?,?)",
                      (rid, video_id, timestamp, str(jpg), hex(sig)))
            observations += 1
            if previous is not None and previous != rid:
                c.execute("INSERT INTO transitions(dungeon_id,from_room,to_room,video_id,timestamp) VALUES(?,?,?,?,?)",
                          (v["dungeon_id"], previous, rid, video_id, timestamp))
            previous = rid
        c.execute("UPDATE videos SET status='analyzed' WHERE id=?", (video_id,))
    return {"rooms_created": created, "observations": observations}


def start_job(kind: str, function, *args) -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK: JOBS[job_id] = {"id": job_id, "kind": kind, "status": "queued", "created": time.time()}
    def runner():
        with JOBS_LOCK: JOBS[job_id]["status"] = "running"
        try:
            result = function(*args)
            with JOBS_LOCK: JOBS[job_id].update(status="complete", result=result, finished=time.time())
        except Exception as exc:
            with JOBS_LOCK: JOBS[job_id].update(status="failed", error=str(exc), finished=time.time())
    threading.Thread(target=runner, daemon=True).start()
    return job_id


def job_update(job_id: str, message: str, current: int = 0, total: int = 0) -> None:
    with JOBS_LOCK:
        if job_id in JOBS: JOBS[job_id].update(message=message, current=current, total=total)


def auto_layout(dungeon: str) -> dict:
    """Lay out the evidence graph as routes and branches, never as a decorative circle."""
    with db() as c:
        d = c.execute("SELECT id FROM dungeons WHERE name=?", (dungeon,)).fetchone()
        if not d: raise ValueError("Unknown dungeon")
        did = d[0]
        rooms = c.execute("SELECT id,floor FROM rooms WHERE dungeon_id=? AND status NOT IN ('rejected','uncertain')", (did,)).fetchall()
        room_ids = {r[0] for r in rooms}
        weighted = c.execute("""SELECT from_room,to_room,COUNT(DISTINCT video_id) videos,COUNT(*) observations
          FROM transitions WHERE dungeon_id=? AND status!='rejected' GROUP BY from_room,to_room
          ORDER BY videos DESC,observations DESC""", (did,)).fetchall()
        adjacency: dict[int, list[tuple[int,int]]] = {rid: [] for rid in room_ids}; incoming = Counter()
        for a,b,videos,observations in weighted:
            if a in room_ids and b in room_ids:
                adjacency[a].append((b, videos * 100 + observations)); incoming[b] += videos * 100 + observations
        first = c.execute("""SELECT e.room_id FROM evidence e JOIN videos v ON v.id=e.video_id
          JOIN rooms r ON r.id=e.room_id WHERE v.dungeon_id=? AND r.status NOT IN ('rejected','uncertain')
          ORDER BY v.id,e.timestamp LIMIT 1""", (did,)).fetchone()
        candidates = sorted(room_ids, key=lambda rid: (incoming[rid], rid))
        entrance_id = first[0] if first and first[0] in room_ids else (candidates[0] if candidates else None)
        roots = ([entrance_id] if entrance_id is not None else []) + [r for r in candidates if r != entrance_id]
        depth: dict[int,int] = {}; order: list[int] = []
        for root in roots:
            if root in depth: continue
            depth[root] = 0 if not order else max(depth.values(), default=0) + 1
            queue = [root]
            while queue:
                node = queue.pop(0); order.append(node)
                for nxt,_weight in sorted(adjacency.get(node, []), key=lambda x: -x[1]):
                    if nxt not in depth:
                        depth[nxt] = depth[node] + 1; queue.append(nxt)
        columns: dict[tuple[int,int], list[int]] = {}
        floors = {r[0]: r[1] or 1 for r in rooms}
        for rid in order: columns.setdefault((floors[rid], depth[rid]), []).append(rid)
        for (floor, column), ids in columns.items():
            for row, rid in enumerate(ids):
                x = 120 + column * 210
                y = 110 + row * 125 + (floor - 1) * 35
                c.execute("UPDATE rooms SET x=?,y=? WHERE id=?", (x, y, rid))
        return {"positioned": len(order), "entrance_room_id": entrance_id}


def classify_uncertain_rooms(dungeon: str) -> dict:
    """Keep weak one-off detections out of the primary map without deleting evidence."""
    with db() as c:
        did = c.execute("SELECT id FROM dungeons WHERE name=?", (dungeon,)).fetchone()[0]
        rows = c.execute("""SELECT r.id,COUNT(e.id) observations,COUNT(DISTINCT e.video_id) videos
          FROM rooms r LEFT JOIN evidence e ON e.room_id=r.id WHERE r.dungeon_id=? AND r.status='candidate'
          GROUP BY r.id""", (did,)).fetchall()
        hidden = 0
        for rid, observations, videos in rows:
            if observations < 3 and videos < 2:
                c.execute("UPDATE rooms SET status='uncertain',confidence=.25 WHERE id=?", (rid,)); hidden += 1
            else:
                confidence = min(.95, .45 + videos * .12 + min(observations, 10) * .025)
                c.execute("UPDATE rooms SET confidence=? WHERE id=?", (confidence, rid))
        return {"uncertain_rooms": hidden}


def map_entire_dungeon(job_id: str, dungeon: str, interval: float = 5.0) -> dict:
    job_update(job_id, "Finding and downloading dungeon footage")
    imported = import_playlist(dungeon, True)
    with db() as c:
        videos = [dict(v) for v in c.execute("""SELECT v.id,v.title FROM videos v JOIN dungeons d ON d.id=v.dungeon_id
          WHERE d.name=? AND v.path IS NOT NULL ORDER BY v.id""", (dungeon,))]
    results = []
    for index, video in enumerate(videos, 1):
        job_update(job_id, f"Analyzing video {index} of {len(videos)}: {video['title']}", index, len(videos))
        results.append(analyze_video(video["id"], interval))
    job_update(job_id, "Comparing routes across videos", len(videos), len(videos))
    uncertainty = classify_uncertain_rooms(dungeon)
    layout = auto_layout(dungeon)
    job_update(job_id, "Map ready for review", len(videos), len(videos))
    return {"playlist": imported, "videos_analyzed": len(videos), "analysis": results,
            "layout": layout, **uncertainty}


def start_mapping(dungeon: str, interval: float = 5.0) -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "kind": "map-dungeon", "status": "queued", "message": "Preparing", "created": time.time()}
    def runner():
        with JOBS_LOCK: JOBS[job_id]["status"] = "running"
        try:
            result = map_entire_dungeon(job_id, dungeon, interval)
            with JOBS_LOCK: JOBS[job_id].update(status="complete", message="Map ready for review", result=result, finished=time.time())
        except Exception as exc:
            with JOBS_LOCK: JOBS[job_id].update(status="failed", message="Mapping stopped", error=str(exc), finished=time.time())
    threading.Thread(target=runner, daemon=True).start()
    return job_id


def merge_rooms(source_id: int, target_id: int) -> None:
    if source_id == target_id: return
    with db() as c:
        source = c.execute("SELECT dungeon_id FROM rooms WHERE id=?", (source_id,)).fetchone()
        target = c.execute("SELECT dungeon_id FROM rooms WHERE id=?", (target_id,)).fetchone()
        if not source or not target or source[0] != target[0]: raise ValueError("Rooms must belong to the same dungeon")
        c.execute("UPDATE evidence SET room_id=? WHERE room_id=?", (target_id, source_id))
        c.execute("UPDATE transitions SET from_room=? WHERE from_room=?", (target_id, source_id))
        c.execute("UPDATE transitions SET to_room=? WHERE to_room=?", (target_id, source_id))
        c.execute("DELETE FROM transitions WHERE from_room=to_room")
        c.execute("DELETE FROM rooms WHERE id=?", (source_id,))


def graph_data(dungeon: str) -> dict:
    with db() as c:
        d = c.execute("SELECT * FROM dungeons WHERE name=?", (dungeon,)).fetchone()
        if not d: raise ValueError("Unknown dungeon")
        rooms = [dict(r) for r in c.execute("SELECT * FROM rooms WHERE dungeon_id=? ORDER BY id", (d["id"],))]
        raw_edges = [dict(r) for r in c.execute("""SELECT t.from_room,t.to_room,a.label from_label,b.label to_label,
          COUNT(*) observations,COUNT(DISTINCT t.video_id) supporting_videos,
          SUM(CASE WHEN t.status='confirmed' THEN 1 ELSE 0 END) confirmed_observations,
          MIN(t.timestamp) first_timestamp
          FROM transitions t JOIN rooms a ON a.id=t.from_room JOIN rooms b ON b.id=t.to_room
          WHERE t.dungeon_id=? GROUP BY t.from_room,t.to_room ORDER BY supporting_videos DESC,observations DESC""", (d["id"],))]
        for edge in raw_edges:
            edge["confidence"] = round(min(.99, .35 + .18 * edge["supporting_videos"] + .04 * edge["observations"]), 2)
            edge["status"] = "confirmed" if edge["confirmed_observations"] else "candidate"
        evidence = [dict(r) for r in c.execute("""SELECT e.id,e.room_id,e.video_id,e.timestamp,e.frame_path,v.title video_title
          FROM evidence e JOIN videos v ON v.id=e.video_id WHERE v.dungeon_id=? ORDER BY e.timestamp""", (d["id"],))]
        evidence_by_room: dict[int, list] = {}
        for item in evidence: evidence_by_room.setdefault(item["room_id"], []).append(item)
        for room in rooms: room["evidence"] = evidence_by_room.get(room["id"], [])
        videos = [dict(r) for r in c.execute("SELECT * FROM videos WHERE dungeon_id=? ORDER BY id", (d["id"],))]
        return {"dungeon": dict(d), "rooms": rooms, "edges": raw_edges, "videos": videos}


def export_graph(dungeon: str, fmt: str = "json") -> Path:
    data = graph_data(dungeon)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", dungeon)
    if fmt == "json":
        out = EXPORT_DIR / f"{safe}.json"; out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    elif fmt == "dot":
        out = EXPORT_DIR / f"{safe}.dot"
        lines = ["digraph dungeon {", "  rankdir=LR;", f'  label="{dungeon}";']
        for r in data["rooms"]:
            style = "solid" if r["status"] == "confirmed" else "dashed"
            lines.append(f'  r{r["id"]} [label={json.dumps(r["label"])},style={style}];')
        for e in data["edges"]:
            label = f'{e["supporting_videos"]} video(s), {e["confidence"]:.0%}'
            lines.append(f'  r{e["from_room"]} -> r{e["to_room"]} [label={json.dumps(label)}];')
        lines.append("}"); out.write_text("\n".join(lines), encoding="utf-8")
    else:
        import csv
        out = EXPORT_DIR / f"{safe}.csv"
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f); writer.writerow(["from_room", "to_room", "supporting_videos", "observations", "confidence", "status"])
            for e in data["edges"]: writer.writerow([e["from_label"], e["to_label"], e["supporting_videos"], e["observations"], e["confidence"], e["status"]])
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(WEB), **kwargs)
    def log_message(self, fmt, *args): pass
    def send_json(self, obj, code=200):
        raw = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def body(self):
        size = int(self.headers.get("Content-Length", "0")); return json.loads(self.rfile.read(size) or b"{}")
    def serve_video(self, video_id: int):
        with db() as c: row = c.execute("SELECT path FROM videos WHERE id=?", (video_id,)).fetchone()
        if not row or not row[0] or not Path(row[0]).is_file(): return self.send_error(404)
        path = Path(row[0]); size = path.stat().st_size; start, end = 0, size - 1
        match = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if match:
            start = int(match.group(1)); end = int(match.group(2)) if match.group(2) else min(size - 1, start + 4 * 1024 * 1024)
        if start >= size or end < start: return self.send_error(416)
        code = 206 if match else 200
        self.send_response(code); self.send_header("Content-Type", "video/mp4"); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if match: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start); remaining = end - start + 1
            while remaining:
                chunk = f.read(min(65536, remaining))
                if not chunk: break
                self.wfile.write(chunk); remaining -= len(chunk)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/dungeons":
            with db() as c: return self.send_json([dict(x) for x in c.execute("SELECT * FROM dungeons ORDER BY id")])
        if u.path == "/api/graph":
            try: return self.send_json(graph_data(urllib.parse.parse_qs(u.query).get("dungeon", [""])[0]))
            except Exception as e: return self.send_json({"error": str(e)}, 400)
        if u.path == "/api/jobs":
            with JOBS_LOCK: return self.send_json(list(JOBS.values()))
        if u.path == "/api/video":
            try: return self.serve_video(int(urllib.parse.parse_qs(u.query).get("id", ["0"])[0]))
            except Exception as e: return self.send_json({"error": str(e)}, 400)
        if u.path.startswith("/media/"):
            p = (ROOT / urllib.parse.unquote(u.path[1:])).resolve()
            if ROOT not in p.parents or not p.is_file(): return self.send_error(404)
            self.path = "/" + str(p.relative_to(ROOT)); self.directory = str(ROOT); return super().do_GET()
        return super().do_GET()
    def do_POST(self):
        try:
            b = self.body()
            if self.path == "/api/local-video": result = {"video_id": add_local_video(b["dungeon"], b["path"])}
            elif self.path == "/api/map-dungeon":
                result = {"job_id": start_mapping(b["dungeon"], float(b.get("interval", 5)))}
            elif self.path == "/api/auto-layout": result = auto_layout(b["dungeon"])
            elif self.path == "/api/import": result = import_playlist(b["dungeon"], bool(b.get("download")))
            elif self.path == "/api/analyze":
                result = {"job_id": start_job("analyze", analyze_video, int(b["video_id"]), float(b.get("interval", 5)))}
            elif self.path == "/api/review":
                with db() as c:
                    fields=[]; vals=[]
                    for key in ("label", "status", "x", "y", "floor", "kind", "notes"):
                        if key in b: fields.append(f"{key}=?"); vals.append(b[key])
                    if not fields: raise ValueError("No room fields supplied")
                    vals.append(int(b["room_id"])); c.execute(f"UPDATE rooms SET {','.join(fields)} WHERE id=?", vals)
                result = {"ok": True}
            elif self.path == "/api/merge":
                merge_rooms(int(b["source_id"]), int(b["target_id"])); result = {"ok": True}
            elif self.path == "/api/transition":
                with db() as c:
                    c.execute("UPDATE transitions SET status=? WHERE dungeon_id=(SELECT id FROM dungeons WHERE name=?) AND from_room=? AND to_room=?",
                              (b["status"], b["dungeon"], int(b["from_room"]), int(b["to_room"])))
                result = {"ok": True}
            elif self.path == "/api/export": result = {"path": str(export_graph(b["dungeon"], b.get("format", "json")))}
            else: return self.send_json({"error": "Not found"}, 404)
            self.send_json(result)
        except Exception as e: self.send_json({"error": str(e)}, 400)


def serve(port: int = 8765):
    init_db(); print(f"OSFR Dungeon Mapper: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8765)
    a = sub.add_parser("analyze"); a.add_argument("video"); a.add_argument("--dungeon", required=True); a.add_argument("--interval", type=float, default=5)
    e = sub.add_parser("export"); e.add_argument("--dungeon", required=True); e.add_argument("--format", choices=("json", "dot", "csv"), default="json")
    args = p.parse_args(); init_db()
    if args.cmd == "serve": serve(args.port)
    elif args.cmd == "analyze": print(json.dumps(analyze_video(add_local_video(args.dungeon, args.video), args.interval), indent=2))
    else: print(export_graph(args.dungeon, args.format))


if __name__ == "__main__": main()
