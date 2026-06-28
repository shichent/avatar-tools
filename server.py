#!/usr/bin/env python3
"""
Avatar Tools local server — stdlib only, no pip installs.

  * Serves the avatar-tools static files (generate.html, chroma-key.html, ...).
  * Reads ONLY OPENAI_API_KEY and RUNWAY_API_KEY from ../.env (verse/.env) —
    never serves or returns the .env file or any other secret.
  * Proxies image generation to OpenAI OR Runway (keys stay server-side, no
    browser CORS). The provider is chosen per-request from the browser payload.
  * Reports the actual cost of each generation (OpenAI: $ from token usage;
    Runway: credits from its published pricing table).
  * Auto-archives every successful generation to assets/_generated/ and supports
    an explicit "Save as" copy.

Run:   python server.py            (then open http://localhost:8000/generate.html)
"""
import os, sys, json, time, base64, uuid, mimetypes, posixpath
import urllib.request, urllib.error, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))          # avatar-tools/
ENV_PATH      = os.path.normpath(os.path.join(BASE_DIR, "..", ".env"))  # verse/.env
ASSETS_DIR    = os.path.join(BASE_DIR, "assets")
GENERATED_DIR = os.path.join(ASSETS_DIR, "_generated")
PORT          = int(os.environ.get("PORT", "8000"))
OPENAI_BASE    = "https://api.openai.com/v1/images"
RUNWAY_BASE    = "https://api.dev.runwayml.com"
RUNWAY_VERSION = "2024-11-06"

# Whitelisted save destinations under assets/. "" = assets/ root (for base_*.png).
SAVE_CATEGORIES = {"", "_generated", "base", "hair", "top", "bottom", "shoes"}

def load_env_keys(names):
    """Pull ONLY the named keys out of verse/.env. Nothing else is read or exposed."""
    wanted, found = set(names), {}
    if not os.path.exists(ENV_PATH):
        return found
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in wanted:
                found[k] = v.strip().strip('"').strip("'")
    return found

_KEYS          = load_env_keys(["OPENAI_API_KEY", "RUNWAY_API_KEY"])
OPENAI_API_KEY = _KEYS.get("OPENAI_API_KEY")
RUNWAY_API_KEY = _KEYS.get("RUNWAY_API_KEY")


def build_multipart(fields, files):
    """fields: {name: str}. files: {name: (filename, bytes, content_type)}."""
    boundary = "----avatartools" + uuid.uuid4().hex
    parts = []
    for name, val in fields.items():
        if val is None:
            continue
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode())
        parts.append((str(val) + "\r\n").encode())
    for name, (filename, content, ctype) in files.items():
        if content is None:
            continue
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (name, filename)).encode())
        parts.append(("Content-Type: %s\r\n\r\n" % ctype).encode())
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(("--" + boundary + "--\r\n").encode())
    return boundary, b"".join(parts)


# OpenAI gpt-image token rates ($ per 1M tokens): text-in, image-in, image-out.
OPENAI_RATES = {"text_in": 5.0, "image_in": 8.0, "image_out": 30.0}

def openai_cost(usage, had_input_image):
    """Compute $ cost from a gpt-image `usage` object. Returns a display string."""
    if not usage:
        return "cost n/a"
    det       = usage.get("input_tokens_details") or {}
    text_tok  = det.get("text_tokens")
    image_tok = det.get("image_tokens")
    if text_tok is None and image_tok is None:        # no breakdown — best effort split
        in_tok    = usage.get("input_tokens", 0)
        image_tok = in_tok if had_input_image else 0
        text_tok  = 0 if had_input_image else in_tok
    text_tok, image_tok = text_tok or 0, image_tok or 0
    out_tok   = usage.get("output_tokens", 0)
    dollars   = (text_tok  * OPENAI_RATES["text_in"]
               + image_tok * OPENAI_RATES["image_in"]
               + out_tok   * OPENAI_RATES["image_out"]) / 1_000_000
    return "$%.4f  (%d txt + %d img in, %d out tok)" % (dollars, text_tok, image_tok, out_tok)


