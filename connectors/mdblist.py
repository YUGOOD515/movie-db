from scripts.fetch_multi_platform_ratings import (
    TitleRatingsSummary,
    fetch_mdblist_ratings,
)


def fetch_title_ratings(title_id: str) -> TitleRatingsSummary:
    """Fetch one movie or series rating summary from MDBList."""
    return fetch_mdblist_ratings(title_id)
