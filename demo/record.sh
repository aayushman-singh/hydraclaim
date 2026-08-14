#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
rm -f demo/record-done.txt demo/live.mp4

# Start screen capture in the background.
ffmpeg -y -f gdigrab -framerate 30 -i desktop \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2" \
  -pix_fmt yuv420p demo/live.mp4 &
FFPID=$!

# Open a maximized terminal and run the demo + benchmark.
"/c/Windows/System32/cmd.exe" //c start //max cmd //c "C:\Repo\trustgraph\demo\run-demo-and-benchmark.bat"

# Wait until the batch signals completion.
while [ ! -f demo/record-done.txt ]; do
  sleep 1
done

# Give ffmpeg a moment to flush, then stop it gracefully.
sleep 2
kill -INT "$FFPID" || true
wait "$FFPID" || true

echo "Recorded: demo/live.mp4"
