COMPLETION_RATIO = 0.90
REMAINING_THRESHOLD_MS = 2 * 60 * 1000


def is_effectively_completed(
    position_ms: int | None,
    duration_ms: int | None,
) -> bool:
    position = max(int(position_ms or 0), 0)
    duration = max(int(duration_ms or 0), 0)
    if duration <= 0:
        return False
    remaining = max(duration - position, 0)
    return position / duration >= COMPLETION_RATIO or remaining < REMAINING_THRESHOLD_MS
