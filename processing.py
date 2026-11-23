"""Single-process memory gate for media work on Render Free."""
from __future__ import annotations

import contextvars
import threading
import uuid

from fastapi import HTTPException

BUSY_MESSAGE = "Another video is being processed. Please wait for it to finish, then try again."

_guard = threading.Lock()
_active_owner: str | None = None
_current_owner: contextvars.ContextVar[str | None] = contextvars.ContextVar("ashuviralcuts_processing_owner", default=None)


def reserve(owner: str | None = None) -> str | None:
    """Reserve the one global video-processing slot, without waiting."""
    global _active_owner
    owner = owner or uuid.uuid4().hex
    with _guard:
        if _active_owner is not None:
            return None
        _active_owner = owner
        return owner


def release(owner: str) -> None:
    """Release a slot only when called by its current owner."""
    global _active_owner
    with _guard:
        if _active_owner == owner:
            _active_owner = None


def active_owner() -> str | None:
    with _guard:
        return _active_owner


def current_owner() -> str | None:
    return _current_owner.get()


def bind(owner: str):
    return _current_owner.set(owner)


def unbind(token) -> None:
    _current_owner.reset(token)


def start(job: str, work, state: dict | None = None, on_busy=None) -> threading.Thread:
    """Run one background job or a child step under its parent's reservation."""
    parent = current_owner()
    owner = parent or reserve(job)
    if owner is None:
        if state is not None:
            state.pop(job, None)
        if on_busy is not None:
            on_busy()
        raise HTTPException(status_code=409, detail=BUSY_MESSAGE)
    owns_slot = parent is None

    def run():
        token = bind(owner)
        try:
            work()
        except Exception:
            # Route workers normally store their own safe diagnostics. This is
            # only the final guard for unexpected failures; never stringify an
            # exception here because it could contain private extractor data.
            if state is not None:
                state[job] = {**state.get(job, {}), "status": "error", "error": "Processing stopped unexpectedly. Please retry."}
        finally:
            unbind(token)
            if owns_slot:
                release(owner)

    try:
        thread = threading.Thread(target=run, daemon=True, name=f"video-job-{job[:8]}")
        thread.start()
    except BaseException:
        if owns_slot:
            release(owner)
        if state is not None:
            state.pop(job, None)
        if on_busy is not None:
            on_busy()
        raise
    return thread


def run_sync(job: str, work, *args, **kwargs):
    """Run an in-thread processing stage under the same global reservation."""
    parent = current_owner()
    owner = parent or reserve(job)
    if owner is None:
        raise HTTPException(status_code=409, detail=BUSY_MESSAGE)
    owns_slot = parent is None
    token = bind(owner)
    try:
        return work(*args, **kwargs)
    finally:
        unbind(token)
        if owns_slot:
            release(owner)


def spawn_child(work, name: str = "video-job-child") -> threading.Thread:
    """Start a sibling step that belongs to the current processing job."""
    owner = current_owner()
    if owner is None:
        raise RuntimeError("A child processing step needs an active job owner.")

    def run():
        token = bind(owner)
        try:
            work()
        finally:
            unbind(token)

    thread = threading.Thread(target=run, daemon=True, name=name)
    thread.start()
    return thread


def clip_length_bounds(minimum=30, maximum=30) -> tuple[float, float]:
    """Validate the supported automatic Shorts duration range (10–120 seconds)."""
    try:
        low = float(minimum)
        high = float(maximum)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Choose a clip length between 10 and 120 seconds.") from error
    if not (0 < low < float("inf") and 0 < high < float("inf")):
        raise ValueError("Choose a clip length between 10 and 120 seconds.")
    if not 10.0 <= low <= 120.0 or not 10.0 <= high <= 120.0:
        raise ValueError("Choose a clip length between 10 and 120 seconds.")
    if low > high:
        raise ValueError("Minimum clip length cannot exceed maximum clip length.")
    return low, high


def fit_clip_range(start, end, duration, minimum=10.0, maximum=120.0) -> tuple[float, float] | None:
    """Center a candidate in the requested window and fit it within the source."""
    try:
        start, end, duration = float(start), float(end), float(duration)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(value == value and abs(value) != float("inf") for value in (start, end, duration)):
        return None
    if not 0 <= start < end or duration <= 0:
        return None
    minimum = max(10.0, min(120.0, float(minimum)))
    maximum = max(minimum, min(120.0, float(maximum)))
    length = min(duration, maximum, max(minimum, end - start))
    center = (start + end) / 2
    start = max(0.0, min(center - length / 2, duration - length))
    end = start + length
    return round(start, 2), round(end, 2)