def call_openai(payload):
    """payload comes from the browser. Returns (b64_png, cost_str, error_str)."""
    if not OPENAI_API_KEY:
        return None, None, "OPENAI_API_KEY not found in %s" % ENV_PATH

    model      = payload.get("model") or "gpt-image-2"
    prompt     = payload.get("prompt") or ""
    size       = payload.get("size") or "1024x1536"
    quality    = payload.get("quality") or "high"
    n          = str(payload.get("n") or 1)
    background = payload.get("background") or "opaque"
    image_b64  = payload.get("image_b64")
    mask_b64   = payload.get("mask_b64")

    headers = {"Authorization": "Bearer " + OPENAI_API_KEY}

    try:
        if image_b64:   # ---- edits (dress-on-body / boy edit) ----
            fields = {"model": model, "prompt": prompt, "size": size,
                      "quality": quality, "n": n, "background": background}
            files = {"image": ("image.png", base64.b64decode(image_b64), "image/png")}
            if mask_b64:
                files["mask"] = ("mask.png", base64.b64decode(mask_b64), "image/png")
            boundary, body = build_multipart(fields, files)
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
            req = urllib.request.Request(OPENAI_BASE + "/edits", data=body, headers=headers, method="POST")
        else:           # ---- generations (girl base) ----
            data = {"model": model, "prompt": prompt, "size": size,
                    "quality": quality, "n": int(n), "background": background}
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
            req = urllib.request.Request(OPENAI_BASE + "/generations", data=body, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=300) as resp:
            out = json.loads(resp.read().decode())
        return out["data"][0]["b64_json"], openai_cost(out.get("usage"), bool(image_b64)), None

    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
            msg = detail.get("error", {}).get("message", str(detail))
        except Exception:
            msg = "HTTP %s" % e.code
        return None, None, "OpenAI error: " + msg
    except Exception as e:
        return None, None, "Request failed: " + str(e)


# ---- Runway pricing (credits per image) by resolution tier × quality ----
RUNWAY_PRICE = {
    "1k": {"low": 1, "medium": 5,  "high": 20, "auto": 20},
    "2k": {"low": 1, "medium": 5,  "high": 20, "auto": 20},
    "4k": {"low": 2, "medium": 11, "high": 41, "auto": 41},
}

def runway_ratio(size):
    """OpenAI-style 'WxH' (or 'auto') -> Runway ratio 'W:H' (or 'auto')."""
    size = (size or "").strip().lower()
    if size in ("", "auto"):
        return "auto"
    return size.replace("x", ":")

def runway_tier(ratio):
    """Map a ratio to a billing tier. 'auto' bills at 4K; 1K and 2K cost the same."""
    if ratio == "auto":
        return "4k"
    try:
        w, h = (int(x) for x in ratio.split(":"))
    except Exception:
        return "1k"
    m = max(w, h)
    if m >= 3072:
        return "4k"
    if m >= 1792:
        return "2k"
    return "1k"

def runway_cost(tier, quality, output_count):
    """Exact billed credits = table[tier][quality] × outputCount (Runway's own formula)."""
    credits = RUNWAY_PRICE.get(tier, RUNWAY_PRICE["1k"]).get(quality, RUNWAY_PRICE["1k"]["high"])
    credits *= max(1, output_count)
    return "%d credits  (%s/%s)" % (credits, tier.upper(), quality)


