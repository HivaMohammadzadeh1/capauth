"""Build the hosted preview of the console: docs/app/, static, replay only.

The real console talks to server.py. The preview replaces every API read with a
JSON file and turns "Run task" into a replay of the recorded run for that scenario.
    uv run python scripts/build_static_app.py   (with server.py running on :8000)
"""
from __future__ import annotations

import json
import re
import shutil
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "app"
API = "http://localhost:8000/api"


def get(path: str):
    with urllib.request.urlopen(API + path, timeout=10) as r:
        return json.load(r)


def main() -> None:
    (OUT / "api" / "policies").mkdir(parents=True, exist_ok=True)
    (OUT / "api" / "recordings").mkdir(parents=True, exist_ok=True)
    scenarios = get("/scenarios")
    (OUT / "api" / "scenarios.json").write_text(json.dumps(scenarios))
    cfg = get("/config"); cfg["backend"] = "hosted"; cfg["hosted"] = True
    (OUT / "api" / "config.json").write_text(json.dumps(cfg))
    (OUT / "api" / "runs.json").write_text("[]")
    pols = get("/policies")
    (OUT / "api" / "policies.json").write_text(json.dumps(pols))
    for p in pols:
        (OUT / "api" / "policies" / f"{p['agent']}.json").write_text(json.dumps(get(f"/policies/{p['agent']}")))
    recs = get("/recordings")
    (OUT / "api" / "recordings.json").write_text(json.dumps(recs))
    for name in recs:
        (OUT / "api" / "recordings" / f"{name}.json").write_text(json.dumps(get(f"/recordings/{name}")))
    shutil.copy(ROOT / "ui" / "logo.svg", OUT / "logo.svg")

    html = (ROOT / "ui" / "index.html").read_text()
    html = html.replace('href="/ui/logo.svg"', 'href="logo.svg"').replace('src="/ui/logo.svg"', 'src="logo.svg"')
    banner = '''<div class="hosted" role="note">Hosted preview. Every run here is a recording of a real session. Live runs need the local server with Claude Code: <a href="https://github.com/HivaMohammadzadeh1/capauth">get the code</a>.</div>
<div class="main">'''
    html = html.replace('<div class="main">', banner, 1)
    html = html.replace("</style>", '''.hosted{background:#1B2430; color:#fff; font-size:13.5px; padding:9px 28px; text-align:center}
.hosted a{color:#9DB6F5}
.main{display:flex; flex-direction:column}
body{flex-direction:row}
</style>''', 1)
    shim_js = r'''<script>
// ---- hosted preview: every API read comes from a JSON file ----
(() => {
  const realFetch = window.fetch.bind(window);
  window.fetch = (url, opts) => {
    if (typeof url === "string" && url.startsWith("/api/")) {
      const rest = url.slice("/api/".length);
      if (opts && opts.method && opts.method !== "GET") return Promise.resolve(new Response("{}", {status: 405}));
      return realFetch("api/" + rest + ".json");
    }
    return realFetch(url, opts);
  };
})();
</script>
'''
    static_js = r'''
<script>
// ---- hosted preview: "Run task" replays the recording of a real run ----
(() => {
  const btn = document.getElementById("runBtn");
  const fresh = btn.cloneNode(true);
  btn.replaceWith(fresh);
  fresh.textContent = "Play recorded run";
  fresh.title = "Hosted preview: replays a recording of a real run for this scenario";
  fresh.addEventListener("click", async () => {
    const sel = document.getElementById("scenario");
    const on = document.getElementById("scopeToggle").checked;
    const want = sel.value + "-" + (on ? "on" : "off");
    let recs = [];
    try { recs = await (await fetch("/api/recordings")).json(); } catch (e) {}
    const name = recs.includes(want) ? want : recs.includes(want + "-simulated") ? want + "-simulated" : null;
    if (!name) { alert("No recording for this scenario yet."); return; }
    const ev = await (await fetch("/api/recordings/" + name)).json();
    playEvents(ev, name, {simulated: name.endsWith("-simulated")});
  });
  document.getElementById("exportBtn").title = "Audit export needs the local server";
  const un = document.querySelector(".user .un"); if (un) un.innerHTML = "preview<small>Read only</small>";
})();
</script>'''
    html = html.replace("<script>", shim_js + "<script>", 1)
    html = html.replace("</body>", static_js + "\n</body>", 1)
    (OUT / "index.html").write_text(html)
    print("built", OUT, "scenarios", len(scenarios), "recordings", len(recs))


if __name__ == "__main__":
    main()
