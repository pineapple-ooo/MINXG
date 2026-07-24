"""
agent_harness/utils/network_safety.py — SSRF / URL-safety guard rails.

Blocks:
  - non-http(s) schemes (file://, gopher://, ftp://, ...)
  - private / loopback / link-local destinations
  - literal hostnames known to be internal (localhost, ip6-localhost, ...)
"""
from __future__ import annotations
import ipaddress
import socket
import urllib.parse
from typing import Optional


_BLOCKED_HOSTNAMES = {
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    "127.0.0.1",
    "::1",
}


def _resolve(host: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = {ipaddress.ip_address(info[4][0]) for info in infos}
        return addrs.pop() if addrs else None
    except Exception:
        return None


def is_private_host(host: str) -> bool:
    if host.lower() in _BLOCKED_HOSTNAMES:
        return True
    addr = _resolve(host)
    if addr is None:
        return True
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


def validate_url(url: str) -> str:
    """
    Validate a URL for outbound requests.
    Returns the normalized URL on success.
    Raises ValueError with a reason on failure.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ValueError(f"invalid URL: {exc}") from exc

    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported scheme: {parsed.scheme or '<empty>'}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("missing host")
    if is_private_host(host):
        raise ValueError(f"private/internal destination blocked: {host}")
    return url
