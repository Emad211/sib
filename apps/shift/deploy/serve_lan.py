"""Serve the shift app over trusted HTTPS on the clinic LAN without Caddy or NSSM.

Caddy stays the documented route (ADR-015). This is the supported alternative for a clinic PC
that has neither Caddy nor NSSM: one Python process keeps a local root CA under ``<home>/tls``,
signs the server certificate with it, and runs two listeners:

* plain HTTP on ``--http-port``: only ``GET /ca.crt`` (the root a phone installs once) and a short
  Persian install page; every other request redirects to the HTTPS URL;
* HTTPS on ``--https-port``: the production app from ``start.py`` (same ``create_app`` and
  ``Settings``, ``SHIFT_ENV=production``, Secure cookies untouched), listening on ``0.0.0.0``.

    python deploy/serve_lan.py --home C:/ShiftData --ip 192.168.1.10

The server certificate is reissued on start when the IP or the names changed, always by the same
root, so a phone never installs anything twice. Nothing is downloaded, no secret is printed, and
the private keys stay under ``<home>/tls`` with owner-only rights where the OS allows it.
"""

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

from shift.app import create_app  # noqa: E402
from shift.config.settings import Settings  # noqa: E402

TLS_DIR = "tls"
CA_KEY_NAME = "ca.key"
CA_CERT_NAME = "ca.crt"
SERVER_KEY_NAME = "server.key"
SERVER_CERT_NAME = "server.crt"
STATE_NAME = "server.json"
CA_SUBJECT = "/CN=Sib Shift Local CA/O=Sib Clinic"
# A CA outlives the server certificates it signs; Android Chrome rejects a leaf that lives longer
# than 398 days, so the server certificate stays at 397 and is renewed a month before it lapses.
CA_VALIDITY_DAYS = 3650
SERVER_VALIDITY_DAYS = 397
RENEW_BEFORE_DAYS = 30
CA_USAGE = "basicConstraints=critical,CA:TRUE"
CA_KEY_USAGE = "keyUsage=critical,keyCertSign,cRLSign"
CERTIFICATE_TYPE = "application/x-x509-ca-cert"
HOST_NAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?")
INSTALL_PAGE = """<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>نصب گواهی اپ شیفت</title>
<style>
body{font-family:system-ui,sans-serif;background:#f6f7f9;color:#1f2937;margin:0;padding:24px;line-height:1.9}
main{max-width:34rem;margin:0 auto;background:#fff;border-radius:16px;padding:24px;box-shadow:0 2px 12px rgba(15,23,42,.08)}
h1{font-size:1.35rem;margin:0 0 12px}
a{color:#0f766e}
code{direction:ltr;display:inline-block;background:#f1f5f9;border-radius:6px;padding:2px 6px}
ol{padding-inline-start:1.4rem}
</style>
</head>
<body>
<main>
<h1>نصب گواهی امنیتی اپ شیفت</h1>
<p>برای اینکه اپ شیفت روی این گوشی باز شود، یک‌بار گواهی ریشهٔ درمانگاه را نصب کنید:</p>
<ol>
<li><a href="/ca.crt" download>دانلود گواهی ریشه (ca.crt)</a> — فایل روی گوشی ذخیره می‌شود.</li>
<li>فایل را باز کنید و در پیام «نصب گواهی» تأیید کنید (اندروید: تنظیمات ← امنیت ← رمزنگاری و
اعتبارنامه‌ها ← نصب گواهی ← گواهی CA).</li>
<li>سپس اپ را از نشانی <a href="{https_url}">{https_url}</a> باز کنید.</li>
</ol>
<p>این صفحه فقط برای نصب گواهی است؛ بقیهٔ نشانی‌ها خودکار به نسخهٔ امن (HTTPS) می‌روند.</p>
</main>
</body>
</html>
"""


@dataclass(frozen=True, slots=True)
class Certificates:
    """The four PEM files: one root CA (kept) and one server certificate (reissued)."""

    ca_key: Path
    ca_crt: Path
    server_key: Path
    server_crt: Path


def ensure_certificates(home: Path, *, ip: str, name: str) -> Certificates:
    """Return the certificates under ``<home>/tls``, creating the CA once and reusing it after."""
    tls_dir = Path(home).resolve() / TLS_DIR
    tls_dir.mkdir(parents=True, exist_ok=True)
    _restrict(tls_dir, 0o700)
    certificates = Certificates(
        ca_key=tls_dir / CA_KEY_NAME,
        ca_crt=tls_dir / CA_CERT_NAME,
        server_key=tls_dir / SERVER_KEY_NAME,
        server_crt=tls_dir / SERVER_CERT_NAME,
    )
    backend = certificate_backend()
    if not (certificates.ca_key.exists() and certificates.ca_crt.exists()):
        _create_ca(backend, certificates)
    state_file = tls_dir / STATE_NAME
    if _server_certificate_is_stale(state_file, certificates, ip=ip, name=name):
        _create_server_certificate(backend, certificates, ip=ip, name=name)
        _write_state(state_file, ip=ip, name=name)
    return certificates


