# Central logger configuration for the project.
# All modules import `get_logger()` so every message goes to the same sink,
# format, and retention policy.

import os
from loguru import logger

# Ensure log folder exists before writing files.
os.makedirs("logs", exist_ok=True)

# File sink used by all bot modules.
logger.add(
    "logs/job_bot.log",
    rotation="1 MB",
    retention="7 days",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
)


def get_logger():
    # Return the shared logger instance used across the project.
    return logger
