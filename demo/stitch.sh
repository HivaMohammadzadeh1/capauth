#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required; install it before running stitch.sh" >&2
  exit 1
fi

# All segments use identical video settings and no audio, for lossless concat.
encode=(-an -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p -r 30 -video_track_timescale 15360 -movflags +faststart)
# Custom sequence: stitch.sh --out BASENAME card.png:3 clip.webm ...
if [[ "${1:-}" == "--out" ]]; then
  out="${2:?output basename required}"
  [[ "$out" != */* && "$out" != "." && "$out" != ".." ]] || exit 2
  shift 2
  [[ "$#" -gt 0 ]] || exit 2
  manifest="${out}-concat.txt"
  : > "$manifest"
  index=0
  for segment in "$@"; do
    index=$((index + 1))
    target="${out}-segment-${index}.mp4"
    if [[ "$segment" == *.png:* ]]; then
      source="${segment%:*}"
      duration="${segment##*:}"
      ffmpeg -hide_banner -loglevel warning -y -loop 1 -framerate 30 -i "$source" \
        -t "$duration" -vf "scale=1440:900,setsar=1" "${encode[@]}" "$target"
    else
      ffmpeg -hide_banner -loglevel warning -y -i "$segment" \
        -vf "scale=1440:900:force_original_aspect_ratio=decrease,pad=1440:900:(ow-iw)/2:(oh-ih)/2:color=0x0B1017,setsar=1,fps=30" \
        "${encode[@]}" "$target"
    fi
    printf "file '%s'\n" "$target" >> "$manifest"
  done
  ffmpeg -hide_banner -loglevel warning -y -f concat -safe 0 -i "$manifest" \
    -c copy -movflags +faststart "${out}.mp4"
  ffmpeg -hide_banner -loglevel error -y -i "${out}.mp4" \
    -filter_complex '[0:v]fps=12,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=single[p];[b][p]paletteuse=new=1:dither=bayer:bayer_scale=3' \
    -loop 0 "${out}.gif"
  for file in "${out}.mp4" "${out}.gif"; do
    test -s "$file"
    printf '%s: %s bytes\n' "$file" "$(wc -c < "$file" | tr -d ' ')"
  done
  exit 0
fi

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
