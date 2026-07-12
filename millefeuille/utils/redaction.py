"""URL and log-message redaction for the Millefeuille.

This module strips or masks sensitive query parameters from URLs and from free-form
log text, so that credentials and tokens are not written to logs. It is a pure
standard-library utility: it does not import from ``domain/`` or ``clients/``.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_SENSITIVE_PARAMS: frozenset[str] = frozenset(
    {"key", "api_key", "token", "access_token", "authorization"}
)


def redact_url(url: str) -> str:
    """Return a copy of the URL with sensitive query values replaced.

    Query parameter names are compared case-insensitively against a fixed set
    of sensitive names; matching values are replaced with ``REDACTED``. If the
    URL has no query string, no query parameter is sensitive, or parsing fails,
    the input is returned unchanged.
    This function never raises: any exception is caught and the original URL
    is returned.

    Args:
        url: A full URL string, typically including a ``?key=value`` query.

    Returns:
        The same URL with sensitive query values redacted, or the original
        string if there is no query, redaction is unnecessary, or on error.
    """
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if not any(name.lower() in _SENSITIVE_PARAMS for name, _ in pairs):
            return url
        redacted_pairs: list[tuple[str, str]] = [
            (name, "REDACTED" if name.lower() in _SENSITIVE_PARAMS else value)
            for name, value in pairs
        ]
        new_query = urlencode(redacted_pairs)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            )
        )
    except Exception:
        return url


def redact_message(message: str) -> str:
    """Return a copy of the message with embedded URLs redacted in-place.

    ``http://`` and ``https://`` substrings are detected with a simple pattern;
    each distinct URL is passed through :func:`redact_url` once, then all
    occurrences of that exact substring in the message are updated. This
    function never raises: any exception is caught and the original message
    is returned.

    Args:
        message: Arbitrary log or user-facing text that may contain URLs.

    Returns:
        The same text with query credentials in detected URLs redacted, or
        the original string on error.
    """
    try:
        matches = re.findall(r"https?://\S+", message)
        if not matches:
            return message
        seen: set[str] = set()
        for url in matches:
            if url in seen:
                continue
            seen.add(url)
            message = message.replace(url, redact_url(url))
        return message
    except Exception:
        return message
