#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  brew install ffmpeg
fi

# All segments use identical video settings and no audio, for lossless concat.
encode=(-an -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p -r 30 -video_track_timescale 15360 -movflags +faststart)
for name in before after; do
  ffmpeg -hide_banner -loglevel warning -y -i "$name.webm" \
    -vf "scale=1440:900:force_original_aspect_ratio=decrease,pad=1440:900:(ow-iw)/2:(oh-ih)/2:color=0x0B1017,setsar=1,fps=30" \
    "${encode[@]}" "$name.mp4"
  ffmpeg -hide_banner -loglevel warning -y -loop 1 -framerate 30 -i "$name-title.png" \
    -t 3 -vf "setsar=1" "${encode[@]}" "$name-title.mp4"
done

cat > concat.txt <<'EOF'
file 'before-title.mp4'
file 'before.mp4'
file 'after-title.mp4'
file 'after.mp4'
EOF
ffmpeg -hide_banner -loglevel warning -y -f concat -safe 0 -i concat.txt \
  -c copy -movflags +faststart before-after.mp4
# Per-frame palettes preserve the entire timeline on FFmpeg builds whose
# global-palette framesync drops the opening card or fails mid-stream.
ffmpeg -hide_banner -loglevel error -y -i before-after.mp4 \
  -filter_complex '[0:v]fps=12,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=single[p];[b][p]paletteuse=new=1:dither=bayer:bayer_scale=3' \
  -loop 0 before-after.gif

for name in before.webm after.webm before.png after.png before.mp4 after.mp4 before-after.mp4 before-after.gif; do
  test -s "$name"
  printf '%s: %s bytes\n' "$name" "$(wc -c < "$name" | tr -d ' ')"
done
