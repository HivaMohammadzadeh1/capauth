#!/usr/bin/env bash
# Rebuild all three demo videos against the console at http://localhost:8000.
set -euo pipefail
cd "$(dirname "$0")/.."
R="uv run python demo/record.py"
T1='CapAuth. An AI organization: a manager agent delegates to a worker.'
T1B='Without CapAuth, a worker agent inherits everything its manager holds.'
T1L='With CapAuth, the manager hands the worker one capability for five minutes, and both leases share one audit trail. Live run, Claude Code.'
T2='CapAuth. A customer-facing agent with standing access.'
T2B='Before: the support agent holds the CRM, refunds and email tokens. A customer message tells it to export every customer record. Simulated agent.'
T2A='After: the same agent holds a lease for one conversation. Export and outbound email are denied; the refund waits for a person. Simulated agent.'
T3B='Before, simulated agent that follows the injection: it holds the user'"'"'s full tokens, so the export and the email run.'
T3A='After, the same simulated agent holds a task-scoped lease from CapAuth: the export and the email are denied.'
$R --title-only --out enterprise-2-ai-org --title "$T1"
$R --title-only --out enterprise-2-ai-org-before --title "$T1B"
$R --title-only --out enterprise-1-support --title "$T2"
$R --scenario ai-org --mode live --scope on --out enterprise-2-ai-org-live --title "$T1L" --record-only
$R --scenario support --mode replay --recording support-off-simulated --scope off --out enterprise-1-support-before --title "$T2B" --record-only
$R --scenario support --mode replay --recording support-on-simulated --scope on --out enterprise-1-support-after --title "$T2A" --record-only
$R --scenario file-issue --mode replay --recording file-issue-off-simulated --scope off --out before --title "$T3B" --record-only
$R --scenario file-issue --mode replay --recording file-issue-on-simulated --scope on --out after --title "$T3A" --record-only
cd demo
bash stitch.sh --out enterprise-2-ai-org enterprise-2-ai-org-title.png:3 enterprise-2-ai-org-before-title.png:4 enterprise-2-ai-org-live-title.png:3 enterprise-2-ai-org-live.webm
bash stitch.sh --out enterprise-1-support enterprise-1-support-title.png:3 enterprise-1-support-before-title.png:3 enterprise-1-support-before.webm enterprise-1-support-after-title.png:3 enterprise-1-support-after.webm
bash stitch.sh --out before-after before-title.png:3 before.webm after-title.png:3 after.webm
for n in enterprise-1-support enterprise-2-ai-org before-after; do
  ffmpeg -hide_banner -loglevel error -y -i $n.mp4 -filter_complex "[0:v]fps=6,scale=760:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" -loop 0 $n-preview.gif
done
cp enterprise-1-support-preview.gif enterprise-2-ai-org-preview.gif ../docs/site/
ls -la enterprise-1-support.mp4 enterprise-2-ai-org.mp4 before-after.mp4 *-preview.gif | awk '{print $5, $9}'
echo REBUILD_DONE
