"""Fetch guard (Phase 4): every outbound HTTP fetch passes through here.

Defences (§34/§35, design doc 05 §8):
  * https-only, no credentials in URL;
  * allowlist required + blocklist override;
  * SSRF: host must resolve to a public IP — private/loopback/link-local
    (incl. cloud metadata 169.254.169.254) rejected AFTER DNS resolution, so
    DNS rebinding via getaddrinfo is covered by resolving once and pinning;
  * content-type whitelist (html/text/pdf only);
  * hard byte cap enforced while streaming;
  * redirect re-validation: we do NOT auto-follow; each hop is re-checked.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from backend.retrieval.policy import DomainPolicy


class FetchDenied(Exception):
    """URL/host/content refused by policy or safety checks."""


@dataclass
class FetchedContent:
    url: str
    final_url: str
    status_code: int
    content: bytes
    content_type: str
    title: str | None = None


_ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain",
                          "application/pdf")
_MAX_HOPS = 4


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast
                or (addr.version == 6 and addr.is_site_local))


def validate_url(url: str, policy: DomainPolicy) -> tuple[str, str]:
    """Static checks + DNS pinning. Returns (host, pinned_ip). Raises FetchDenied."""
    u = urlparse(url)
    if u.scheme != "https":
        raise FetchDenied(f"scheme {u.scheme!r} not allowed (https only)")
    if not u.hostname:
        raise FetchDenied("missing host")
    if u.username or u.password:
        raise FetchDenied("credentials in URL are forbidden")
    host = u.hostname.lower()
    if not policy.domain_allowed(host):
        raise FetchDenied(f"domain {host!r} not in approved allowlist")
    # Resolve once and pin: prevents TOCTOU DNS rebinding to internal IPs.
    try:
        infos = socket.getaddrinfo(host, u.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise FetchDenied(f"DNS resolution failed for {host}: {e}") from e
    ips = sorted({i[4][0] for i in infos})
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise FetchDenied(f"host {host!r} resolves to non-public address(es)")
    return host, ips[0]


def fetch(url: str, policy: DomainPolicy, *, client: httpx.Client | None = None) -> FetchedContent:
    """Bounded, policy-checked GET with manual redirect re-validation."""
    own_client = client is None
    client = client or httpx.Client(timeout=policy.timeout_seconds,
                                    headers={"User-Agent": policy.user_agent},
                                    follow_redirects=False)
    current = url
    try:
        for _hop in range(_MAX_HOPS):
            host, pinned_ip = validate_url(current, policy)
            req = client.build_request("GET", current)
            resp = _send_pinned(client, req, pinned_ip, host)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                resp.close()
                if not loc:
                    raise FetchDenied("redirect without location")
                current = httpx.URL(current).join(loc).unicode_string()
                continue
            if resp.status_code >= 400:
                resp.close()
                raise FetchDenied(f"HTTP {resp.status_code} for {current}")
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if ctype not in _ALLOWED_CONTENT_TYPES:
                resp.close()
                raise FetchDenied(f"content type {ctype!r} not allowed")
            body = _read_capped(resp, policy.max_bytes_per_fetch)
            resp.close()
            return FetchedContent(url=url, final_url=current,
                                  status_code=resp.status_code,
                                  content=body, content_type=ctype)
        raise FetchDenied("too many redirects")
    finally:
        if own_client:
            client.close()


def _send_pinned(client: httpx.Client, request: httpx.Request, ip: str,
                 host: str) -> httpx.Response:
    """Force the connection onto the validated IP while keeping real Host/SNI.

    Rewriting only the URL *origin* (not path/query) means a rebinding DNS
    answer obtained after validate_url() can never redirect the socket to an
    internal address; TLS SNI still presents the true hostname via the
    sni_hostname extension, and the Host header is restored explicitly.
    """
    request.url = request.url.copy_with(host=ip)
    request.headers["Host"] = host
    resp = client.send(request, stream=True,
                       extensions={"sni_hostname": host})
    return resp


def _read_capped(resp: httpx.Response, max_bytes: int) -> bytes:
    declared = resp.headers.get("content-length")
    if declared and int(declared) > max_bytes:
        raise FetchDenied(f"declared size {declared} exceeds cap {max_bytes}")
    buf = bytearray()
    for chunk in resp.iter_raw():          # raw: no decoding surprises on cap math
        buf += chunk
        if len(buf) > max_bytes:
            raise FetchDenied("stream exceeded max_bytes_per_fetch")
    return bytes(buf)
