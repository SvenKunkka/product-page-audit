"""Small, bounded HTTP client with public-address validation and DNS pinning.

No proxy settings, authentication, cookies, or browser session are inherited.
The optional local-development switch is deliberately off by default.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import zlib
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit


class UnsafeURLError(ValueError):
    """A URL cannot be fetched under the configured network policy."""


class FetchError(RuntimeError):
    """The remote resource could not be safely collected."""


_SECRET_KEY = re.compile(
    r"token|secret|password|passwd|credential|signature|authorization|api.?key|"
    r"preview.?key|access.?key|(?:^|_)auth(?:$|_)|(?:^|_)key$|(?:^|_)sig$", re.I
)
_REDIRECTS = {301, 302, 303, 307, 308}


def redact_url(url: str) -> str:
    """Remove credentials/fragments and redact secret-like query values.

    This is for output only. Fetching must continue to use the original query,
    including Shopify preview keys.
    """
    try:
        parts = urlsplit(str(url))
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port is not None:
            host += f":{parts.port}"
        query = urlencode([
            (key, "[REDACTED]" if _SECRET_KEY.search(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ])
        return urlunsplit((parts.scheme, host, parts.path, query, ""))
    except (TypeError, ValueError):
        return "[invalid URL]"


def _normalise_url(url: str) -> str:
    if not isinstance(url, str) or not url or re.search(r"[\x00-\x20\x7f]", url):
        raise UnsafeURLError("URL must be a nonempty URL without whitespace or control characters")
    if "\\" in url:
        raise UnsafeURLError("Backslashes are not allowed in URLs")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"}:
            raise UnsafeURLError("Only http and https URLs are supported")
        if parts.username is not None or parts.password is not None:
            raise UnsafeURLError("Credentials embedded in URLs are not allowed")
        host = parts.hostname
        if not host:
            raise UnsafeURLError("URL must contain a hostname")
        if "%" in host:
            raise UnsafeURLError("Encoded hosts and IPv6 zone identifiers are not allowed")
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        if not host or any(c in host for c in "/?#@"):
            raise UnsafeURLError("Invalid hostname")
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise UnsafeURLError("Invalid port")
        authority = f"[{host}]" if ":" in host else host
        if port is not None:
            authority += f":{port}"
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="%:@!$&'()*+,;=/?-._~")
        return urlunsplit((parts.scheme.lower(), authority, path, query, ""))
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, UnsafeURLError):
            raise
        raise UnsafeURLError("Malformed URL") from exc


def _addresses(host: str, port: int, allow_local: bool) -> list[tuple]:
    if not allow_local and (host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
        raise UnsafeURLError("Local hostnames are not allowed")
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError(f"DNS lookup failed for {host}") from exc
    if not answers:
        raise FetchError(f"DNS lookup returned no addresses for {host}")
    unique = []
    for family, socktype, protocol, _canonname, address in answers:
        try:
            ip = ipaddress.ip_address(address[0].split("%", 1)[0])
        except ValueError as exc:
            raise UnsafeURLError("DNS returned an invalid address") from exc
        # Reject an entire mixed public/private DNS answer, not just the private
        # entry. IPv4-mapped IPv6 must satisfy the IPv4 policy too.
        effective = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
        if not allow_local and (
            not effective.is_global or effective.is_multicast or effective.is_unspecified
            or effective.is_reserved or effective.is_loopback or effective.is_link_local
        ):
            raise UnsafeURLError("URL resolves to a non-public network address")
        item = (family, socktype, protocol, address)
        if item not in unique:
            unique.append(item)
    return unique


def validate_url(url: str, allow_local: bool = False) -> str:
    """Validate syntax and every current DNS answer; return a canonical URL.

    Validation alone does not pin subsequent browser connections. SafeFetcher
    separately pins each actual HTTP connection to the checked address.
    """
    normalised = _normalise_url(url)
    parts = urlsplit(normalised)
    _addresses(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), allow_local)
    return normalised


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, timeout, addresses):
        super().__init__(host, port, timeout=timeout)
        self._pinned_addresses = addresses

    def connect(self):
        last_error = None
        for family, socktype, protocol, address in self._pinned_addresses:
            candidate = socket.socket(family, socktype, protocol)
            candidate.settimeout(self.timeout)
            try:
                candidate.connect(address)
                self.sock = candidate
                return
            except OSError as exc:
                last_error = exc
                candidate.close()
        raise OSError("All validated addresses failed to connect") from last_error


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def connect(self):
        super().connect()
        try:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
        except Exception:
            self.close()
            raise


class SafeFetcher:
    """Public-only HTTP(S) collection with bounded responses and redirects."""

    def __init__(self, timeout=15, max_bytes=4_000_000, allow_hosts=None, allow_local=False):
        if timeout <= 0 or max_bytes <= 0:
            raise ValueError("timeout and max_bytes must be positive")
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        self.allow_local = bool(allow_local)
        self.allow_hosts = (
            {str(host).lower().rstrip(".").encode("idna").decode("ascii") for host in allow_hosts}
            if allow_hosts is not None else None
        )

    def _request(self, url: str, method: str, addresses: list[tuple]) -> dict:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        connection_cls = _PinnedHTTPSConnection if parts.scheme == "https" else _PinnedHTTPConnection
        connection = connection_cls(parts.hostname, port, self.timeout, addresses)
        path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            connection.request(method, path, headers={
                "User-Agent": "ProductPageAudit/1.0 (+read-only product page QA)",
                "Accept": "*/*", "Accept-Encoding": "identity", "Connection": "close",
            })
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            declared_length = headers.get("content-length", "")
            if method != "HEAD" and declared_length.isdigit() and int(declared_length) > self.max_bytes:
                raise FetchError("Response exceeds the configured byte limit")
            body = b"" if method == "HEAD" else response.read(self.max_bytes + 1)
            if len(body) > self.max_bytes:
                raise FetchError("Response exceeds the configured byte limit")
            encoding = headers.get("content-encoding", "identity").strip().lower()
            if body and encoding not in {"", "identity"}:
                if encoding not in {"gzip", "deflate"}:
                    raise FetchError(f"Unsupported HTTP content encoding: {encoding}")
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
                body = decoder.decompress(body, self.max_bytes + 1)
                if len(body) > self.max_bytes or decoder.unconsumed_tail:
                    raise FetchError("Decompressed response exceeds the configured byte limit")
                if not decoder.eof:
                    raise FetchError("Incomplete compressed HTTP response")
                headers.pop("content-encoding", None)
                headers.pop("content-length", None)
            return {"url": url, "status": response.status, "headers": headers, "body": body}
        except (OSError, http.client.HTTPException, zlib.error) as exc:
            raise FetchError(f"HTTP collection failed for {redact_url(url)}: {type(exc).__name__}") from exc
        finally:
            connection.close()

    def fetch(self, url, method="GET", *, follow_redirects=True, url_guard=None) -> dict:
        """Fetch read-only evidence; an optional guard must approve every hop.

        url_guard receives the canonical URL and returns True to permit it.
        It can enforce a narrower scope, such as same-origin non-action links.
        """
        method = method.upper()
        if method not in {"GET", "HEAD"}:
            raise ValueError("Only read-only GET and HEAD methods are supported")
        current = _normalise_url(url)
        seen = set()
        for _hop in range(6):
            if url_guard is not None and not url_guard(current):
                raise UnsafeURLError("URL is outside the request-specific collection scope")
            parts = urlsplit(current)
            if self.allow_hosts is not None and parts.hostname not in self.allow_hosts:
                raise UnsafeURLError("Hostname is outside the configured allowlist")
            if current in seen:
                raise FetchError("Redirect loop detected")
            seen.add(current)
            addresses = _addresses(
                parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), self.allow_local
            )
            response = self._request(current, method, addresses)
            if response["status"] not in _REDIRECTS:
                return response
            location = response["headers"].get("location")
            if not location:
                return response
            destination = _normalise_url(urljoin(current, location))
            if not follow_redirects:
                if url_guard is not None and not url_guard(destination):
                    raise UnsafeURLError("Redirect is outside the request-specific collection scope")
                target = urlsplit(destination)
                if self.allow_hosts is not None and target.hostname not in self.allow_hosts:
                    raise UnsafeURLError("Redirect hostname is outside the configured allowlist")
                _addresses(target.hostname, target.port or (443 if target.scheme == "https" else 80), self.allow_local)
                return response
            current = destination
        raise FetchError("Redirect limit exceeded")
