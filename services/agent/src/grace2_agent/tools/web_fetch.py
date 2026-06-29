"""``web_fetch`` atomic tool -- generic web-page ingest with extraction modes.

Registers one atomic tool, ``web_fetch``, that fetches an http/https URL and
returns a structured ``dict`` (one of four extraction modes: ``full_html``,
``main_text``, ``json``, ``metadata``). Unlike the layer-producing fetchers in
``data_fetch.py``, the result is a plain dict -- NOT a ``LayerURI`` -- intended
for the agent's research / event-ingest loop. The 1-hour cache (``dynamic-1h``)
is keyed on ``(canonicalized url, extract, user_agent)``.

Public surface, typed errors, and cross-tool seams are documented on the
``web_fetch`` function docstring below (the tool-catalog description). Internal
helpers below are implementation detail.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

import httpx

from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = [
    "web_fetch",
    "WebFetchError",
    "WebFetchInputError",
    "WebFetchUpstreamError",
]

logger = logging.getLogger("grace2_agent.tools.web_fetch")


# ---------------------------------------------------------------------------
# Typed errors (FR-AS-11).
# ---------------------------------------------------------------------------


class WebFetchError(RuntimeError):
    """Base class for web_fetch failures. ``error_code`` is the A.6 code."""

    error_code: str = "WEB_FETCH_ERROR"
    retryable: bool = True


class WebFetchInputError(WebFetchError):
    """Invalid input to ``web_fetch`` (bad URL, unknown extract mode)."""

    error_code = "WEB_FETCH_INPUT_INVALID"
    retryable = False


class WebFetchUpstreamError(WebFetchError):
    """Upstream HTTP fetch failed or returned an unparseable response."""

    error_code = "WEB_FETCH_UPSTREAM_ERROR"
    retryable = True


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_DEFAULT_USER_AGENT = "grace2-agent/0.1 (research; contact: grace2-ops@local)"
_ALLOWED_EXTRACT_MODES = ("full_html", "main_text", "json", "metadata")

#: Boilerplate tags stripped before the main-text extraction so the result is
#: readable narrative content rather than navigation chrome.
_BOILERPLATE_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript")


# ---------------------------------------------------------------------------
# URL canonicalization for the cache key.
# ---------------------------------------------------------------------------


def _canonicalize_url(url: str) -> str:
    """Return a deterministic canonical form of ``url`` for cache-keying.

    Rules:
        - lowercase scheme + netloc;
        - drop default ports (http:80, https:443);
        - ensure a trailing slash on the root path;
        - keep query string verbatim (order matters to many APIs).

    Raises ``WebFetchInputError`` if the URL has no scheme or host, since the
    underlying ``httpx.get`` would otherwise raise an opaque error.
    """
    if not url or not isinstance(url, str):
        raise WebFetchInputError(f"url must be a non-empty string; got {url!r}")
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        raise WebFetchInputError(
            f"url has no scheme (expected http:// or https://): {url!r}"
        )
    if parsed.scheme not in ("http", "https"):
        raise WebFetchInputError(
            f"unsupported url scheme {parsed.scheme!r}; only http/https are allowed"
        )
    if not parsed.netloc:
        raise WebFetchInputError(f"url has no host: {url!r}")
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    # Strip default ports.
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[: -len(":80")]
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[: -len(":443")]
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


# ---------------------------------------------------------------------------
# HTML extraction helpers.
# ---------------------------------------------------------------------------


def _extract_main_text(html: str) -> tuple[str, str | None, str | None]:
    """Boilerplate-stripped readable text from ``html``.

    Strategy: parse with ``lxml`` via BeautifulSoup, remove all boilerplate
    tags, then preferentially extract from ``<main>`` -> ``<article>`` ->
    ``<body>``. The fallback is the whole soup if none of those land.

    Returns ``(text, title, lang)`` so the caller can populate the result
    dict's top-level fields. ``title`` and ``lang`` come from the original
    soup (before body extraction) so they survive the strip.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None
    if isinstance(lang, list):
        lang = lang[0] if lang else None

    # Strip boilerplate.
    for tag_name in _BOILERPLATE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Preferential extraction.
    container = soup.find("main") or soup.find("article") or soup.find("body") or soup
    text = container.get_text(separator="\n", strip=True)
    # Collapse runs of blank lines.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return ("\n".join(lines), title, lang)


