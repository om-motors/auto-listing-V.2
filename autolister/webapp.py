"""Lokale Upload-Website: Fotos vom Handy/Browser direkt in den Eingang legen.

    .venv/bin/python -m autolister.webapp

Danach im Browser (auch vom iPhone im selben WLAN):
    http://<mac-name>.local:8790
Der Watcher übernimmt die hochgeladenen Fotos automatisch.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from flask import Flask, redirect, request

from . import config

app = Flask("autolister")

PAGE = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Auto-Listing Upload</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 480px;
         margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.4rem; }
  input, button { font-size: 1.1rem; width: 100%%; box-sizing: border-box;
                  margin: .4rem 0; padding: .7rem; border-radius: .5rem;
                  border: 1px solid #bbb; }
  button { background: #0064d2; color: white; border: none; font-weight: 600; }
  .ok { background: #e6f4e6; border: 1px solid #7c7; padding: .8rem;
        border-radius: .5rem; }
  .hint { color: #666; font-size: .9rem; }
</style></head><body>
<h1>&#128247; Auto-Listing &mdash; Fotos hochladen</h1>
%s
<form method="post" action="/upload" enctype="multipart/form-data">
  <input type="text" name="name" placeholder="Name (optional, z.B. Teilenummer)">
  <input type="file" name="photos" accept="image/*" multiple required>
  <button type="submit">Hochladen &amp; Entwurf erstellen lassen</button>
</form>
<p class="hint">Ein Upload = ein Produkt. Die Pipeline erkennt Teilenummer,
recherchiert Preise und legt den eBay-Entwurf automatisch an. Ergebnis kommt
als Mac-Benachrichtigung + Bericht im Ordner &bdquo;Berichte&ldquo;.</p>
</body></html>"""


@app.get("/")
def index():
    msg = ""
    if request.args.get("ok"):
        msg = '<div class="ok">&#10003; %s Foto(s) hochgeladen — Verarbeitung startet gleich.</div>' % request.args["ok"]
    return PAGE % msg


@app.post("/upload")
def upload():
    config.ensure_dirs()
    files = [f for f in request.files.getlist("photos") if f.filename]
    if not files:
        return redirect("/")
    raw_name = (request.form.get("name") or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", raw_name) or time.strftime("upload_%Y%m%d_%H%M%S")
    target = config.EINGANG / safe
    target.mkdir(parents=True, exist_ok=True)
    for f in files:
        suffix = Path(f.filename).suffix.lower() or ".jpg"
        name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(f.filename).name)
        if not name or name.startswith("."):
            name = "foto_%d%s" % (int(time.time() * 1000), suffix)
        f.save(str(target / name))
    return redirect("/?ok=%d" % len(files))


def main() -> None:
    config.ensure_dirs()
    app.run(host="0.0.0.0", port=config.WEBAPP_PORT)


if __name__ == "__main__":
    main()
