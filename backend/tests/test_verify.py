"""Verify-script regression tests (Stage 17): mock TLS server, no public host.

Pins deploy/verify.ps1 against a LOCAL stub HTTPS server (Python stdlib
ssl + http.server, self-signed cert generated at runtime via the
`cryptography` package if present, else TLS cases skip):
- usage failures: missing/http/loopback base URL (exit 1), no probes made;
- untrusted TLS fails closed (exit 3) instead of silently disabling
  certificate validation; the stub replays the REAL backend contracts
  (HealthResponse, ReadinessResponse, error envelope, CORS echo rules,
  HTML storefront root) but is deliberately self-signed;
- static pins that the script stays read-only and never disables TLS.

A successful full HTTPS pass needs a genuinely trusted certificate and is
NOT RUN here (operator runs verify.ps1 after deploy). No real credentials,
no public hosts, no mutations, no AI runs.
"""

import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent.parent / "deploy"
REPO_ROOT = DEPLOY.parent
VERIFY = DEPLOY / "verify.ps1"

_PWSH = "powershell"

try:
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

needs_crypto = pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography package required")


class StubHandler(BaseHTTPRequestHandler):
    """Replay of the real backend contracts (see app/api/v1/*.py)."""

    mode = "ok"

    def log_message(self, *args: object) -> None:
        pass

    def _cors_headers(self) -> None:
        req_origin = self.headers.get("Origin", "")
        if req_origin == "https://verify.test.invalid":
            self.send_header("Access-Control-Allow-Origin", req_origin)
            self.send_header("Access-Control-Allow-Credentials", "true")

    def _send_json(self, status: int, payload: object) -> None:
        import json

        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Trace-Id", "trace123abc456")
        if self.command == "OPTIONS":
            self._cors_headers()
        self.end_headers()
        if self.command != "OPTIONS":
            self.wfile.write(raw)

    def _route(self) -> None:
        if self.path == "/api/v1/health":
            payload = {"status": "ok", "version": "0.1.0", "environment": "prod", "contract": "v1"}
            self._send_json(200, payload)
        elif self.path == "/api/v1/readiness":
            if self.mode == "degraded":
                self._send_json(503, {"status": "degraded", "database": "unconfigured"})
            else:
                self._send_json(200, {"status": "ready", "database": "ok"})
        elif self.path.startswith("/api/v1/no-such-route"):
            err = {"code": "not_found", "message": "No route.", "trace_id": "trace123abc456"}
            self._send_json(404, {"error": err})
        elif self.path == "/api/v1/auth/logout" and self.command == "OPTIONS":
            self._send_json(204, {})
        elif self.path == "/":
            raw = b"<html><head><title>Fluxus</title></head><body>ok</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            payload = {"error": {"code": "not_found", "message": "No route.", "trace_id": "t"}}
            self._send_json(404, payload)

    do_GET = _route
    do_OPTIONS = _route

def _make_cert(cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "verify.test.invalid")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("verify.test.invalid")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


@pytest.fixture()
def stub_server():
    __tracebackhide__ = True
    if not _HAS_CRYPTO:
        pytest.skip("cryptography package required")
    tmp = Path(tempfile.mkdtemp(prefix="verify-test-"))
    cert_path, key_path = tmp / "cert.pem", tmp / "key.pem"
    _make_cert(cert_path, key_path)
    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)


def _free_loopback_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _run(base_url: str | None) -> subprocess.CompletedProcess[str]:
    args = ["-BaseUrl", base_url] if base_url is not None else []
    env = dict(os.environ)
    for proxy_var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(proxy_var, None)
    return subprocess.run(
        [_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(VERIFY), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=120,
    )


def test_verify_rejects_missing_base_url() -> None:
    proc = _run(None)
    assert proc.returncode == 1
    assert "usage" in (proc.stdout + proc.stderr).lower()


def test_verify_rejects_http_and_loopback() -> None:
    for bad in ("http://verify.test.invalid", "https://127.0.0.1:8443"):
        proc = _run(bad)
        assert proc.returncode == 1, bad
        assert "usage" in (proc.stdout + proc.stderr).lower()


@needs_crypto
def test_verify_rejects_untrusted_tls(stub_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """A self-signed endpoint must fail closed, proving TLS validation is on."""
    monkeypatch.setenv("SSL_CERT_FILE", "")
    proc = _run(f"https://verify.test.invalid:{stub_server}")
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 3
    assert "health" in combined
    assert "unreachable" in combined


@needs_crypto
def test_verify_source_safety_properties() -> None:
    """Static pins: read-only verbs, validated TLS, no secret handling."""
    body = VERIFY.read_text(encoding="utf-8")
    assert "BaseUrl" in body
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert f"-Method '{verb}'" not in body, f"verify must be read-only (no {verb})"
    assert "ServerCertificateCustomValidationCallback" not in body
