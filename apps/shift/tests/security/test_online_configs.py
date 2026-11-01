"""O05: the relay and tunnel files carry no secret and the relay never terminates TLS."""

import re
from pathlib import Path

ONLINE = Path(__file__).resolve().parents[2] / "deploy" / "online"


def test_the_relay_passes_tls_through_and_forwards_only_the_public_name() -> None:
    config = (ONLINE / "haproxy.cfg").read_text(encoding="utf-8")
    assert "mode tcp" in config
    assert " ssl " not in config and "crt " not in config, "the relay holds no certificate"
    assert "req.ssl_sni -i shift.clinic.example" in config
    assert "default_backend drop" in config, "any other name is dropped"
    assert "127.0.0.1:7443" in config, "the tunnel end listens on the VPS loopback only"


def test_the_tunnel_files_are_templates_without_secrets() -> None:
    for name in ("rathole-server.toml.example", "rathole-client.toml.example"):
        text = (ONLINE / name).read_text(encoding="utf-8")
        assert "REPLACE_WITH_THE_SHARED_TOKEN" in text, name
        assert 'type = "noise"' in text, f"{name}: the tunnel is encrypted"
        assert not re.search(r"\b[0-9a-f]{32,}\b", text), f"{name}: looks like a real secret"
    server = (ONLINE / "rathole-server.toml.example").read_text(encoding="utf-8")
    assert 'bind_addr = "127.0.0.1:7443"' in server


def test_the_scripts_download_nothing_and_print_no_token() -> None:
    for path in ONLINE.iterdir():
        if path.suffix not in {".sh", ".ps1"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert (
            not re.search(r"\b(curl|wget|Invoke-WebRequest|iwr)\b[^\n]*https?://", text)
            or path.name == "relay-check.sh"
        ), path.name
        assert "Write-Host $Token" not in text and "echo $token" not in text.lower(), path.name
