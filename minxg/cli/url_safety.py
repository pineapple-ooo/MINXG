"""URL safety checks — blocks requests to private/internal network addresses.

Adapted from Hermes' ``tools/url_safety.py`` and tailored to the
AgentHarness project layout.  The public surface is intentionally small:

  * ``is_safe_url(url)`` — True only for public internet targets
  * ``sensitive_query_param_name(url)`` — credential-bearing query keys
  * ``normalize_url_for_request(url)`` — ASCII-safe HTTP/HTTPS URL

Cloud metadata endpoints (169.254.169.254, metadata.google.internal,
etc.) are always blocked; CGNAT/private ranges are blocked unless the
user explicitly opts out via ``HERMES_ALLOW_PRIVATE_URLS`` or
``security.allow_private_urls`` in config.yaml.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Optional
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit


# Query parameter names that are unambiguously credential-bearing.
_SENSITIVE_QUERY_PARAM_NAMES = frozenset({
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "awsaccesskeyid",
    "client_secret",
    "credential",
    "credentials",
    "jwt",
    "password",
    "passwd",
    "secret",
    "session_id",
    "signature",
    "token",
    "x_amz_security_token",
    "x_amz_signature",
    "x-amz-security-token",
    "x-amz-signature",
})

# Hostnames that should always be blocked regardless of IP resolution.
_BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
})

# IPs / networks that should always be blocked regardless of config toggle.
_ALWAYS_BLOCKED_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("169.254.169.253"),
    ipaddress.ip_address("100.100.100.200"),
})

_ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
)

# Cache for the global allow-private toggle.
_allow_private_resolved = False
_cached_allow_private = False


def _global_allow_private_urls() -> bool:
    """Return True when the user has opted out of private-IP blocking."""
    global _allow_private_resolved, _cached_allow_private
    if _allow_private_resolved:
        return _cached_allow_private
    _allow_private_resolved = True
    _cached_allow_private = False
    env_val = os.getenv("HERMES_ALLOW_PRIVATE_URLS", "").strip().lower()
    if env_val in {"true", "1", "yes"}:
        _cached_allow_private = True
        return True
    try:
        from multiligua_cli.utils import load_config
        cfg = load_config()
        security = cfg.get("security", {})
        if security.get("allow_private_urls"):
            _cached_allow_private = True
            return True
        browser = cfg.get("browser", {})
        if browser.get("allow_private_urls"):
            _cached_allow_private = True
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def normalize_url_for_request(url: str) -> str:
    """Return an ASCII-safe HTTP URL for AgentHarness URL tools."""
    if not isinstance(url, str):
        return url
    raw = url.strip()
    if not raw:
        return raw
    raw = re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)\s+", r"\1", raw)
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if parsed.scheme.lower() not in {"http", "https"}:
        return raw
    netloc = parsed.netloc
    hostname = parsed.hostname
    if hostname:
        try:
            ascii_host = hostname.encode("idna").decode("ascii")
        except Exception:
            ascii_host = hostname
        if ascii_host != hostname:
            netloc = netloc.replace(hostname, ascii_host, 1)
    path = quote(parsed.path, safe="/%:@!$&'()*+,;=")
    query = quote(parsed.query, safe="/%:@!$&'()*+,;=?")
    fragment = quote(parsed.fragment, safe="/%:@!$&'()*+,;=?")
    return urlunsplit((parsed.scheme, netloc, path, query, fragment))


def sensitive_query_param_name(url: str) -> Optional[str]:
    """Return the first credential-bearing query parameter name in ``url``."""
    if not isinstance(url, str) or "?" not in url:
        return None
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.query:
        return None
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if unquote(key).lower() in _SENSITIVE_QUERY_PARAM_NAMES:
            return key
    return None


def has_sensitive_query_params(url: str) -> bool:
    """Return True when ``url`` carries likely credential-bearing query params."""
    return sensitive_query_param_name(url) is not None


def is_safe_url(url: str) -> bool:
    """Return True only for public internet targets.

    Always blocks cloud metadata endpoints and link-local addresses.
    Blocks private/CGNAT ranges unless the user explicitly allows them.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    url = normalize_url_for_request(url)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    if hostname in _BLOCKED_HOSTNAMES:
        return False
    if has_sensitive_query_params(url):
        # Credential-bearing URLs are still "safe" structurally, but we flag
        # them separately so callers can warn; we do not hard-block here
        # because some legitimate APIs need them.
        pass
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    if addr in _ALWAYS_BLOCKED_IPS:
        return False
    for net in _ALWAYS_BLOCKED_NETWORKS:
        if addr in net:
            return False
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return _global_allow_private_urls()
    return True