def certificate_backend() -> str:
    """`cryptography` when the venv has it, otherwise the `openssl` on PATH. Never both."""
    if importlib.util.find_spec("cryptography") is not None:
        return "cryptography"
    if shutil.which("openssl"):
        return "openssl"
    raise SystemExit(
        "serve_lan: neither the `cryptography` package nor `openssl` is available; "
        "install one of them, nothing is downloaded automatically."
    )


def _server_certificate_is_stale(state_file: Path, certificates: Certificates, *, ip: str, name: str) -> bool:
    if not (certificates.server_key.exists() and certificates.server_crt.exists()):
        return True
    state = _read_state(state_file)
    if state is None or state.get("ip") != ip or state.get("name") != name:
        return True
    try:
        not_after = datetime.fromisoformat(state["not_after"])
    except (KeyError, ValueError):
        return True
    return not_after - timedelta(days=RENEW_BEFORE_DAYS) <= datetime.now(UTC)


def _read_state(state_file: Path) -> dict[str, str] | None:
    try:
        loaded = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_state(state_file: Path, *, ip: str, name: str) -> None:
    not_after = datetime.now(UTC) + timedelta(days=SERVER_VALIDITY_DAYS)
    state_file.write_text(
        json.dumps({"ip": ip, "name": name, "not_after": not_after.isoformat()}, indent=2) + "\n",
        encoding="utf-8",
    )


def _create_ca(backend: str, certificates: Certificates) -> None:
    if backend == "cryptography":
        _create_ca_with_cryptography(certificates)
    else:
        _create_ca_with_openssl(certificates)


def _create_server_certificate(backend: str, certificates: Certificates, *, ip: str, name: str) -> None:
    if backend == "cryptography":
        _create_server_with_cryptography(certificates, ip=ip, name=name)
    else:
        _create_server_with_openssl(certificates, ip=ip, name=name)


