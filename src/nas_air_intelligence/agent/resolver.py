"""Stream discovery and verification, kept separate from the recording core.

The resolver only performs plain, unauthenticated HTTP GETs of public pages and the
resources those pages reference. It does not bypass access restrictions and does not
look for unofficial re-broadcasts: if a page exposes no stream, resolution fails.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from ..ffmpeg import find_binary
from ..util import isoformat, utc_now

USER_AGENT = "Mozilla/5.0 (compatible; NASAirMonitor/0.1; +official-stream-discovery)"
MAX_PAGE_FETCHES = 8
MAX_FETCH_BYTES = 1_500_000

_AUDIO_EXTENSIONS = (".mp3", ".aac", ".m3u8", ".pls", ".m3u", ".ogg", ".opus", ".m4a")
_ASSET_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".woff", ".woff2",
    ".ttf", ".eot", ".map", ".mp4", ".webm",
)  # fmt: skip
_BLOCKED_HOSTS = (
    "facebook.com", "youtube.com", "youtu.be", "twitter.com", "instagram.com",
    "google.com", "googleapis.com", "gstatic.com", "w3.org", "schema.org",
)  # fmt: skip
_URL_RE = re.compile(r"""https?:(?:\\?/){2}[^\s"'<>)\]\\]+(?:\\/[^\s"'<>)\]\\]*)*""")
_STREAMISH_PATH = re.compile(r"/(stream|live|radio|listen|audio|icecast|shoutcast)\b|;stream", re.I)


class ResolveError(RuntimeError):
    pass


@dataclass
class Candidate:
    url: str
    score: int
    origin: str


@dataclass
class VerificationResult:
    ok: bool
    reason: str
    content_type: str | None = None
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    decoded_seconds: float | None = None
    mean_volume_db: float | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ResolvedStream:
    station_name: str
    source_page: str | None
    resolved_stream_url: str | None
    stream_type: str | None
    codec: str | None
    verification_status: str  # verified | failed | unverified
    verified_at: str | None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolvedStream:
        return cls(**data)


class _MediaTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.media: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"audio", "source"} and values.get("src"):
            self.media.append(values["src"] or "")
        for key, value in attrs:
            is_url = bool(value) and (value or "").startswith(("http://", "https://"))
            if (
                is_url
                and key.startswith("data-")
                and ("stream" in key or "audio" in key or "src" in key)
            ):
                self.media.append(value or "")
        if tag == "script" and values.get("src"):
            self.scripts.append(values["src"] or "")


def _clean_url(raw: str) -> str:
    return raw.replace("\\/", "/").replace("\\u0026", "&").replace("&amp;", "&").rstrip(".,;\\")


