import urllib.request, ssl, json

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def login(uname, pw):
    body = json.dumps({"username": uname, "password": pw}).encode()
    req = urllib.request.Request(
        "https://127.0.0.1:5000/api/auth/login",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urllib.request.urlopen(req, context=ctx, timeout=15)
        d = json.loads(r.read().decode())
        role = d.get("user", {}).get("role")
        tok = d.get("access_token", "")
        print("LOGIN %s: %s -> role=%s token_len=%d" % (uname, r.status, role, len(tok)))
    except urllib.error.HTTPError as e:
        print("LOGIN %s: %s -> %s" % (uname, e.code, e.read().decode()[:200]))

cases = [
    ("admin@automan.com", "Admin123!"),
    ("staff@automan.com", "Staff123!"),
    ("mechanic@automan.com", "Mechanic123!"),
    ("customer@automan.com", "Customer123!"),
    ("admin@automan.com", "wrongpass"),
]
for u, p in cases:
    login(u, p)