def _create_ca_with_cryptography(certificates: Certificates) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    now = datetime.now(UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sib Shift Local CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_private(
        certificates.ca_key,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    certificates.ca_crt.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _create_server_with_cryptography(certificates: Certificates, *, ip: str, name: str) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_key = serialization.load_pem_private_key(certificates.ca_key.read_bytes(), password=None)
    ca_certificate = x509.load_pem_x509_certificate(certificates.ca_crt.read_bytes())
    now = datetime.now(UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=SERVER_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address(ip)),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.DNSName(name),
                    x509.DNSName("localhost"),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_private(
        certificates.server_key,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    certificates.server_crt.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _create_ca_with_openssl(certificates: Certificates) -> None:
    _run_openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:3072",
        "-sha256",
        "-days",
        str(CA_VALIDITY_DAYS),
        "-nodes",
        "-keyout",
        str(certificates.ca_key),
        "-out",
        str(certificates.ca_crt),
        "-subj",
        CA_SUBJECT,
        "-addext",
        CA_USAGE,
        "-addext",
        CA_KEY_USAGE,
        "-addext",
        "subjectKeyIdentifier=hash",
    )
    _restrict(certificates.ca_key, 0o600)


def _create_server_with_openssl(certificates: Certificates, *, ip: str, name: str) -> None:
    request_file = certificates.server_crt.with_suffix(".csr")
    extension_file = certificates.server_crt.with_suffix(".ext")
    extension_file.write_text(_server_extensions(ip=ip, name=name), encoding="ascii")
    try:
        _run_openssl(
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-keyout",
            str(certificates.server_key),
            "-out",
            str(request_file),
            "-subj",
            f"/CN={name}",
        )
        _run_openssl(
            "x509",
            "-req",
            "-in",
            str(request_file),
            "-CA",
            str(certificates.ca_crt),
            "-CAkey",
            str(certificates.ca_key),
            "-set_serial",
            str(secrets.randbits(127)),
            "-days",
            str(SERVER_VALIDITY_DAYS),
            "-sha256",
            "-extfile",
            str(extension_file),
            "-out",
            str(certificates.server_crt),
        )
    finally:
        for temporary in (request_file, extension_file):
            temporary.unlink(missing_ok=True)
    _restrict(certificates.server_key, 0o600)


def _server_extensions(*, ip: str, name: str) -> str:
    return "\n".join(
        [
            "basicConstraints=critical,CA:FALSE",
            "keyUsage=critical,digitalSignature,keyEncipherment",
            "extendedKeyUsage=serverAuth",
            f"subjectAltName=IP:{ip},IP:127.0.0.1,DNS:{name},DNS:localhost",
            "",
        ]
    )


def _run_openssl(*arguments: str) -> None:
    """Run one `openssl` step; the arguments and its output never hold a printable secret."""
    executable = shutil.which("openssl") or "openssl"
    result = subprocess.run([executable, *arguments], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"openssl {arguments[0]} failed: {detail}")


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    _restrict(path, 0o600)


def _restrict(path: Path, mode: int) -> None:
    """Owner-only where the OS honours it; on Windows the folder ACL is the real guard."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def build_install_app(certificates: Certificates, *, https_url: str, https_port: int, ip: str, name: str) -> Starlette:
    """The plain-HTTP listener: the root certificate, a short install page, redirects elsewhere."""
    trusted_hosts = {ip.lower(), name.lower(), "localhost", "127.0.0.1"}

    async def root_certificate(request: Request) -> FileResponse:
        return FileResponse(certificates.ca_crt, media_type=CERTIFICATE_TYPE, filename="sib-shift-ca.crt")

    async def install_page(request: Request) -> HTMLResponse:
        return HTMLResponse(INSTALL_PAGE.replace("{https_url}", https_url))

    async def to_https(request: Request) -> RedirectResponse:
        hostname = (request.url.hostname or "").lower()
        host = hostname if hostname in trusted_hosts else ip
        port = "" if https_port == 443 else f":{https_port}"
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"https://{host}{port}{request.url.path}{query}", status_code=307)

    return Starlette(
        routes=[
            Route("/ca.crt", root_certificate, methods=["GET"]),
            Route("/", install_page, methods=["GET"]),
            # Everything else, in any verb, leaves for the HTTPS address of the same host.
            Route("/{rest:path}", to_https, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    """Run both listeners until Ctrl+C and shut them down cleanly."""
    arguments = _parse_arguments(argv)
    home = Path(arguments.home).expanduser().resolve()
    ip = arguments.ip
    certificates = ensure_certificates(home, ip=ip, name=arguments.name)
    # The production app, exactly as `start.py` builds it, with the home and the port of this run.
    settings = replace(
        Settings.from_env({**os.environ, "SHIFT_HOME": str(home)}),
        host="0.0.0.0",
        port=arguments.https_port,
        environment="production",
    )
    https_url = f"https://{ip}:{arguments.https_port}"
    ca_url = f"http://{ip}:{arguments.http_port}/ca.crt"
    install_app = build_install_app(
        certificates,
        https_url=https_url,
        https_port=arguments.https_port,
        ip=ip,
        name=arguments.name,
    )
    servers = (
        uvicorn.Server(
            uvicorn.Config(
                install_app,
                host="0.0.0.0",
                port=arguments.http_port,
                proxy_headers=True,
                forwarded_allow_ips="127.0.0.1",
                log_level="warning",
            )
        ),
        uvicorn.Server(
            uvicorn.Config(
                create_app(settings),
                host="0.0.0.0",
                port=arguments.https_port,
                ssl_certfile=str(certificates.server_crt),
                ssl_keyfile=str(certificates.server_key),
                proxy_headers=True,
                forwarded_allow_ips="127.0.0.1",
                log_level="info",
            )
        ),
    )
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    workers = []
    for scheme, server in zip(("http", "https"), servers, strict=True):
        worker = threading.Thread(target=server.run, name=f"shift-{scheme}", daemon=True)
        worker.start()
        workers.append(worker)
    if not _wait_until_serving(servers, workers):
        _shut_down(servers, workers)
        print(
            "serve_lan: a listener did not start; is its port already in use?",
            file=sys.stderr,
        )
        return 1
    print(f"shift HTTPS: {https_url}/")
    print(f"root CA:     {ca_url}  (install it once on every phone)")
    stop.wait()
    _shut_down(servers, workers)
    print("serve_lan: stopped")
    return 0


def _wait_until_serving(servers: tuple[uvicorn.Server, ...], workers: list[threading.Thread]) -> bool:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if all(server.started for server in servers):
            return True
        if not all(worker.is_alive() for worker in workers):
            return False
        time.sleep(0.1)
    return False


def _shut_down(servers: tuple[uvicorn.Server, ...], workers: list[threading.Thread]) -> None:
    for server in servers:
        server.should_exit = True
    for worker in workers:
        worker.join(timeout=30)


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="serve_lan.py",
        description="Run the shift app with trusted HTTPS on the clinic LAN, without Caddy or NSSM.",
    )
    parser.add_argument("--home", required=True, help="data directory; TLS keys land in <home>/tls")
    parser.add_argument("--ip", required=True, type=_ip_address, help="the LAN IP phones will use")
    parser.add_argument("--name", default="shift.local", type=_host_name, help="a second name for the certificate")
    parser.add_argument("--https-port", default=8443, type=_port, help="HTTPS port (default 8443)")
    parser.add_argument("--http-port", default=8080, type=_port, help="plain HTTP port for /ca.crt (default 8080)")
    return parser.parse_args(argv)


def _ip_address(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not an IP address: {value}") from error


def _host_name(value: str) -> str:
    if not HOST_NAME.fullmatch(value.strip()):
        raise argparse.ArgumentTypeError(f"not a host name: {value}")
    return value.strip()


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port out of range: {value}")
    return port


if __name__ == "__main__":
    raise SystemExit(main())
