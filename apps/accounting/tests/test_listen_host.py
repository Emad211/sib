"""Opt-in LAN listening: the SIB_HOST rule for the built-in server.

Today the accounting app binds 127.0.0.1 in production and 0.0.0.0 elsewhere (start.py and
the run section of src/app.py). SIB_HOST lets the clinic opt into LAN listening on a
production install, and is ignored when it is not a valid IPv4 address so a typo can never
silently expose the app.
"""

import pytest

from src.config.settings import resolve_listen_host


class TestSibHostUnset:
    def test_production_defaults_to_loopback(self):
        assert resolve_listen_host(production=True, environ={}) == "127.0.0.1"

    def test_development_defaults_to_all_interfaces(self):
        assert resolve_listen_host(production=False, environ={}) == "0.0.0.0"

    def test_reads_the_real_environment(self, monkeypatch):
        monkeypatch.delenv("SIB_HOST", raising=False)
        assert resolve_listen_host(production=True) == "127.0.0.1"


class TestSibHostValid:
    @pytest.mark.parametrize("value", ["0.0.0.0", "127.0.0.1", "192.168.1.40", "10.242.52.219"])
    def test_valid_ipv4_wins_in_production(self, value):
        assert resolve_listen_host(production=True, environ={"SIB_HOST": value}) == value

    @pytest.mark.parametrize("value", ["0.0.0.0", "127.0.0.1", "192.168.1.40"])
    def test_valid_ipv4_wins_in_development(self, value):
        assert resolve_listen_host(production=False, environ={"SIB_HOST": value}) == value

    def test_surrounding_spaces_are_trimmed(self):
        assert resolve_listen_host(production=True, environ={"SIB_HOST": " 192.168.1.40 "}) == "192.168.1.40"

    def test_reads_sib_host_from_the_real_environment(self, monkeypatch):
        monkeypatch.setenv("SIB_HOST", "0.0.0.0")
        assert resolve_listen_host(production=True) == "0.0.0.0"


class TestSibHostInvalid:
    @pytest.mark.parametrize(
        "value",
        ["", "   ", "localhost", "192.168.1", "192.168.1.40:8080", "999.1.2.3", "::1", "0.0.0.0/0"],
    )
    def test_invalid_falls_back_to_production_default(self, value):
        assert resolve_listen_host(production=True, environ={"SIB_HOST": value}) == "127.0.0.1"

    @pytest.mark.parametrize("value", ["", "localhost", "999.1.2.3", "::1"])
    def test_invalid_falls_back_to_development_default(self, value):
        assert resolve_listen_host(production=False, environ={"SIB_HOST": value}) == "0.0.0.0"
