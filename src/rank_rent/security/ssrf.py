from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    pass


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_outbound_url(
    url: str,
    *,
    allowed_hosts: Iterable[str] = (),
    resolve_dns: bool = True,
) -> str:
    """Reject non-HTTPS, credential-bearing, local, and private-network URLs."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsafeURLError("Outbound URLs must use HTTPS and include a hostname.")
    if parsed.username or parsed.password:
        raise UnsafeURLError("Outbound URLs must not contain credentials.")
    hostname = parsed.hostname.rstrip(".").lower()
    allowlist = {item.rstrip(".").lower() for item in allowed_hosts}
    if allowlist and hostname not in allowlist:
        raise UnsafeURLError("Outbound URL host is not allowlisted.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        if not _is_public_address(hostname):
            raise UnsafeURLError("Outbound URL resolves to a non-public address.")
    else:
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
            raise UnsafeURLError("Outbound URL resolves to a local hostname.")
        if resolve_dns:
            try:
                addresses = {
                    str(result[4][0])
                    for result in socket.getaddrinfo(hostname, parsed.port or 443)
                }
            except socket.gaierror as exc:
                raise UnsafeURLError("Outbound URL hostname could not be resolved.") from exc
            if not addresses or not all(_is_public_address(address) for address in addresses):
                raise UnsafeURLError("Outbound URL resolves to a non-public address.")
    return url
