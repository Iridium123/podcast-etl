from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from mutagen.mp3 import MP3

from podcast_etl.models import Episode, Podcast, format_date

logger = logging.getLogger(__name__)

REMEMBER_COOKIE_NAME = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d"


class ModifiedUnit3dTracker:
    """Client for a modified UNIT3D tracker using web form upload (supports cover/banner images)."""

    def __init__(
        self,
        url: str,
        announce_url: str,
        defaults: dict[str, Any],
        username: str | None = None,
        password: str | None = None,
        remember_cookie: str | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._remember_cookie = remember_cookie
        self.announce_url = announce_url
        self._defaults = defaults

        if not remember_cookie and not (username and password):
            raise ValueError("Tracker config must specify either 'remember_cookie' or both 'username' and 'password'")

    def _authenticate(self, client: httpx.Client) -> None:
        """Authenticate the client session."""
        if self._remember_cookie:
            client.cookies.set(REMEMBER_COOKIE_NAME, self._remember_cookie, domain=urlparse(self._url).hostname)
            # Make a request to establish the session from the remember cookie
            resp = client.get(f"{self._url}/torrents/create", follow_redirects=True)
            if "login" in str(resp.url):
                raise RuntimeError("Remember cookie is expired or invalid")
            return

        self._login(client)

    def _login(self, client: httpx.Client) -> None:
        """Authenticate via the web login form."""
        login_page = client.get(f"{self._url}/login")
        login_page.raise_for_status()
        token = _extract_csrf_token(login_page.text)

        resp = client.post(
            f"{self._url}/login",
            data={
                "_token": token,
                "username": self._username,
                "password": self._password,
                "remember": "on",
            },
            follow_redirects=False,
        )
        if resp.status_code not in (301, 302):
            raise RuntimeError(f"Login failed: unexpected status {resp.status_code}")

        location = resp.headers.get("location", "")
        if "two-factor-challenge" in location:
            raise RuntimeError("Account has 2FA enabled; use 'remember_cookie' from your browser instead")
        if "login" in location:
            raise RuntimeError("Login failed: redirected back to login page (bad credentials?)")

        # Follow the redirect to complete the session
        client.get(resp.headers["location"], follow_redirects=True)
        logger.debug("Logged in to %s as %s", self._url, self._username)

    def upload(
        self,
        torrent_path: Path,
        episode: Episode,
        podcast: Podcast,
        feed_config: dict[str, Any],
        audio_path: Path | None = None,
        cover_image_override: Path | None = None,
    ) -> dict[str, Any]:
        """Upload a torrent via the web form. Returns tracker metadata."""
        category_id = feed_config.get("category_id")
        type_id = feed_config.get("type_id")
        if category_id is None:
            raise ValueError("Feed config must specify 'category_id' for tracker upload")
        if type_id is None:
            raise ValueError("Feed config must specify 'type_id' for tracker upload")

        name = _build_torrent_name(episode, podcast, feed_config, audio_path)
        description = episode.description or ""
        description_suffix = self._defaults.get("description_suffix")
        if description_suffix:
            description = f"{description}\n\n{description_suffix}" if description else description_suffix

        with httpx.Client(follow_redirects=False, timeout=120) as client:
            self._authenticate(client)

            # GET the create page to get a fresh CSRF token
            create_page = client.get(f"{self._url}/torrents/create", follow_redirects=True)
            create_page.raise_for_status()
            token = _extract_csrf_token(create_page.text)

            fields: dict[str, str] = {
                "_token": token,
                "name": name,
                "description": description,
                "category_id": str(category_id),
                "type_id": str(type_id),
                "imdb": "0",
                "tvdb": "0",
                "tmdb": "0",
                "mal": "0",
                "igdb": "0",
                "stream": "0",
                "sd": "0",
                "anon": str(self._defaults.get("anonymous", 0)),
                "personal_release": str(self._defaults.get("personal_release", 0)),
                "mod_queue_opt_in": str(self._defaults.get("mod_queue_opt_in", 0)),
            }

            files: list[tuple[str, tuple[str, bytes, str]]] = []
            files.append(("torrent", (torrent_path.name, torrent_path.read_bytes(), "application/x-bittorrent")))

            cover_path = cover_image_override or (
                Path(feed_config["cover_image"]) if feed_config.get("cover_image") else None
            )
            if cover_path:
                mime = mimetypes.guess_type(cover_path.name)[0] or "image/jpeg"
                files.append(("torrent-cover", (cover_path.name, cover_path.read_bytes(), mime)))

            banner_image_path = feed_config.get("banner_image")
            if banner_image_path:
                banner = Path(banner_image_path)
                mime = mimetypes.guess_type(banner.name)[0] or "image/jpeg"
                files.append(("torrent-banner", (banner.name, banner.read_bytes(), mime)))

            resp = client.post(
                f"{self._url}/torrents",
                data=fields,
                files=files,
                follow_redirects=False,
            )

            if resp.status_code == 419:
                raise RuntimeError("CSRF token expired during upload")

            if resp.status_code not in (301, 302):
                raise RuntimeError(
                    f"Upload failed: status {resp.status_code} (expected redirect)"
                )

            location = resp.headers.get("location", "")

            # A redirect back to the create page means validation failed.
            # Follow it to extract Laravel's error messages from the HTML.
            if "torrents/create" in location:
                error_page = client.get(location, follow_redirects=True)
                errors = _extract_validation_errors(error_page.text)
                error_msg = "; ".join(errors) if errors else "unknown validation error"
                raise RuntimeError(f"Upload rejected by tracker: {error_msg}")

            torrent_id = _extract_torrent_id(location)
            torrent_url = location if location.startswith("http") else f"{self._url}{location}"

            logger.info("Uploaded torrent to tracker: id=%s url=%s", torrent_id, torrent_url)
            return {"torrent_id": torrent_id, "url": torrent_url}

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ModifiedUnit3dTracker:
        for key in ("url", "announce_url"):
            if not config.get(key):
                raise ValueError(f"Tracker config missing required key {key!r}")
        has_cookie = config.get("remember_cookie")
        has_login = config.get("username") and config.get("password")
        if not has_cookie and not has_login:
            raise ValueError("Tracker config must specify 'remember_cookie' or both 'username' and 'password'")
        return cls(
            url=config["url"],
            username=config.get("username"),
            password=config.get("password"),
            remember_cookie=config.get("remember_cookie"),
            announce_url=config["announce_url"],
            defaults={
                "anonymous": config.get("anonymous", 0),
                "personal_release": config.get("personal_release", 0),
                "mod_queue_opt_in": config.get("mod_queue_opt_in", 0),
                "description_suffix": config.get("description_suffix"),
            },
        )


def _extract_csrf_token(html: str) -> str:
    """Extract the _token value from a Laravel page."""
    match = re.search(r'name="_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'value="([^"]+)"\s+name="_token"', html)
    if not match:
        raise RuntimeError("Could not find CSRF token in page")
    return match.group(1)


