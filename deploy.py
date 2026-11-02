"""Деплой новой версии функции tracker-support-unique-priority-system (d4eio0bqlv9h1rvnrbna).
createVersion: python311, index.handler, 1024 MB, 180 s, env TOKEN(OAuth)/ORG, content=zip(base64)."""
import base64, io, json, os, sys, time, zipfile
import helpers as h

FID = h.secrets()["function_id"]

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(os.path.join(os.path.dirname(__file__), "src", "index.py"), "index.py")
content_b64 = base64.b64encode(buf.getvalue()).decode()

body = {
    "functionId": FID,
    "runtime": "python311",
    "entrypoint": "index.handler",
    "resources": {"memory": "1073741824"},
    "executionTimeout": "180s",
    "environment": {"TOKEN": h.secrets()["oauth"], "ORG": h.secrets()["org"]},
    "content": content_b64,
}
s, b = h.yc("POST", "/functions/v1/versions", body=body)
print("createVersion:", s)
if s != 200:
    print(json.dumps(b, ensure_ascii=False)[:800]); sys.exit(1)

op_id = b.get("id")
print("operation:", op_id)
for _ in range(60):
    s2, op = h.op(op_id)
    if op.get("done"):
        if "error" in op:
            print("ОШИБКА:", json.dumps(op["error"], ensure_ascii=False)); sys.exit(1)
        resp = op.get("response", {})
        meta = op.get("metadata", {})
        print("OK. versionId:", resp.get("id"), "| functionId:", meta.get("functionId"),
              "| createdAt:", resp.get("createdAt"))
        sys.exit(0)
    time.sleep(2)
print("timeout операции"); sys.exit(1)
