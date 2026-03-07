# Human-like pacing helpers.
# This module introduces random delays so interactions are not perfectly timed.

import random
import time

from core.logger import get_logger

logger = get_logger()


class RateLimiter:
    # Controls wait timing for short and long interaction gaps.

    def __init__(self, min_delay: float = 2.0, max_delay: float = 5.0):
        self.min_delay = min_delay
        self.max_delay = max_delay

    def wait(self):
        # General delay used between larger loop steps.
        delay = random.uniform(self.min_delay, self.max_delay)
        logger.debug(f"Waiting {delay:.1f} seconds...")
        time.sleep(delay)

    def short_wait(self):
        # Short delay used between small actions (click, type, focus).
        time.sleep(random.uniform(0.3, 0.8))

    def long_wait(self):
        # Long delay used between larger transitions (pages, workflows).
        time.sleep(random.uniform(3.5, 6.0))