def _extract_metadata(html: str) -> tuple[dict[str, Any], str | None, str | None]:
    """Open Graph + meta-tag dictionary from ``html``.

    Returns ``(metadata_dict, title, lang)``. ``metadata_dict`` includes
    every ``<meta name=*>`` / ``<meta property=*>`` keyed by the attribute
    value, with the meta's ``content`` as the dict value. ``<title>`` and
    ``<html lang>`` are still surfaced separately so the result dict's
    top-level ``title`` / ``lang`` fields are uniformly populated across
    modes.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None
    if isinstance(lang, list):
        lang = lang[0] if lang else None

    metadata: dict[str, Any] = {}
    if title is not None:
        metadata["title"] = title
    if lang is not None:
        metadata["lang"] = lang

    for meta in soup.find_all("meta"):
        # property= is OG/Twitter; name= is everything else (description, etc.).
        key = meta.get("property") or meta.get("name")
        if not key:
            continue
        content = meta.get("content")
        if content is None:
            continue
        metadata[str(key).lower()] = content

    return (metadata, title, lang)


# ---------------------------------------------------------------------------
# Fetch + extract — the body the cache shim calls on miss.
# ---------------------------------------------------------------------------


def _fetch_and_extract_bytes(
    url: str,
    extract: str,
    timeout_s: float,
    user_agent: str,
) -> bytes:
    """Perform the HTTP GET + extraction and return the result dict as JSON bytes.

    The cache shim writes the bytes to the cache verbatim; the tool function
    then decodes them back to a dict before returning to the caller. This keeps
    the cache miss/hit paths symmetric (both return JSON-decodable bytes).
    """
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    try:
        with httpx.Client(
            timeout=timeout_s, follow_redirects=True, headers=headers
        ) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise WebFetchUpstreamError(
            f"web_fetch timed out after {timeout_s}s for url={url!r}: {exc}"
        ) from exc
    except httpx.HTTPError as exc:
        raise WebFetchUpstreamError(
            f"web_fetch HTTP error for url={url!r}: {exc}"
        ) from exc

    status = response.status_code
    if status >= 500:
        raise WebFetchUpstreamError(
            f"web_fetch upstream {status} for url={url!r}"
        )
    if status >= 400:
        # 4xx is a client-input problem (404, 401, etc.) — non-retryable at
        # the same URL. Surface as input error so the agent doesn't retry-loop.
        raise WebFetchInputError(
            f"web_fetch client error {status} for url={url!r}"
        )

    final_url = str(response.url)
    fetched_at = datetime.now(timezone.utc).isoformat()
    body_text = response.text
    content_length = len(response.content)

    content: str | dict[str, Any] | None
    title: str | None = None
    lang: str | None = None

    if extract == "full_html":
        content = body_text
    elif extract == "main_text":
        content, title, lang = _extract_main_text(body_text)
    elif extract == "metadata":
        meta_dict, title, lang = _extract_metadata(body_text)
        content = meta_dict
    elif extract == "json":
        content_type = response.headers.get("Content-Type", "").lower()
        if "json" not in content_type and not content_type.startswith(
            ("application/", "text/")
        ):
            # Strict: refuse to parse non-JSON as JSON.
            raise WebFetchInputError(
                f"web_fetch extract='json' but Content-Type={content_type!r} is not json-ish "
                f"for url={url!r}"
            )
        if "json" not in content_type:
            # text/* or application/* — only proceed if it actually parses.
            pass
        try:
            content = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise WebFetchUpstreamError(
                f"web_fetch could not decode JSON from {url!r}: {exc}"
            ) from exc
    else:
        # Should be filtered upstream; defensive.
        raise WebFetchInputError(
            f"unknown extract mode {extract!r}; allowed: {_ALLOWED_EXTRACT_MODES}"
        )

    result: dict[str, Any] = {
        "url": final_url,
        "status_code": status,
        "fetched_at": fetched_at,
        "extract_mode": extract,
        "content": content,
        "title": title,
        "lang": lang,
        "content_length": content_length,
    }
    return json.dumps(result).encode("utf-8")


# ---------------------------------------------------------------------------
# Registration + public entry point.
# ---------------------------------------------------------------------------


_WEB_FETCH_METADATA = AtomicToolMetadata(
    name="web_fetch",
    ttl_class="dynamic-1h",
    source_class="web_fetch",
    cacheable=True,
)


@register_tool(
    _WEB_FETCH_METADATA,
    # Annotations: readOnlyHint=True (HTTP GET only; no state mutation),
    # openWorldHint=True (fetches arbitrary public URLs; fully open-world),
    # destructiveHint=False, idempotentHint=True (cache shim + TTL deduplicates).
    open_world_hint=True,
)
def web_fetch(
    url: str,
    extract: Literal["full_html", "main_text", "json", "metadata"] = "main_text",
    timeout_s: float = 30.0,
    user_agent: str = _DEFAULT_USER_AGENT,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> dict[str, Any]:
    """Fetch one http/https URL; extract its main text, full HTML, JSON, or metadata. [data-only: returns a dict, NOT a map LayerURI]

    Use this when:
        - Reading a news-article / incident-report body for event ingest.
        - Cheaply confirming a page subject (extract="metadata", meta tags only).
        - Pulling a small public JSON API that has no dedicated fetcher.

    Do NOT use this for:
        - Map layers -- returns text, not geometry; the geospatial fetchers
          (fetch_dem, fetch_landcover, fetch_administrative_boundaries) render.
        - Geocoding a place name -- use geocode_location.
        - Choosing which dataset/endpoint to hit -- use discover_dataset.
        - The full news->event pipeline -- use run_model_news_event_ingest.

    Honesty: a bad URL, 4xx/5xx, or decode failure raises a typed error
    (WebFetchInputError / WebFetchUpstreamError), never a fake success.

    Action: returns a data-only dict (url, status_code, content, title, lang); it
    does NOT publish or render a map layer.

    Params:
        url: absolute http/https URL. Other schemes raise WebFetchInputError.
        extract: "main_text" (default; boilerplate-stripped readable text via
            BeautifulSoup+lxml, strips script/style/nav/header/footer/aside/
            noscript, prefers <main>/<article>), "full_html" (raw body), "json"
            (response.json() after a Content-Type check), or "metadata" (Open
            Graph + <meta> + <title> only; cheapest).
        timeout_s: per-request timeout in seconds. Default 30.0.
        user_agent: User-Agent header; part of the cache key, so a change forces
            a refetch.

    Returns:
        dict: {url (final, post-redirect), status_code, fetched_at (ISO-8601
        UTC), extract_mode, content (str|dict|None), title, lang,
        content_length}. 4xx/5xx never return here -- they raise.

    Caching: result cached as a JSON blob under the dynamic-1h cache prefix,
    keyed on (canonicalized url, extract, user_agent); the 1-hour TTL window is
    the only freshness boundary. (Robots.txt is NOT honored in v0.1.)

    Typed errors (FR-AS-11):
        - WebFetchInputError (not retryable) -- bad URL, unsupported scheme,
          unknown extract mode, 4xx response, or Content-Type mismatch on json.
        - WebFetchUpstreamError (retryable) -- timeout, connection error, 5xx,
          or JSON decode failure.

    Cross-tool dependencies:
        - Downstream: aggregate_claims_across_sources (the returned dict feeds
          the sources list) and run_model_news_event_ingest (calls this per url
          source).
    """
    if extract not in _ALLOWED_EXTRACT_MODES:
        raise WebFetchInputError(
            f"unknown extract mode {extract!r}; allowed: {_ALLOWED_EXTRACT_MODES}"
        )
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise WebFetchInputError(
            f"timeout_s must be a positive number; got {timeout_s!r}"
        )

    canonical_url = _canonicalize_url(url)

    params = {
        "url": canonical_url,
        "extract": extract,
        "user_agent": user_agent,
    }
    result = read_through(
        metadata=_WEB_FETCH_METADATA,
        params=params,
        ext="json",
        fetch_fn=lambda: _fetch_and_extract_bytes(
            canonical_url, extract, timeout_s, user_agent
        ),
    )
    try:
        decoded = json.loads(result.data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # Cache corruption — should not happen, but surface as upstream so
        # the agent can decide to retry (which will force-refresh on next call).
        raise WebFetchUpstreamError(
            f"web_fetch cache entry could not be decoded as JSON: {exc}"
        ) from exc
    if not isinstance(decoded, dict):
        raise WebFetchUpstreamError(
            f"web_fetch cache entry decoded to non-dict ({type(decoded).__name__}); "
            "cache corruption?"
        )
    return decoded
