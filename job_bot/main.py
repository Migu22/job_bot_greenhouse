# Application entry point.
# This module wires together services, browser lifecycle, and platform automation.
# It supports both dry-run and live-run execution modes.

import argparse
import os
import sys
from pathlib import Path

# Allow running as either module (`python -m job_bot.main`) or script (`python job_bot/main.py`).
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.browser import BrowserManager
from core.logger import get_logger
from core.rate_limiter import RateLimiter
from platforms.greenhouse import Greenhouse
from services.application_service import ApplicationService
from services.dashboard_service import DashboardService

logger = get_logger()


def parse_args() -> argparse.Namespace:
    # Parse command-line flags that control runtime behavior.
    parser = argparse.ArgumentParser(description="Greenhouse Auto-Apply Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full flow but do not submit applications.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode.",
    )
    return parser.parse_args()


def main():
    # Run one bot session from startup to summary output.
    args = parse_args()

    # Startup banner + mode marker.
    logger.info("=" * 60)
    logger.info(" GREENHOUSE AUTO-APPLY BOT - STARTING")
    logger.info("=" * 60)
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE RUN'}")

    # Required runtime folder for screenshots.
    os.makedirs("screenshots", exist_ok=True)

    # Load user config and initialize local services.
    app_service = ApplicationService()
    dashboard = DashboardService()
    user_data = app_service.load_user_data()

    # Hard-stop if resume path cannot be resolved.
    if not user_data.get("resume_path"):
        logger.error(
            "resume_path is missing (set RESUME_PATH in .env or resume_path in user_data.json). Stopping."
        )
        return

    # Print core run context in logs.
    logger.info(
        f"Running as: {user_data.get('first_name')} {user_data.get('last_name')}"
    )
    logger.info(f"Application limit: {user_data.get('application_limit_per_run', 10)}")
    logger.info(f"Keywords: {user_data.get('search_keywords', [])}")

    # Start browser and pacing helpers.
    browser = BrowserManager(headless=args.headless, slow_mo=80)
    page = browser.start()
    rate_limiter = RateLimiter(min_delay=2.0, max_delay=5.0)

    try:
        # Hand off automation to platform-specific bot.
        bot = Greenhouse(page, rate_limiter, user_data, dry_run=args.dry_run)
        total_processed = bot.run()

        logger.info(f"\n{'=' * 60}")
        if args.dry_run:
            logger.info(f" DONE - Simulated {total_processed} jobs this run")
            app_service.log_event(
                "run_complete_dry",
                f"Simulated {total_processed} jobs",
            )
        else:
            logger.info(f" DONE - Applied to {total_processed} jobs this run")
            app_service.log_event("run_complete", f"Applied to {total_processed} jobs")
        logger.info(f"{'=' * 60}")

    except KeyboardInterrupt:
        logger.info("\nBot stopped manually by user (Ctrl+C)")

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        app_service.log_event("fatal_error", str(e))

    finally:
        # Always close browser and print final dashboard summary.
        browser.quit()
        dashboard.show_summary()

        logger.info("Bot finished. Check:")
        logger.info("  logs/job_bot.log     - full activity log")
        logger.info("  data/jobs.csv        - all applications recorded")
        logger.info("  screenshots/         - before/after screenshots")


if __name__ == "__main__":
    main()
