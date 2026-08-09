#!/usr/bin/env python3
"""
VISION MODULE - lets Hermes SEE the DST game.
1. capture(): screenshot the primary screen -> PNG
2. analyze(image, question): send to a vision LLM (OpenRouter qwen3-vl) -> text
3. see(question): capture + analyze in one call (main entry point)
"""
import os, sys, base64, json, subprocess, time, urllib.request

PROJ = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(PROJ, "vision_shot.png")
PS1 = os.path.join(PROJ, "shot.ps1")
MODEL = os.environ.get("VISION_MODEL", "qwen/qwen3-vl-8b-instruct")

def _get_key():
    env_path = os.path.expanduser(r"~\AppData\Local\hermes\.env")
    if os.path.exists(env_path):
        for l in open(env_path, encoding="utf-8"):
            if l.startswith("OPENROUTER_API_KEY"):
                return l.split("=", 1)[1].strip()
    return os.environ.get("OPENROUTER_API_KEY", "")

def capture(out_path=None):
    """Screenshot the primary screen. Returns path or None."""
    path = out_path or SHOT
    p = path.replace("\\", "/")
    ps = f'Add-Type -AssemblyName System.Windows.Forms\n' \
         f'Add-Type -AssemblyName System.Drawing\n' \
         f'$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds\n' \
         f'$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)\n' \
         f'$g = [System.Drawing.Graphics]::FromImage($bmp)\n' \
         f'$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)\n' \
         f'$bmp.Save("{p}")\n' \
         f'$g.Dispose(); $bmp.Dispose()'
    with open(PS1, "w") as f:
        f.write(ps)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS1],
                       capture_output=True, text=True, timeout=30)
    return path if os.path.exists(path) else None

def analyze(image_path, question, model=None, max_tokens=400, timeout=90):
    """Send image to vision LLM, return text description."""
    key = _get_key()
    if not key:
        return "ERROR: no OPENROUTER_API_KEY"
    b64 = base64.b64encode(open(image_path, "rb").read()).decode()
    payload = {
        "model": model or MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": question}
        ]}],
        "max_tokens": max_tokens
    }
    t0 = time.time()
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}",
                     "HTTP-Referer": "https://localhost/dst-bot",
                     "X-Title": "DST AI Bot"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode())
            content = resp["choices"][0]["message"]["content"]
            return f"[vision {time.time()-t0:.0f}s] " + content.strip()
    except Exception as e:
        return f"VISION ERROR ({time.time()-t0:.0f}s): {str(e)[:250]}"

def see(question, model=None):
    """Capture + analyze in one call. Main entry point."""
    path = capture()
    if not path:
        return "VISION ERROR: screenshot capture failed"
    return analyze(path, question, model=model)

if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Describe this screen briefly."
    print(see(q))
