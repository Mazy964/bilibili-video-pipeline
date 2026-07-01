"""Health-check a Hermes provider by sending a minimal chat completion request.
Usage: python3 check-provider.py <provider-name>
Exits 0 if healthy, non-zero otherwise.
"""
import sys, json, ssl, urllib.request

NAME = sys.argv[1] if len(sys.argv) > 1 else "hermes-ds"

# Provider → chat endpoint mapping
ENDPOINTS = {
    "hermes-ds":   "https://api.deepseek.com/v1/chat/completions",
    "hermes-kimi": "https://api.kimi.com/coding/v1/chat/completions",
}
ENDPOINT = ENDPOINTS.get(NAME)
if not ENDPOINT:
    print(f"UNKNOWN: {NAME}")
    sys.exit(1)

# Parse config YAML (simple line-by-line — good enough for our flat structure)
config_path = "/Users/mazy/.hermes/config.yaml"
with open(config_path) as f:
    lines = f.readlines()

# Extract API key and model name for the given provider
key = None
model = None
in_target = False
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == f"- name: {NAME}":
        in_target = True
        # Scan the provider block (next ~10 lines)
        for j in range(i + 1, min(i + 15, len(lines))):
            s = lines[j].rstrip()
            if s.strip().startswith("- name:") and j > i + 1:
                break  # next provider block
            if "api_key:" in s:
                key = s.split("api_key:", 1)[1].strip()
            if "model:" in s and not s.strip().startswith("models:"):
                model = s.split("model:", 1)[1].strip()
        break

# Fallback: look for top-level model.default
if not model:
    in_model_section = False
    for line in lines:
        s = line.rstrip()
        if s == "model:":
            in_model_section = True
        elif in_model_section and "default:" in s:
            model = s.split("default:", 1)[1].strip()
            break
        elif in_model_section and s and not s.startswith(" "):
            in_model_section = False

if not key:
    print(f"NO_KEY: {NAME}")
    sys.exit(1)
if not model:
    print(f"NO_MODEL: {NAME}")
    sys.exit(1)

# Minimal chat request — 1 token in, 1 token out
payload = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": "."}],
    "max_tokens": 1,
    "stream": False,
}).encode()

req = urllib.request.Request(
    ENDPOINT,
    data=payload,
    headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    },
)

ctx = ssl.create_default_context()
try:
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        body = json.loads(resp.read().decode())
        msg = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        if msg is not None:
            print(f"OK: {NAME} (model={model}, status={resp.status})")
            sys.exit(0)
        else:
            print(f"UNHEALTHY: {NAME} — empty response body")
            sys.exit(1)
except urllib.error.HTTPError as e:
    body = e.read().decode()[:500]
    code = e.code
    if code == 429:
        print(f"RATE_LIMITED: {NAME} — 配额耗尽，拒绝切换")
    elif code == 401:
        print(f"AUTH_FAILED: {NAME} — API key 无效，拒绝切换")
    else:
        print(f"UNHEALTHY: {NAME} — HTTP {code}\n{body}")
    sys.exit(1)
except Exception as e:
    print(f"UNHEALTHY: {NAME} — {e}")
    sys.exit(1)
