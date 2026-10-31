"""The LAN TLS alternative: one root signs a short server certificate and survives a new IP."""

import socket
import ssl
import threading
from pathlib import Path
from typing import Any

from serve_lan import SERVER_VALIDITY_DAYS, Certificates, ensure_certificates

LAN_IP = "192.168.50.10"
OTHER_LAN_IP = "192.168.60.20"
DAY_SECONDS = 24 * 60 * 60


def _trusted_peer_certificate(certificates: Certificates, hostname: str) -> dict[str, Any]:
    """One TLS handshake in which the client trusts only our root and checks `hostname`."""
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificates.server_crt, certificates.server_key)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            # The client hangs up as soon as it has the certificate, which Windows reports as an
            # aborted connection; the client side asserts, so a real handshake failure still fails.
            try:
                with server_context.wrap_socket(connection, server_side=True) as tls:
                    tls.recv(1)
            except OSError:
                pass
        listener.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    client_context = ssl.create_default_context(cafile=str(certificates.ca_crt))
    with socket.create_connection(("127.0.0.1", port), timeout=20) as raw:
        with client_context.wrap_socket(raw, server_hostname=hostname) as tls:
            peer = tls.getpeercert()
            tls.sendall(b"x")
    worker.join(timeout=20)
    assert not worker.is_alive(), "the TLS handshake did not finish"
    return peer


def test_the_server_certificate_chains_to_the_root_and_covers_the_lan_ip(tmp_path: Path) -> None:
    certificates = ensure_certificates(tmp_path, ip=LAN_IP, name="shift.local")

    peer = _trusted_peer_certificate(certificates, LAN_IP)

    names = {tuple(entry) for entry in peer["subjectAltName"]}
    assert ("IP Address", LAN_IP) in names
    assert ("IP Address", "127.0.0.1") in names
    assert ("DNS", "shift.local") in names
    assert ("DNS", "localhost") in names
    valid_from = ssl.cert_time_to_seconds(peer["notBefore"])
    valid_to = ssl.cert_time_to_seconds(peer["notAfter"])
    assert valid_to - valid_from <= SERVER_VALIDITY_DAYS * DAY_SECONDS


def test_a_changed_ip_reissues_the_certificate_under_the_same_root(tmp_path: Path) -> None:
    first = ensure_certificates(tmp_path, ip=LAN_IP, name="shift.local")
    root = first.ca_crt.read_bytes(), first.ca_key.read_bytes()
    issued = first.server_crt.read_bytes()

    second = ensure_certificates(tmp_path, ip=OTHER_LAN_IP, name="shift.local")
    unchanged = ensure_certificates(tmp_path, ip=OTHER_LAN_IP, name="shift.local")

    assert (second.ca_crt.read_bytes(), second.ca_key.read_bytes()) == root
    assert second.server_crt.read_bytes() != issued
    assert unchanged.server_crt.read_bytes() == second.server_crt.read_bytes()
    peer = _trusted_peer_certificate(second, OTHER_LAN_IP)
    names = {tuple(entry) for entry in peer["subjectAltName"]}
    assert ("IP Address", OTHER_LAN_IP) in names
    assert ("IP Address", LAN_IP) not in names
