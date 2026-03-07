# Browser lifecycle manager.
# Creates the Playwright browser/context/page with anti-detection friendly defaults,
# and provides controlled shutdown and screenshot helpers.

import os

from playwright.sync_api import Page, sync_playwright

from core.logger import get_logger

logger = get_logger()


class BrowserManager:
    # Owns browser startup, page creation, and cleanup.

    def __init__(self, headless: bool = False, slow_mo: int = 80):
        self.headless = headless
        self.slow_mo = slow_mo
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def start(self) -> Page:
        # Start Playwright and return the active page object.
        logger.info("Starting browser...")
        self.playwright = sync_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]

        # Headed mode: ask Chromium to open in a near full-screen window.
        if not self.headless:
            launch_args.append("--start-maximized")

        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=launch_args,
        )

        # Use real browser window size in headed mode (no fixed Playwright viewport).
        self.context = self.browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )

        # Mask webdriver flag often used by bot-detection scripts.
        self.context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            """
        )

        self.page = self.context.new_page()
        self.page.set_default_timeout(30000)

        logger.info("Browser started successfully")
        return self.page

    def take_screenshot(self, name: str) -> str:
        # Capture a screenshot on the current page and return file path.
        os.makedirs("screenshots", exist_ok=True)
        path = f"screenshots/{name}.png"
        if self.page:
            self.page.screenshot(path=path)
        return path

    def quit(self):
        # Close context, browser, and Playwright runtime in order.
        logger.info("Closing browser...")
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            logger.info("Browser closed")
        except Exception as e:
            logger.warning(f"Error while closing browser: {e}")
