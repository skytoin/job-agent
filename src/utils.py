"""Shared utilities: logging, retries, file helpers."""

import asyncio
import functools
import logging
from datetime import datetime
from pathlib import Path

from rich.logging import RichHandler


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure rich logging for the project."""
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    return logging.getLogger("job-agent")


logger = setup_logging()


def retry_async(max_retries: int = 1, delay: float = 2.0):
    """Decorator to retry async functions on failure."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Retry {attempt + 1}/{max_retries} for {func.__name__}: {e}"
                        )
                        await asyncio.sleep(delay)
            raise last_error

        return wrapper

    return decorator


def ensure_output_dirs() -> None:
    """Create all output directories."""
    dirs = [
        "output/screenshots",
        "output/cover_letters",
        "output/logs",
    ]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)


def timestamp_filename(prefix: str, ext: str = "png") -> str:
    """Generate a timestamped filename like 'prefix_20260316_143022.png'."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{ts}.{ext}"
