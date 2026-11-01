"""The LAN run: plain HTTP hands out the root, HTTPS serves the real production app."""

import http.client
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = APP_ROOT / "deploy" / "serve_lan.py"
LAN_IP = "192.168.50.10"
STARTUP_SECONDS = 180


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_until_answering(
    process: subprocess.Popen, url: str, log: Path, *, context: ssl.SSLContext | None = None
) -> None:
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(
                f"serve_lan exited with {process.returncode}:\n{log.read_text(encoding='utf-8', errors='replace')}"
            )
        try:
            with urllib.request.urlopen(url, timeout=5, context=context):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"serve_lan never answered on {url}:\n{log.read_text(encoding='utf-8', errors='replace')}")


def test_the_lan_run_offers_the_root_over_http_and_the_login_page_over_https(tmp_path: Path) -> None:
    home = tmp_path / "home"
    log = tmp_path / "serve_lan.log"
    https_port, http_port = _free_port(), _free_port()
    command = [
        sys.executable,
        str(SCRIPT),
        "--home",
        str(home),
        "--ip",
        LAN_IP,
        "--name",
        "shift.test",
        "--https-port",
        str(https_port),
        "--http-port",
        str(http_port),
    ]
    with log.open("w", encoding="utf-8") as sink:
        process = subprocess.Popen(
            command,
            cwd=str(APP_ROOT),
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    try:
        ca_url = f"http://127.0.0.1:{http_port}/ca.crt"
        _wait_until_answering(process, ca_url, log)
        context = ssl.create_default_context(cafile=str(home / "tls" / "ca.crt"))
        _wait_until_answering(process, f"https://127.0.0.1:{https_port}/healthz", log, context=context)

        with urllib.request.urlopen(ca_url, timeout=10) as answer:
            root = answer.read()
            assert answer.status == 200
            assert answer.headers["Content-Type"].startswith("application/x-x509-ca-cert")
            assert "attachment" in answer.headers["Content-Disposition"]
        assert root == (home / "tls" / "ca.crt").read_bytes()

        with urllib.request.urlopen(f"http://127.0.0.1:{http_port}/", timeout=10) as answer:
            install_page = answer.read().decode("utf-8")
        assert f"https://{LAN_IP}:{https_port}" in install_page
        assert "/ca.crt" in install_page

        connection = http.client.HTTPConnection("127.0.0.1", http_port, timeout=10)
        connection.request("GET", "/login?next=/shift")
        redirect = connection.getresponse()
        assert redirect.status == 307
        assert redirect.getheader("Location") == f"https://127.0.0.1:{https_port}/login?next=/shift"
        redirect.read()
        connection.close()

        with urllib.request.urlopen(f"https://127.0.0.1:{https_port}/login", context=context, timeout=20) as answer:
            page = answer.read().decode("utf-8")
            cookie = answer.headers["Set-Cookie"]
            assert answer.status == 200
        assert "ورود به شیفت" in page
        assert "shift_csrf=" in cookie
        assert "Secure" in cookie
    finally:
        process.terminate()
        process.wait(timeout=60)


def test_the_public_name_gets_its_own_certificate_on_the_same_port(tmp_path: Path) -> None:
    """O05: through the relay the head's phone asks for the public name and gets the public certificate;
    a LAN phone asking for the IP still gets the clinic's own one. One port, two certificates, by SNI."""
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    cert, key = public_dir / "fullchain.pem", public_dir / "privkey.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=shift.public.test",
            "-addext",
            "subjectAltName=DNS:shift.public.test",
        ],
        check=True,
        capture_output=True,
    )
    home = tmp_path / "home"
    log = tmp_path / "serve_lan.log"
    https_port, http_port = _free_port(), _free_port()
    command = [
        sys.executable,
        str(SCRIPT),
        "--home",
        str(home),
        "--ip",
        LAN_IP,
        "--name",
        "shift.test",
        "--https-port",
        str(https_port),
        "--http-port",
        str(http_port),
        "--public-name",
        "shift.public.test",
        "--public-cert",
        str(cert),
        "--public-key",
        str(key),
    ]
    with log.open("w", encoding="utf-8") as sink:
        process = subprocess.Popen(
            command,
            cwd=str(APP_ROOT),
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    try:
        _wait_until_answering(process, f"http://127.0.0.1:{http_port}/ca.crt", log)
        lan = ssl.create_default_context(cafile=str(home / "tls" / "ca.crt"))
        _wait_until_answering(process, f"https://127.0.0.1:{https_port}/healthz", log, context=lan)

        public = ssl.create_default_context(cafile=str(cert))
        with socket.create_connection(("127.0.0.1", https_port), timeout=10) as raw:
            with public.wrap_socket(raw, server_hostname="shift.public.test") as tls:
                presented = tls.getpeercert()
        assert ("DNS", "shift.public.test") in presented["subjectAltName"]

        with socket.create_connection(("127.0.0.1", https_port), timeout=10) as raw:
            lan.check_hostname = False
            with lan.wrap_socket(raw, server_hostname="shift.test") as tls:
                assert ("DNS", "shift.public.test") not in tls.getpeercert().get("subjectAltName", ())
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


def test_the_public_certificate_arguments_go_together(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--home", str(tmp_path), "--ip", LAN_IP, "--public-name", "shift.public.test"],
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "go together" in result.stderr
