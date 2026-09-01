"""Application-facing TMDB connector backed by the completed CLI module."""

from scripts.fetch_tmdb_metadata import (
    build_media_metadata,
    download_metadata_images,
    fetch_title_cast,
)

__all__ = ["build_media_metadata", "download_metadata_images", "fetch_title_cast"]
