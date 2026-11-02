"""Минимальный HTTP-хелпер (urllib) для API-запросов. Токены — из secrets.json (не коммитится)."""
import json, os, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))

def secrets():
    with open(os.path.join(ROOT, "secrets.json")) as f:
        return json.load(f)

def req(method, url, headers=None, body=None, timeout=120, raw=False):
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else _json(payload))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, payload.decode(errors="replace")[:2000]

def _json(p):
    try:
        return json.loads(p)
    except Exception:
        return p.decode(errors="replace")[:4000]

def tracker_headers(use="iam"):
    s = secrets()
    tok = s["iam"] if use == "iam" else s["oauth"]
    return {"Authorization": f"Bearer {tok}", "X-Cloud-Org-Id": s["org"]}

def yc_headers():
    return {"Authorization": f"Bearer {secrets()['iam']}"}

def tracker(method, path, use="iam", **kw):
    base = "https://api.tracker.yandex.net"
    return req(method, base + path, headers=tracker_headers(use), **kw)

def yc(method, path, **kw):
    return req(method, "https://serverless-functions.api.cloud.yandex.net" + path, headers=yc_headers(), **kw)

def op(operation_id):
    return req("GET", f"https://operation.api.cloud.yandex.net/operations/{operation_id}", headers=yc_headers())
