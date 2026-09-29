"""Isolate real TCP tests from each other's closed sockets (Linux TIME_WAIT)."""
import socket

import pytest


@pytest.fixture(autouse=True)
def isolated_legacy_port(request, monkeypatch):
    # Keep every original assertion: both the normal two-client session and the
    # deliberately occupied default-port scenario run real sockets/subprocesses.
    if request.node.name not in {
        "test_two_clients_commands_round_and_rematch",
        "test_real_server_partial_packet_does_not_stop_clock_and_disconnect_propagates",
        "test_solo_starts_with_occupied_default_port_and_two_instances",
    }:
        return
    import re7_21 as engine
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(engine, "DEFAULT_PORT", port)
    monkeypatch.setenv("RE7_PORT", str(port))
    monkeypatch.setenv("RE7_BIND", "127.0.0.1")
