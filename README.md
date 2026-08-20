# OSFR Dungeon Mapper

An evidence-first tool for reconstructing Free Realms dungeons from archived gameplay.

[![Tests](https://github.com/MentorKenner37/OSFR-Dungeon-Mapper/actions/workflows/tests.yml/badge.svg)](https://github.com/MentorKenner37/OSFR-Dungeon-Mapper/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

The mapper deliberately produces a **room graph**, not an invented geometric floor plan. It samples videos, groups visually similar scenes into candidate rooms, records transitions with timestamps, and gives a reviewer a fast way to confirm, rename, merge, or reject the results.

## Quick start (Linux)

Requirements: Python 3.10+, `ffmpeg`, and optionally `yt-dlp` for importing YouTube playlists.

```bash
sudo apt install ffmpeg
python3 -m pip install --user yt-dlp
./run.sh
```

Open <http://127.0.0.1:8765>.

## Workflow

1. Select one of the 28 preloaded dungeon playlists.
2. Import playlist metadata and optionally download videos, or add an existing local video.
3. Analyze a video. The default sample interval is five seconds.
4. Review candidate rooms and automatically observed transitions.
5. Rename confirmed rooms, reject junk/loading screens, and export the evidence-backed graph.

All state is local in `data/mapper.db`. Downloaded videos and sampled frames stay under `data/` and are ignored by Git.

## Privacy and copyright

The program runs locally and does not upload footage or mapping data. Only download videos you are permitted to use, and follow YouTube's terms and applicable copyright rules.

## Reliability model

- Every proposed room and connection retains its source video and timestamp.
- Automatic matches remain marked `candidate` until reviewed.
- Similar appearance is treated as evidence, never proof.
- The export distinguishes confirmed, candidate, and rejected data.

## Command-line analysis

```bash
python3 mapper.py analyze /absolute/path/to/video.mp4 --dungeon "The Bat Cave" --interval 5
python3 mapper.py export --dungeon "The Bat Cave" --format json
```

## License

Released under the [MIT License](LICENSE). This is an independent community tool and is not affiliated with Daybreak Game Company.