def _looks_streamish(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if any(parsed.hostname.endswith(host) for host in _BLOCKED_HOSTS):
        return False
    path = parsed.path.lower()
    if path.endswith(_ASSET_EXTENSIONS) or path.endswith(".js") or path.endswith(".html"):
        return False
    return path.endswith(_AUDIO_EXTENSIONS) or bool(_STREAMISH_PATH.search(parsed.path))


def extract_candidates(text: str, base_url: str | None = None) -> list[Candidate]:
    """Pull plausible stream URLs out of HTML, JavaScript config, or JSON text."""
    found: dict[str, Candidate] = {}

    def add(raw: str, origin: str, bonus: int) -> None:
        url = _clean_url(raw.strip())
        if base_url:
            url = urljoin(base_url, url)
        if not _looks_streamish(url) and not (origin == "tag" and url.startswith("http")):
            return
        path = urlparse(url).path.lower()
        score = bonus
        if path.endswith((".mp3", ".aac", ".m3u8")):
            score += 2
        elif path.endswith(_AUDIO_EXTENSIONS):
            score += 1
        if any(word in url.lower() for word in ("radio", "stream", "live")):
            score += 1
        existing = found.get(url)
        if existing is None or existing.score < score:
            found[url] = Candidate(url=url, score=score, origin=origin)

    parser = _MediaTagParser()
    with contextlib.suppress(Exception):  # malformed HTML must never abort discovery
        parser.feed(text)
    for src in parser.media:
        add(src, "tag", 3)
    for match in _URL_RE.finditer(text):
        add(match.group(0), "text", 0)
    return sorted(found.values(), key=lambda c: (-c.score, c.url))


def script_references(html: str, page_url: str) -> list[str]:
    parser = _MediaTagParser()
    with contextlib.suppress(Exception):
        parser.feed(html)
    return [urljoin(page_url, s) for s in parser.scripts]


def api_references(text: str, page_url: str) -> list[str]:
    """JSON/API endpoints referenced by a script that look related to radio streams."""
    refs = []
    for match in re.finditer(
        r"""["'](/[A-Za-z0-9_\-./]*(?:radio|stream|live|player)[A-Za-z0-9_\-./]*)["']""", text, re.I
    ):  # noqa: E501
        path = match.group(1)
        if "api" in path.lower() or path.lower().endswith(".json"):
            refs.append(urljoin(page_url, path))
    return refs


def classify_stream(url: str, content_type: str | None, headers: dict[str, str]) -> str:
    path = urlparse(url).path.lower()
    ctype = (content_type or "").lower()
    lowered = {k.lower(): v for k, v in headers.items()}
    if path.endswith(".m3u8") or "mpegurl" in ctype:
        return "hls"
    server = lowered.get("server", "").lower()
    if "icecast" in server or "ice-" in " ".join(lowered):
        return "icecast"
    if any(key.startswith("icy-") for key in lowered) or "shoutcast" in server:
        return "shoutcast"
    if "aac" in ctype or path.endswith(".aac"):
        return "aac"
    if "mpeg" in ctype or path.endswith(".mp3"):
        return "mp3"
    if "ogg" in ctype or "opus" in ctype:
        return "ogg"
    return "unknown"


def parse_playlist(text: str) -> str | None:
    """First stream URL from a .pls or plain .m3u playlist."""
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("file") and "=" in line:
            line = line.split("=", 1)[1].strip()
        if line.startswith(("http://", "https://")):
            return line
    return None


def default_fetch(url: str, timeout: float = 15.0) -> tuple[str, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        headers = {k: v for k, v in response.headers.items()}
        body = response.read(MAX_FETCH_BYTES)
    charset = "utf-8"
    ctype = headers.get("Content-Type", "")
    if "charset=" in ctype:
        charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
    return body.decode(charset, errors="replace"), headers


def probe_headers(url: str, timeout: float = 10.0) -> tuple[int, dict[str, str]]:
    """Open the URL, read response headers and a small body sample, then close."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Icy-MetaData": "0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        headers = {k: v for k, v in response.headers.items()}
        response.read(2048)
        return response.status, headers


_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
_MEAN_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def verify_stream(
    url: str,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    sample_seconds: float = 6.0,
) -> VerificationResult:
    """Reachable, real (non-silent) audio, decodable by FFmpeg for a short sample."""
    try:
        status, headers = probe_headers(url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return VerificationResult(False, f"unreachable: {exc}")
    content_type = headers.get("Content-Type", "").split(";")[0].strip().lower() or None
    if status >= 400:
        return VerificationResult(False, f"http status {status}", content_type, headers=headers)
    if content_type and (content_type.startswith("text/html") or "json" in content_type):
        return VerificationResult(False, f"not an audio stream ({content_type})", content_type)

    try:
        probe = subprocess.run(
            [find_binary(ffprobe), "-v", "error", "-rw_timeout", "15000000", "-show_streams",
             "-select_streams", "a", "-of", "json", url],
            capture_output=True, text=True, timeout=40, check=False,
        )  # fmt: skip
        streams = json.loads(probe.stdout or "{}").get("streams", [])
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        return VerificationResult(False, f"ffprobe failed: {exc}", content_type, headers=headers)
    if not streams:
        return VerificationResult(False, "no audio stream found", content_type, headers=headers)
    audio = streams[0]
    codec = audio.get("codec_name")
    sample_rate = int(audio["sample_rate"]) if audio.get("sample_rate") else None
    channels = audio.get("channels")

    try:
        sample = subprocess.run(
            [find_binary(ffmpeg), "-hide_banner", "-nostdin", "-rw_timeout", "15000000",
             "-t", str(sample_seconds), "-i", url, "-vn", "-af", "volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=sample_seconds + 45, check=False,
        )  # fmt: skip
    except (subprocess.SubprocessError, OSError) as exc:
        return VerificationResult(
            False, f"ffmpeg sample failed: {exc}", content_type, codec, sample_rate, channels,
            headers=headers,
        )  # fmt: skip
    times = _TIME_RE.findall(sample.stderr)
    decoded = 0.0
    if times:
        h, m, s = times[-1]
        decoded = int(h) * 3600 + int(m) * 60 + float(s)
    mean = _MEAN_RE.search(sample.stderr)
    mean_db = float(mean.group(1)) if mean else None

    common = dict(
        content_type=content_type, codec=codec, sample_rate=sample_rate, channels=channels,
        decoded_seconds=round(decoded, 2), mean_volume_db=mean_db, headers=headers,
    )  # fmt: skip
    if decoded < sample_seconds * 0.6:
        return VerificationResult(False, f"decoded only {decoded:.1f}s of audio", **common)
    if mean_db is None or mean_db < -70.0:
        return VerificationResult(False, "audio is silent or level undetectable", **common)
    return VerificationResult(True, "ok", **common)


class StreamResolver:
    def __init__(
        self,
        fetch: Callable[[str], tuple[str, dict[str, str]]] = default_fetch,
        verifier: Callable[[str], VerificationResult] | None = None,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
    ) -> None:
        self.fetch = fetch
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.verifier = verifier or (lambda u: verify_stream(u, ffmpeg, ffprobe))

    def discover(self, page_url: str) -> list[Candidate]:
        """Ordered stream candidates found on a station page (never verified here)."""
        html, _ = self.fetch(page_url)
        candidates: dict[str, Candidate] = {c.url: c for c in extract_candidates(html, page_url)}
        pending = script_references(html, page_url)[:5]
        fetches = 1
        while pending and fetches < MAX_PAGE_FETCHES:
            ref = pending.pop(0)
            try:
                body, _ = self.fetch(ref)
            except (urllib.error.URLError, OSError, ValueError):
                continue
            fetches += 1
            for cand in extract_candidates(body, ref):
                candidates.setdefault(cand.url, cand)
            if ref.endswith(".js"):
                pending.extend(api_references(body, ref)[:3])
        return sorted(candidates.values(), key=lambda c: (-c.score, c.url))

    def _expand_playlist(self, url: str) -> str:
        if urlparse(url).path.lower().endswith((".pls", ".m3u")):
            try:
                body, _ = self.fetch(url)
            except (urllib.error.URLError, OSError, ValueError):
                return url
            return parse_playlist(body) or url
        return url

    def resolve(
        self,
        station_name: str,
        *,
        page: str | None = None,
        url: str | None = None,
    ) -> ResolvedStream:
        if not page and not url:
            raise ResolveError("a station page or a stream URL is required")
        details: dict[str, Any] = {"attempts": []}
        if url:
            candidates = [Candidate(url=url, score=100, origin="user")]
        else:
            assert page is not None
            try:
                candidates = self.discover(page)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                details["error"] = f"page fetch failed: {exc}"
                return ResolvedStream(station_name, page, None, None, None, "failed", None, details)
            if not candidates:
                details["error"] = "no stream candidates found on the page"
                return ResolvedStream(station_name, page, None, None, None, "failed", None, details)

        for candidate in candidates[:6]:
            stream_url = self._expand_playlist(candidate.url)
            result = self.verifier(stream_url)
            details["attempts"].append(
                {
                    "url": stream_url,
                    "origin": candidate.origin,
                    "ok": result.ok,
                    "reason": result.reason,
                }  # fmt: skip
            )
            if result.ok:
                return ResolvedStream(
                    station_name=station_name,
                    source_page=page,
                    resolved_stream_url=stream_url,
                    stream_type=classify_stream(stream_url, result.content_type, result.headers),
                    codec=result.codec,
                    verification_status="verified",
                    verified_at=isoformat(utc_now()),
                    details={
                        **details,
                        "sample_rate": result.sample_rate,
                        "channels": result.channels,
                        "decoded_seconds": result.decoded_seconds,
                        "mean_volume_db": result.mean_volume_db,
                        "content_type": result.content_type,
                        "icy_name": result.headers.get("icy-name"),
                    },
                )
        details["error"] = "no candidate passed verification"
        return ResolvedStream(station_name, page, None, None, None, "failed", None, details)
