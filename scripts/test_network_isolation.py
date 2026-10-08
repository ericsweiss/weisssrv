"""The scripts/ suite never reaches the network.

Pins the autouse guard in scripts/conftest.py: an outbound connect raises,
loopback still works, and the error is not an OSError a caller can swallow.
"""
from __future__ import annotations

import socket
import urllib.request

import pytest

from conftest import NetworkBlocked, _is_loopback

# RFC 5737 documentation range: never routed, so the guard is what stops it.
UNROUTED = "198.51.100.1"


def test_an_outbound_connect_is_refused():
    with socket.socket() as sock, pytest.raises(NetworkBlocked):
        sock.connect((UNROUTED, 80))


def test_connect_ex_is_refused_too():
    """urllib3-style callers reach for connect_ex; it must not slip past."""
    with socket.socket() as sock, pytest.raises(NetworkBlocked):
        sock.connect_ex((UNROUTED, 80))


def test_urlopen_is_refused_and_not_disguised_as_urlerror():
    with pytest.raises(NetworkBlocked):
        urllib.request.urlopen(f"http://{UNROUTED}/latest", timeout=1)
    assert not issubclass(NetworkBlocked, OSError)


def test_loopback_is_still_allowed():
    """A suite that stands up its own server on 127.0.0.1 keeps working."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as client:
            client.settimeout(2)
            client.connect(server.getsockname())


@pytest.mark.parametrize(
    ("address", "local"),
    [
        (("127.0.0.1", 8080), True),
        (("localhost", 80), True),
        (("::1", 80), True),
        ((UNROUTED, 443), False),
        (("registry.example.com", 443), False),
        (("192.0.2.10", 53), False),
    ],
)
def test_only_loopback_peers_are_treated_as_local(address, local):
    """Mutation case: widening this predicate re-opens the suite to the LAN."""
    assert _is_loopback(address) is local
