from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from almanac.errors import FetchBlocked, FetchFailed


def default_resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as e:
        raise FetchFailed(f"network: {e}") from e
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _blocked(addr: str) -> bool:
    """True when the address is not safe to fetch from. Unparseable addresses are blocked."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global  # also covers CGNAT 100.64.0.0/10 (e.g. cloud metadata at 100.100.100.200)
    )


def check_url(url: str, allow_private: bool, resolve: Callable[[str], list[str]] = default_resolve) -> None:
    """Raise FetchBlocked unless url is http(s) and every address its host resolves to is public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchBlocked("scheme")
    if parts.username is not None or parts.password is not None:
        raise FetchBlocked("credentials")
    host = parts.hostname
    if not host:
        raise FetchBlocked("scheme")
    if allow_private:
        return
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        addrs = [host]  # literal IP: no DNS
    except ValueError:
        addrs = resolve(host)
    if not addrs or any(_blocked(a) for a in addrs):
        raise FetchBlocked("private-address")