def call_runway(payload):
    """Route a gpt-image generation through Runway's async text_to_image endpoint.

    Submits the task, polls until it finishes, downloads the resulting image and
    base64-encodes it, so the browser sees the SAME contract as the OpenAI path.
    Returns (b64_png, cost_str, error_str).
    """
    if not RUNWAY_API_KEY:
        return None, None, "RUNWAY_API_KEY not found in %s" % ENV_PATH

    image_b64 = payload.get("image_b64")
    if payload.get("mask_b64"):
        return None, None, ("Runway gpt_image_2 has no mask support — "
                            "remove the mask or switch to OpenAI.")
    if (payload.get("background") or "").strip().lower() == "transparent":
        return None, None, ("Runway gpt_image_2 cannot produce transparent backgrounds — "
                            "choose opaque/auto or switch to OpenAI.")

    model   = (payload.get("model") or "gpt-image-2").replace("-", "_")   # gpt-image-2 -> gpt_image_2
    prompt  = payload.get("prompt") or ""
    ratio   = runway_ratio(payload.get("size"))
    quality = (payload.get("quality") or "high").strip().lower()
    if quality not in ("low", "medium", "high", "auto"):
        quality = "high"

    body = {
        "model": model,
        "promptText": prompt,
        "ratio": ratio,
        "quality": quality,
        "outputCount": 1,            # server only returns the first image; never multiply cost
    }
    if image_b64:                    # edits: pass the attached image as a tagged reference
        body["promptText"]     = prompt if "@base" in prompt else ("@base " + prompt)
        body["referenceImages"] = [{"uri": "data:image/png;base64," + image_b64, "tag": "base"}]

    auth = {"Authorization": "Bearer " + RUNWAY_API_KEY, "X-Runway-Version": RUNWAY_VERSION}

    try:
        req = urllib.request.Request(
            RUNWAY_BASE + "/v1/text_to_image",
            data=json.dumps(body).encode(),
            headers=dict(auth, **{"Content-Type": "application/json"}),
            method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            task = json.loads(resp.read().decode())
        task_id = task.get("id")
        if not task_id:
            return None, None, "Runway: no task id in response"

        # ---- poll the task until it finishes (hidden from the browser) ----
        poll_url = RUNWAY_BASE + "/v1/tasks/" + urllib.parse.quote(task_id)
        deadline, out = time.time() + 300, None
        while time.time() < deadline:
            time.sleep(2)
            preq = urllib.request.Request(poll_url, headers=auth, method="GET")
            with urllib.request.urlopen(preq, timeout=60) as presp:
                out = json.loads(presp.read().decode())
            status = out.get("status")
            if status == "SUCCEEDED":
                break
            if status == "FAILED":
                return None, None, "Runway task failed: " + str(out.get("error") or out.get("failure") or "unknown")
        else:
            return None, None, "Runway task timed out after 300s"

        outputs = out.get("output") or []
        if not outputs:
            return None, None, "Runway: task succeeded but returned no output URL"

        # ---- download the signed URL and base64-encode (matches OpenAI's b64 contract) ----
        dreq = urllib.request.Request(outputs[0], method="GET")
        with urllib.request.urlopen(dreq, timeout=120) as dresp:
            b64 = base64.b64encode(dresp.read()).decode()

        return b64, runway_cost(runway_tier(ratio), quality, 1), None

    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
            msg = detail.get("error") or detail.get("message") or str(detail)
        except Exception:
            msg = "HTTP %s" % e.code
        return None, None, "Runway error: " + str(msg)
    except Exception as e:
        return None, None, "Request failed: " + str(e)


def decode_png_b64(b64):
    """Tolerant base64 → bytes: strips a data-URL prefix, whitespace, and fixes padding."""
    if not b64:
        raise ValueError("empty image data")
    if "," in b64 and b64.strip().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    b64 = "".join(b64.split())
    b64 += "=" * (-len(b64) % 4)
    return base64.b64decode(b64)


def safe_name(name, default="output.png"):
    name = os.path.basename(name or default).strip()
    if not name:
        name = default
    if not name.lower().endswith(".png"):
        name += ".png"
    return name


def scan_bases():
    """List assets/base/ into {body:{skin:{green,cutout}}}.

    Filename scheme: base_<body>[_<skin>][_green].png.
    Unsuffixed (no skin token) == the 'default' tone; '_green' marks the
    green-screen source, otherwise the file is a transparent cutout.
    """
    grid = {"girl": {}, "boy": {}}
    base_dir = os.path.join(ASSETS_DIR, "base")
    try:
        names = os.listdir(base_dir)
    except OSError:
        return grid
    for name in names:
        if not (name.startswith("base_") and name.lower().endswith(".png")):
            continue
        stem = name[len("base_"):-len(".png")]
        kind = "cutout"
        if stem.endswith("_green"):
            kind = "green"; stem = stem[:-len("_green")]
        parts = stem.split("_", 1)
        body = parts[0]
        if body not in grid:
            continue
        skin = parts[1] if len(parts) > 1 and parts[1] else "default"
        grid[body].setdefault(skin, {"green": False, "cutout": False})[kind] = True
    return grid


def resolve_save_dir(category):
    """Map a whitelisted category to a directory under assets/, path-checked."""
    category = (category or "").strip().strip("/")
    if category not in SAVE_CATEGORIES:
        raise ValueError("invalid category: %r" % category)
    target = os.path.normpath(os.path.join(ASSETS_DIR, category)) if category else ASSETS_DIR
    if not (target == ASSETS_DIR or target.startswith(ASSETS_DIR + os.sep)):
        raise ValueError("category escapes assets/")
    return target


class Handler(BaseHTTPRequestHandler):
    server_version = "AvatarTools/1.0"

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---- static files ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/bases":
            self._json(200, {"ok": True, "bases": scan_bases()}); return
        if path == "/":
            path = "/index.html"
        rel = posixpath.normpath(path).lstrip("/")
        if os.path.basename(rel) == ".env" or rel.startswith(".."):
            self.send_error(403, "Forbidden"); return
        full = os.path.normpath(os.path.join(BASE_DIR, *rel.split("/")))
        if not full.startswith(BASE_DIR) or not os.path.isfile(full):
            self.send_error(404, "Not found"); return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- api ----
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode())
        except Exception:
            self._json(400, {"ok": False, "error": "invalid JSON"}); return

        try:
            if path == "/api/generate":
                provider = (payload.get("provider") or "openai").strip().lower()
                if provider == "runway":
                    b64, cost, err = call_runway(payload)
                else:
                    b64, cost, err = call_openai(payload)
                if err:
                    self._json(502, {"ok": False, "error": err}); return
                png = decode_png_b64(b64)                       # validate before touching disk
                os.makedirs(GENERATED_DIR, exist_ok=True)
                stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4] + ".png"
                with open(os.path.join(GENERATED_DIR, stamp), "wb") as f:
                    f.write(png)
                self._json(200, {"ok": True, "b64": b64, "cost": cost,
                                 "archivedAs": "assets/_generated/" + stamp})

            elif path == "/api/save":
                png = decode_png_b64(payload.get("b64"))         # validate before touching disk
                save_dir = resolve_save_dir(payload.get("category"))
                name = safe_name(payload.get("filename"))
                dest = os.path.join(save_dir, name)
                rel = os.path.relpath(dest, BASE_DIR).replace(os.sep, "/")
                if os.path.exists(dest) and not payload.get("overwrite"):
                    self._json(200, {"ok": False, "exists": True, "savedAs": rel}); return
                os.makedirs(save_dir, exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(png)
                self._json(200, {"ok": True, "savedAs": rel})

            else:
                self._json(404, {"ok": False, "error": "unknown endpoint"})
        except Exception as e:
            self._json(400, {"ok": False, "error": str(e)})


def main():
    os.makedirs(GENERATED_DIR, exist_ok=True)
    print("=" * 60)
    print(" Avatar Tools server")
    print("  serving : %s" % BASE_DIR)
    print("  .env    : %s" % ENV_PATH)
    print("  openai  : %s" % ("OPENAI_API_KEY loaded" if OPENAI_API_KEY else "NOT FOUND"))
    print("  runway  : %s" % ("RUNWAY_API_KEY loaded" if RUNWAY_API_KEY else "NOT FOUND"))
    print("  open    : http://localhost:%d/" % PORT)
    print("  (Ctrl+C to stop)")
    print("=" * 60)
    # bind localhost only — never expose the proxy/key to the network
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
