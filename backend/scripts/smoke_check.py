"""Post-deployment smoke check (Stage 13, stdlib only, no new dependencies).

Exercises a production-like backend with real HTTP: health/readiness,
unknown-route envelope + trace id, CORS preflight echo, and — when
credentials are given — login/session-cookie flags, an authenticated read,
marketplace shape, uniform 404 for a foreign business, and a wrong-password
401. Ends with logout so the smoke session is revoked server-side.

Usage (from anywhere; needs a RUNNING backend, never dev/test data):
    python backend/scripts/smoke_check.py --base-url http://127.0.0.1:8000
    python backend/scripts/smoke_check.py --base-url https://app.example.com \\
        --email op@example.com --password '...' --origin https://app.example.com

Exit codes: 0 all checks passed, 1 any check failed. Passwords are never
printed; failures print only status codes and safe envelope codes.
"""

import argparse
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request
import uuid

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--origin", default="http://127.0.0.1:3000")
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, data=None, headers=None, method=None):
        request_headers = {"Content-Type": "application/json", "X-Client-Contract": "1"}
        request_headers.update(headers or {})
        request = urllib.request.Request(
            base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=request_headers,
            method=method or ("POST" if data is not None else "GET"),
        )
        try:
            with opener.open(request) as response:
                raw = response.read()
                body = json.loads(raw) if raw else None
                return response.status, dict(response.headers), body
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = None
            return exc.code, dict(exc.headers), body

    # 1. Liveness (dependency-free by design).
    status, headers, body = call("/api/v1/health")
    check(
        "health",
        status == 200 and isinstance(body, dict) and body.get("status") == "ok",
        f"status={status}",
    )

    # 2. Readiness must be honest: 200 only with a live database.
    status, headers, body = call("/api/v1/readiness")
    ready = status == 200 and isinstance(body, dict) and body.get("status") == "ready"
    check("readiness", ready, f"status={status} body={body}")

    # 3. Unknown route: contract envelope + trace id, no internals.
    try:
        urllib.request.urlopen(base + "/api/v1/nope-here")
        check("unknown-route", False, "unexpected 200")
    except urllib.error.HTTPError as exc:
        try:
            envelope = json.loads(exc.read())
        except ValueError:
            envelope = None
        err = envelope.get("error", {}) if isinstance(envelope, dict) else {}
        check(
            "unknown-route",
            exc.code == 404
            and err.get("code") == "not_found"
            and bool(err.get("trace_id"))
            and exc.headers.get("X-Trace-Id") is not None,
            f"status={exc.code}",
        )

    # 4. CORS preflight echoes the configured origin (never foreign ones).
    preflight = urllib.request.Request(
        base + "/api/v1/auth/logout",
        headers={
            "Origin": args.origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
        method="OPTIONS",
    )
    with opener.open(preflight) as response:
        echo = response.headers.get("Access-Control-Allow-Origin")
    check("cors-preflight", echo == args.origin, f"echo={echo!r}")

    if not args.email:
        print("[skip] authenticated checks (no --email given)")
        return 0 if not FAILURES else 1

    if not args.password:
        print("error: --password is required with --email", file=sys.stderr)
        return 1

    # 5. Wrong password: uniform 401, no session cookie.
    jar.clear()
    status, _, body = call(
        "/api/v1/auth/login", {"email": args.email, "password": "wrong-" + args.password}
    )
    code = body.get("error", {}).get("code") if isinstance(body, dict) else None
    check("login-401", status == 401 and code == "unauthorized", f"status={status}")

    # 6. Login: 200 + HttpOnly session cookie (+ Secure on https).
    status, headers, body = call(
        "/api/v1/auth/login", {"email": args.email, "password": args.password}
    )
    csrf = body.get("csrf_token") if isinstance(body, dict) else None
    lowered = {k.lower(): v for k, v in headers.items()}
    set_cookie = lowered.get("set-cookie", "")
    secure_ok = ("Secure" in set_cookie) if base.startswith("https://") else True
    check(
        "login",
        status == 200
        and bool(csrf)
        and "HttpOnly" in set_cookie
        and secure_ok
        and isinstance(body.get("businesses"), list),
        f"status={status}",
    )
    if status != 200:
        return 1
    businesses = body["businesses"]
    authed = {"X-CSRF-Token": csrf}

    # 7. Authenticated read + contract echo.
    status, _, me = call("/api/v1/auth/me", headers=authed)
    check(
        "me",
        status == 200 and me.get("contract") == "v1" and isinstance(me.get("businesses"), list),
        f"status={status}",
    )

    # 8. Foreign business: uniform 404, never distinguished.
    foreign = "biz_" + uuid.uuid4().hex[:8]
    status, _, body = call(f"/api/v1/businesses/{foreign}", headers=authed)
    code = body.get("error", {}).get("code") if isinstance(body, dict) else None
    check("tenant-404", status == 404 and code == "not_found", f"status={status}")

    # 9. Marketplace shape (empty catalog is a valid 200, not an error).
    if businesses:
        status, _, page = call(f"/api/v1/businesses/{businesses[0]}/marketplace?limit=5")
        check(
            "marketplace",
            status == 200
            and isinstance(page.get("items"), list)
            and isinstance(page.get("total"), int),
            f"status={status}",
        )
    else:
        print("[skip] marketplace (no memberships)")

    # 10. Logout revokes server-side; the cookie stops working.
    status, _, _ = call("/api/v1/auth/logout", {}, headers=authed)
    check("logout", status == 200, f"status={status}")

    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
