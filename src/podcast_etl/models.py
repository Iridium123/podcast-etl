from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def sanitize_filename(title: str) -> str:
    """Sanitize a title for use as a filename across Windows, macOS, and Linux.

    Replaces ': ' with ' - ' before stripping ':' so that 'Ep 1: Title'
    becomes 'Ep 1 - Title' rather than 'Ep 1 Title'. Removes all characters
    forbidden on Windows (\\/:*?"<>|) and collapses extra whitespace.
    """
    name = title.replace(": ", " - ")
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = re.sub(r" {2,}", " ", name)
    return name.strip()


def format_date(published: str | None) -> str | None:
    """Parse a date string (RFC 2822 or ISO 8601) into yyyy-mm-dd format."""
    if not published:
        return None
    try:
        return parsedate_to_datetime(published).strftime("%Y-%m-%d")
    except Exception:
        pass
    try:
        return datetime.fromisoformat(published).strftime("%Y-%m-%d")
    except Exception:
        return None


def episode_basename(podcast_title: str, episode_title: str, published: str | None) -> str:
    """Return the base filename (no extension) for an episode, matching the download naming scheme."""
    date_prefix = format_date(published) or "unknown-date"
    return f"{sanitize_filename(podcast_title)} - {date_prefix} - {sanitize_filename(episode_title)}"


def episode_json_filename(guid: str, raw_title: str | None, published: str | None) -> str:
    """Return the base filename (no extension) for an episode's JSON state file.

    Uses a GUID hash for stability — the filename does not change when titles
    are cleaned or modified in the RSS feed.
    """
    date_prefix = format_date(published) or "unknown-date"
    slug = slugify(raw_title or "")
    if len(slug) > 60:
        cut = slug.rfind("-", 0, 61)
        slug = slug[:cut] if cut > 0 else slug[:60]
    guid_hash = hashlib.sha256(guid.encode()).hexdigest()[:8]
    if slug:
        return f"{date_prefix}-{slug}-{guid_hash}"
    return f"{date_prefix}-{guid_hash}"


@dataclass
class StepStatus:
    completed_at: str
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"completed_at": self.completed_at, "result": self.result}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepStatus:
        return cls(completed_at=data["completed_at"], result=data.get("result", {}))


@dataclass
class Episode:
    title: str
    guid: str
    published: str | None
    audio_url: str | None
    duration: str | None
    description: str | None
    slug: str
    image_url: str | None = None
    episode_number: int | None = None
    raw_title: str | None = None
    status: dict[str, StepStatus | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status_dict = {}
        for step_name, step_status in self.status.items():
            status_dict[step_name] = step_status.to_dict() if step_status else None
        return {
            "title": self.title,
            "guid": self.guid,
            "published": self.published,
            "audio_url": self.audio_url,
            "duration": self.duration,
            "description": self.description,
            "slug": self.slug,
            "image_url": self.image_url,
            "episode_number": self.episode_number,
            "raw_title": self.raw_title,
            "status": status_dict,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Episode:
        status = {}
        for step_name, step_data in data.get("status", {}).items():
            status[step_name] = StepStatus.from_dict(step_data) if step_data else None
        return cls(
            title=data["title"],
            guid=data["guid"],
            published=data.get("published"),
            audio_url=data.get("audio_url"),
            duration=data.get("duration"),
            description=data.get("description"),
            slug=data["slug"],
            image_url=data.get("image_url"),
            episode_number=data.get("episode_number"),
            raw_title=data.get("raw_title"),
            status=status,
        )

    def save(self, podcast_dir: Path, podcast_title: str) -> None:
        episodes_dir = podcast_dir / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        filename = episode_basename(podcast_title, self.title, self.published) + ".json"
        path = episodes_dir / filename
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> Episode:
        data = json.loads(path.read_text())
        return cls.from_dict(data)


@dataclass
class Podcast:
    title: str
    url: str
    description: str | None
    image_url: str | None
    slug: str
    last_fetched: str | None = None
    episodes: list[Episode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "description": self.description,
            "image_url": self.image_url,
            "slug": self.slug,
            "last_fetched": self.last_fetched,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Podcast:
        return cls(
            title=data["title"],
            url=data["url"],
            description=data.get("description"),
            image_url=data.get("image_url"),
            slug=data["slug"],
            last_fetched=data.get("last_fetched"),
        )

    def podcast_dir(self, output_dir: Path) -> Path:
        return output_dir / self.slug

    def save(self, output_dir: Path) -> None:
        podcast_dir = self.podcast_dir(output_dir)
        podcast_dir.mkdir(parents=True, exist_ok=True)
        path = podcast_dir / "podcast.json"
        self.last_fetched = datetime.now().isoformat()
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        for episode in self.episodes:
            episode.save(podcast_dir, self.title)

    @classmethod
    def load(cls, podcast_dir: Path) -> Podcast:
        data = json.loads((podcast_dir / "podcast.json").read_text())
        podcast = cls.from_dict(data)
        episodes_dir = podcast_dir / "episodes"
        if episodes_dir.exists():
            for ep_path in sorted(episodes_dir.glob("*.json")):
                podcast.episodes.append(Episode.load(ep_path))
        return podcast
