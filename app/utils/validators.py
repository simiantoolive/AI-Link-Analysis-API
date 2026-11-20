"""URL validation and SSRF protection utilities.

Prevents the server from accessing localhost, loopback, link-local (including
cloud metadata endpoints like 169.254.169.254), private RFC1918 networks,
reserved ranges, or non-HTTP(S) schemes.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse


class URLValidationError(ValueError):
    """Raised when a URL is malformed, unsupported, or blocked by SSRF policy."""

    def __init__(self, message: str, error_code: str = "INVALID_URL") -> None:
        super().__init__(message)
        self.error_code = error_code


BLOCKED_HOSTNAMES: set[str] = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata",
}

BLOCKED_SUFFIXES: tuple[str, ...] = (
    ".local",
    ".localhost",
    ".internal",
    ".localdomain",
    ".home.arpa",
)


def _is_public_ip(ip_str: str) -> bool:
    """Return True only if the IP address is globally routable."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    # Unwrap IPv4-mapped IPv6 addresses (e.g., ::ffff:127.0.0.1)
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    return (
        ip_obj.is_global
        and not ip_obj.is_private
        and not ip_obj.is_loopback
        and not ip_obj.is_link_local
        and not ip_obj.is_multicast
        and not ip_obj.is_reserved
        and not ip_obj.is_unspecified
    )


def _validate_hostname_and_dns(hostname: str) -> None:
    """Validate hostname string and ensure all resolved IPs are publicly routable."""
    lowered = hostname.lower().rstrip(".")
    if not lowered:
        raise URLValidationError("URL must include a valid hostname.", "INVALID_URL")

    if lowered in BLOCKED_HOSTNAMES or lowered.endswith(BLOCKED_SUFFIXES):
        raise URLValidationError(
            "Access to private, loopback, or internal hostnames is forbidden.",
            "SSRF_BLOCKED",
        )

    # Reject raw IP literals that are non-public immediately (no DNS needed)
    try:
        ipaddress.ip_address(lowered)
    except ValueError:
        pass
    else:
        if not _is_public_ip(lowered):
            raise URLValidationError(
                "Access to private or internal IP addresses is forbidden.",
                "SSRF_BLOCKED",
            )
        return

    # Require a dot in public hostnames (blocks single-label internal hosts like "redis" or "db")
    if "." not in lowered:
        raise URLValidationError(
            "URL hostname must be a valid public domain name.",
            "INVALID_URL",
        )

    try:
        addr_info = socket.getaddrinfo(lowered, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise URLValidationError(
            f"Unable to resolve hostname '{lowered}'. Please check the URL.",
            "DNS_RESOLUTION_FAILED",
        ) from exc

    if not addr_info:
        raise URLValidationError(
            f"No IP addresses found for hostname '{lowered}'.",
            "DNS_RESOLUTION_FAILED",
        )

    for entry in addr_info:
        ip_str = entry[4][0]
        if not _is_public_ip(ip_str):
            raise URLValidationError(
                "Target hostname resolves to a private or internal network address and is blocked.",
                "SSRF_BLOCKED",
            )


def normalize_and_validate_url(raw_url: str, *, resolve_dns: bool = True) -> str:
    """Validate URL format, scheme, and SSRF safety, returning a normalized URL."""
    if not raw_url or not isinstance(raw_url, str):
        raise URLValidationError("A valid website URL is required.", "INVALID_URL")

    cleaned = raw_url.strip()
    if len(cleaned) > 2048:
        raise URLValidationError("URL exceeds maximum allowed length (2048 chars).", "INVALID_URL")

    parsed = urlparse(cleaned)

    if parsed.scheme.lower() not in {"http", "https"}:
        raise URLValidationError(
            "Only 'http://' and 'https://' URLs are supported.",
            "INVALID_URL",
        )

    if parsed.username or parsed.password:
        raise URLValidationError(
            "URLs containing embedded credentials are not allowed.",
            "INVALID_URL",
        )

    if not parsed.hostname:
        raise URLValidationError("URL must include a valid domain name.", "INVALID_URL")

    try:
        port = parsed.port
    except ValueError as exc:
        raise URLValidationError("URL contains an invalid port number.", "INVALID_URL") from exc

    if port is not None and port not in {80, 443, 8080, 8443}:
        raise URLValidationError(
            "Only standard web ports (80, 443, 8080, 8443) are permitted.",
            "SSRF_BLOCKED",
        )

    if resolve_dns:
        _validate_hostname_and_dns(parsed.hostname)
    else:
        # Still check obvious blocked hostnames / raw IPs without network lookup
        lowered = parsed.hostname.lower().rstrip(".")
        if lowered in BLOCKED_HOSTNAMES or lowered.endswith(BLOCKED_SUFFIXES):
            raise URLValidationError(
                "Access to private, loopback, or internal hostnames is forbidden.",
                "SSRF_BLOCKED",
            )
        try:
            ipaddress.ip_address(lowered)
        except ValueError:
            if "." not in lowered:
                raise URLValidationError(
                    "URL hostname must be a valid public domain name.",
                    "INVALID_URL",
                )
        else:
            if not _is_public_ip(lowered):
                raise URLValidationError(
                    "Access to private or internal IP addresses is forbidden.",
                    "SSRF_BLOCKED",
                )

    # Normalize scheme & hostname casing, strip fragment
    netloc = parsed.hostname.lower()
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"

    path = parsed.path or "/"
    normalized = urlunparse(
        (parsed.scheme.lower(), netloc, path, "", parsed.query, "")
    )
    return normalized


def cache_key_for_url(normalized_url: str) -> str:
    """Create a canonical cache key for a normalized URL."""
    return normalized_url.rstrip("/")
