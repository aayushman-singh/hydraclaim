#!/usr/bin/env bash
# Rebuild the demo video from live output. Reproducible and Docker-aware.
#
#   bash demo/build-video.sh
#
# Requires: ffmpeg, docker (for the live HydraDB node), Python + PIL.
# Set LLM_API_KEY (e.g. a local llama.cpp server) to include the live
# extraction/evaluation section; without it that section is skipped.
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. Regenerate the title/info cards (intro, schema, benchmark, outro).
python demo/gen-cards.py

# 2. Turn each card PNG into a looping 1920x1080@30 h264 clip.
card_to_video() {
  local png=$1 dur=$2 out=$3
  ffmpeg -y -loop 1 -i "demo/$png" -t "$dur" -r 30 \
    -vf "scale=1920:1080" -pix_fmt yuv420p -preset fast "demo/$out" \
    -loglevel error
}
card_to_video intro.png     5  intro.mp4
card_to_video schema.png   10  schema.mp4
card_to_video benchmark.png 10 benchmark.mp4
card_to_video outro.png     5  outro.mp4

# 3. Capture the live demo and the benchmark text output (UTF-8).
bash scripts/demo.sh > demo/demo-output.txt 2>&1
python -m trustgraph.benchmark data/sessions/*.json --arm all \
  > demo/bench-output.txt 2>&1

# 4. Render the text captures as scrolling terminal clips.
python demo/term-video.py demo/demo-output.txt demo/demo-terminal.mp4 \
  --duration 52 --fps 30
python demo/term-video.py demo/bench-output.txt demo/bench-terminal.mp4 \
  --duration 13 --fps 30

# 5. Concatenate: intro -> schema -> demo terminal -> benchmark -> outro.
# Use absolute Windows paths: ffmpeg resolves concat entries relative to the
# concat file's directory and Git Bash's /c/ paths are foreign to it.
ROOT="$(pwd -P | cygpath -w -m -f -)"
printf '%s\n' \
  "file '$ROOT/demo/intro.mp4'" \
  "file '$ROOT/demo/schema.mp4'" \
  "file '$ROOT/demo/demo-terminal.mp4'" \
  "file '$ROOT/demo/benchmark.mp4'" \
  "file '$ROOT/demo/bench-terminal.mp4'" \
  "file '$ROOT/demo/outro.mp4'" > demo/concat.txt
ffmpeg -y -f concat -safe 0 -i demo/concat.txt \
  -c copy demo/trustgraph-demo.mp4 -loglevel error
rm -f demo/concat.txt

ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 demo/trustgraph-demo.mp4
echo "Wrote demo/trustgraph-demo.mp4"