# OSFR Dungeon Mapper

An evidence-first tool for reconstructing Free Realms dungeons from archived gameplay.

[![Tests](https://github.com/MentorKenner37/OSFR-Dungeon-Mapper/actions/workflows/tests.yml/badge.svg)](https://github.com/MentorKenner37/OSFR-Dungeon-Mapper/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

The mapper deliberately produces an evidence-backed **room graph**, not an invented geometric floor plan. Version 0.3 can download and process a complete dungeon playlist, compare routes across videos, and automatically arrange the resulting route and branches.

## Highlights

- Multi-signal visual fingerprints instead of single-hash matching
- Background video analysis with recoverable job status
- Cross-video consensus and connection confidence scores
- Embedded video player with evidence thumbnails and timestamp seeking
- Automatic route-and-branch layout with draggable corrections and floor filtering
- Room types, notes, confirmation, rejection, and duplicate merging
- JSON, Graphviz DOT, and CSV research exports

## Quick start (Linux)

Requirements: Python 3.10+, `ffmpeg`, and optionally `yt-dlp` for importing YouTube playlists.

```bash
sudo apt install ffmpeg
python3 -m pip install --user yt-dlp
./run.sh
```

Open <http://127.0.0.1:8765>.

## One-click mapping

1. Choose a dungeon.
2. Click **START MAPPING THIS DUNGEON**.
3. Leave the mapper open while it downloads and analyzes the playlist.
4. Review only uncertain or incorrectly matched evidence.
5. Export the finished map.

The automatic pipeline processes every available video sequentially, compares routes, hides weak one-off detections without deleting their evidence, infers a likely entrance, and lays out the strongest route with branches. The result is an evidence graph, not a claim about exact in-game distances.

## Workflow

1. Select one of the 28 preloaded dungeon playlists.
2. Start automatic mapping.
3. Click candidate rooms to compare screenshots and jump to exact video timestamps.
4. Correct any uncertain matches.
5. Export the reviewed evidence graph for the OSFR team.

All state is local in `data/mapper.db`. Downloaded videos and sampled frames stay under `data/` and are ignored by Git.

## Privacy and copyright

The program runs locally and does not upload footage or mapping data. Only download videos you are permitted to use, and follow YouTube's terms and applicable copyright rules.

## Reliability model

- Every proposed room and connection retains its source video and timestamp.
- Automatic matches remain marked `candidate` until reviewed.
- Similar appearance is treated as evidence, never proof.
- The export distinguishes confirmed, candidate, and rejected data.
- Connection confidence increases when independent videos show the same transition.
- Human confirmation remains mandatory; visual similarity is never silently presented as fact.

## Keyboard-friendly review

The embedded reviewer includes one- and five-second seek controls. Evidence thumbnails open their source video at the recorded timestamp, avoiding hours of manual playlist scrubbing.

## Command-line analysis

```bash
python3 mapper.py analyze /absolute/path/to/video.mp4 --dungeon "The Bat Cave" --interval 5
python3 mapper.py export --dungeon "The Bat Cave" --format json
```

## License

Released under the [MIT License](LICENSE). This is an independent community tool and is not affiliated with Daybreak Game Company.
