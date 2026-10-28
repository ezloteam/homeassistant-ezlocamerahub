"""RTSP URL helpers.

Camera RTSP URLs from the engine can carry credentials in their userinfo.
They are passed to the stream and ffmpeg verbatim (the engine recorder does
not percent-decode userinfo, so re-encoding would change what the camera
sees) and must never be logged, shown in attributes or put in diagnostics:
use redact_rtsp_url for anything a person can read.
"""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit

REDACTED = "***"


def redact_rtsp_url(url: str) -> str:
    """Return the URL with any userinfo replaced by a placeholder."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    end = len(rest)
    for char in "/?#":
        index = rest.find(char)
        if index != -1:
            end = min(end, index)
    # A password may itself contain '@'; the host starts after the last one
    # before the path.
    authority, path = rest[:end], rest[end:]
    _userinfo, at, host = authority.rpartition("@")
    if not at:
        return url
    return f"{scheme}://{REDACTED}@{host}{path}"


class InvalidStreamUrl(ValueError):
    """Not an rtsp:// or rtsps:// URL with a host."""


def split_rtsp_url(url: str) -> tuple[str, str, str]:
    """Split a pasted URL into (URL without credentials, username, password).

    The username and password come back percent-decoded.
    """
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("rtsp", "rtsps") or not parts.hostname:
        raise InvalidStreamUrl(url)
    userinfo, _at, hostport = parts.netloc.rpartition("@")
    user, _colon, password = userinfo.partition(":")
    bare = parts._replace(netloc=hostport).geturl()
    return bare, unquote(user), unquote(password)


def compose_rtsp_url(url: str, username: str, password: str) -> str:
    """Put percent-encoded credentials into a credential-free RTSP URL."""
    if not username and not password:
        return url
    parts = urlsplit(url)
    userinfo = quote(username, safe="")
    if password:
        userinfo += ":" + quote(password, safe="")
    return parts._replace(netloc=f"{userinfo}@{parts.netloc}").geturl()