def _extract_torrent_id(url: str) -> int | None:
    """Extract torrent ID from a redirect URL like /torrents/123."""
    match = re.search(r"/torrents/(\d+)", url)
    return int(match.group(1)) if match else None


def _extract_validation_errors(html: str) -> list[str]:
    """Extract validation error messages from a Laravel error page."""
    # Laravel renders errors as <li> items inside an alert/error block
    errors = re.findall(r"<li>\s*(.+?)\s*</li>", html)
    if errors:
        # Strip any remaining HTML tags from the messages
        return [re.sub(r"<[^>]+>", "", e).strip() for e in errors]
    # Fallback: look for common error patterns in the page text
    matches = re.findall(r"(?:The |A )\w[\w\s]*(?:field |is )\w[\w\s]*\.", html)
    return matches


def _get_mp3_bitrate(path: Path) -> int:
    """Return the bitrate of an MP3 file in kbps."""
    info = MP3(path).info
    return info.bitrate // 1000


def _build_torrent_name(
    episode: Episode,
    podcast: Podcast,
    feed_config: dict[str, Any],
    audio_path: Path | None = None,
) -> str:
    """Build torrent name: Podcast - Episode [date/MP3-bitrate]."""
    podcast_name = feed_config.get("title_override") or podcast.title
    base = f"{podcast_name} - {episode.title}"

    tag_parts: list[str] = []

    date_str = format_date(episode.published)
    if date_str:
        tag_parts.append(date_str)

    if audio_path:
        bitrate = _get_mp3_bitrate(audio_path)
        tag_parts.append(f"MP3-{bitrate}kbps")

    if tag_parts:
        return f"{base} [{'/'.join(tag_parts)}]"
    return base
