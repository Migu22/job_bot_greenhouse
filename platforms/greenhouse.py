# Greenhouse platform automation workflow.
# This module owns end-to-end Greenhouse-specific behavior:
# - login and dashboard navigation
# - search, location, and filters
# - job-card discovery and iteration
# - apply-button handling + form fill + submit (or dry-run simulation)
# - structured step logging for troubleshooting

import time

from playwright.sync_api import Page

from core.form_filler import FormFiller
from core.logger import get_logger
from platforms.base_platform import BasePlatform
from services.application_service import ApplicationService

logger = get_logger()


# Main platform bot implementation.
class Greenhouse(BasePlatform):
    DASHBOARD_URL = "https://my.greenhouse.io/dashboard"

    def __init__(
        self, page: Page, rate_limiter, user_data: dict, dry_run: bool = False
    ):
        super().__init__(None, rate_limiter)
        self.page = page
        self.user_data = user_data
        self.application_limit = user_data.get("application_limit_per_run", 10)
        # Track how many job cards we process this run (attempt cap).
        self.jobs_processed = 0
        self.applications_submitted = 0
        self.visited_urls = set()
        self.form_filler = FormFiller(page, user_data)
        self.app_service = ApplicationService()
        self.dry_run = dry_run
        # During testing, require manual confirmation before submit click.
        self.confirm_before_submit = user_data.get("confirm_before_submit", True)

    def _log_step(self, step: str, status: str, details: str = ""):
        # Write structured step logs to both terminal log and CSV event log.
        msg = f"STEP | {step} | {status}"
        if details:
            msg = f"{msg} | {details}"

        if status in {"FAIL", "ERROR"}:
            logger.error(msg)
        elif status == "WARN":
            logger.warning(msg)
        else:
            logger.info(msg)

        self.app_service.log_event(
            f"step_{step}_{status.lower()}",
            details or "-",
        )

    # Login and navigation
    def navigate_to_dashboard(self) -> bool:
        # Open dashboard URL and branch into login flow when redirected.
        self._log_step("navigate_to_dashboard", "START", self.DASHBOARD_URL)
        try:
            self.page.goto(self.DASHBOARD_URL, wait_until="domcontentloaded")
            time.sleep(2)

            current_url = self.page.url
            if "login" in current_url or "sign_in" in current_url:
                self._log_step("navigate_to_dashboard", "INFO", "redirected_to_login")
                return self._handle_login()

            self._log_step("navigate_to_dashboard", "SUCCESS", "already_logged_in")
            return True
        except Exception as e:
            self._log_step("navigate_to_dashboard", "FAIL", str(e))
            return False

    def _handle_login(self) -> bool:
        # Perform Greenhouse email + security-code login flow.
        self._log_step("login", "START")
        try:
            email = self.user_data.get("greenhouse_email", "")
            if not email:
                self._log_step("login", "FAIL", "missing GREENHOUSE_EMAIL")
                return False

            self.page.wait_for_selector(
                "input[type='email'], input[name='email'], input#email", timeout=15000
            )
            self.page.fill(
                "input[type='email'], input[name='email'], input#email", email
            )
            self.rate_limiter.short_wait()

            sent = False
            for selector in [
                "button:has-text('Send Security Code')",
                "button:has-text('Send Code')",
                "button:has-text('Continue')",
                "input[type='submit']",
                "button[type='submit']",
            ]:
                try:
                    self.page.click(selector, timeout=5000)
                    sent = True
                    break
                except Exception:
                    continue

            if not sent:
                self._log_step("login", "FAIL", "send_security_code_button_not_found")
                return False

            time.sleep(2)
            self.page.wait_for_selector(
                "[data-test-id='security-input-0']", timeout=60000
            )

            print("\n" + "=" * 60)
            print("ACTION REQUIRED: Check your email for the security code.")
            print("=" * 60)

            code = input(
                "Type the 8-character security code here, then press Enter: "
            ).strip()
            if len(code) != 8:
                self._log_step("login", "FAIL", f"invalid_code_length={len(code)}")
                return False

            for i, char in enumerate(code):
                box_selector = f"[data-test-id='security-input-{i}']"
                try:
                    self.page.wait_for_selector(box_selector, timeout=10000)
                    self.page.click(box_selector)
                    time.sleep(0.15)
                    self.page.press(box_selector, char)
                except Exception as e:
                    self._log_step("login", "FAIL", f"security_input_{i}: {e}")
                    return False

            self.rate_limiter.short_wait()

            for selector in [
                "button:has-text('Verify')",
                "button:has-text('Confirm')",
                "button:has-text('Submit')",
                "button:has-text('Sign In')",
                "button[type='submit']",
                "input[type='submit']",
            ]:
                try:
                    self.page.click(selector, timeout=3000)
                    break
                except Exception:
                    continue

            self.page.wait_for_load_state("networkidle")
            time.sleep(3)

            if "dashboard" in self.page.url:
                self._log_step("login", "SUCCESS")
                return True

            self._log_step("login", "FAIL", f"final_url={self.page.url}")
            return False
        except Exception as e:
            self._log_step("login", "ERROR", str(e))
            return False

    def navigate_to_jobs_section(self) -> bool:
        # Navigate from dashboard shell to jobs search section.
        self._log_step("navigate_to_jobs", "START")
        try:
            for selector in [
                "nav a:has-text('Jobs')",
                "a:has-text('Jobs')",
                "a[href*='/job']",
                "li:has-text('Jobs')",
            ]:
                try:
                    self.page.click(selector, timeout=5000)
                    time.sleep(2)
                    self._log_step("navigate_to_jobs", "SUCCESS", selector)
                    return True
                except Exception:
                    continue

            self._log_step("navigate_to_jobs", "WARN", "jobs_nav_not_found")
            return True
        except Exception as e:
            self._log_step("navigate_to_jobs", "FAIL", str(e))
            return False

    # Search + filtering helpers
    def apply_search_filters(self, keyword: str) -> bool:
        # Apply keyword + location, click Search, then apply required filters.
        self._log_step("apply_filters", "START", keyword)
        try:
            self._fill_search_bar(keyword)
            time.sleep(0.8)
            self._fill_location("United States")
            time.sleep(0.5)
            self._click_search_button()
            time.sleep(1.5)

            # Required filters requested by user.
            self._filter_date_posted()
            self._filter_work_type()
            self._filter_employment_type()

            # Re-run search to apply filter changes if needed by UI.
            self._click_search_button()
            time.sleep(2.0)

            self._log_step("apply_filters", "SUCCESS", keyword)
            return True
        except Exception as e:
            self._log_step("apply_filters", "FAIL", f"{keyword}: {e}")
            return False

    def _fill_search_bar(self, keyword: str):
        # Type search keyword into the job search input.
        for selector in [
            "input[type='search']",
            "input[placeholder*='Search']",
            "input[placeholder*='Job title']",
            "input[placeholder*='Keywords']",
            "input[aria-label*='Search']",
            "input[id*='search']",
            "input[name*='search']",
        ]:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=3000):
                    locator.click()
                    locator.fill("")
                    time.sleep(0.2)
                    locator.type(keyword, delay=30)
                    logger.info(f"Search bar filled: '{keyword}'")
                    return
            except Exception:
                continue

        logger.warning("Could not find search bar")

    def _fill_location(self, location: str):
        # Set location filter and select from dropdown suggestions.
        for selector in [
            "input[id^='react-select-'][id$='-input']",
            "input[role='combobox'][aria-autocomplete='list']",
            "[class*='select__control'] input.select__input",
            "input[placeholder*='Location']",
            "input[placeholder*='City, State']",
            "input[placeholder*='City']",
            "input[aria-label*='Location']",
            "input[id*='location']",
            "input[name*='location']",
        ]:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=3000):
                    locator.click()
                    time.sleep(0.2)
                    locator.fill("")
                    time.sleep(0.2)
                    locator.type(location, delay=40)
                    time.sleep(0.9)

                    # Prefer exact option text match first (example: "United States").
                    exact_option = self.page.locator(
                        "[role='option']:has-text('" + location + "'), "
                        "[id*='react-select-'][id*='-option-']:has-text('" + location + "')"
                    ).first

                    suggestions = self.page.locator(
                        "[class*='suggestion'] li, "
                        "[class*='autocomplete'] li, "
                        "[role='option'], "
                        "[id*='react-select-'][id*='-option-'], "
                        "[class*='menu'] [class*='option']"
                    )
                    if exact_option.count() > 0:
                        exact_option.click()
                        self._log_step("location", "SUCCESS", f"selected_exact={location}")
                    elif suggestions.count() > 0:
                        suggestions.first.click()
                        self._log_step("location", "SUCCESS", f"selected_first_option={location}")
                    else:
                        locator.press("Enter")
                        # If the control already shows the requested location, treat as success.
                        selected_chip = self.page.locator(
                            f".select__single-value:has-text('{location}'), [class*='singleValue']:has-text('{location}')"
                        ).first
                        if selected_chip.count() > 0:
                            self._log_step("location", "SUCCESS", f"already_selected={location}")
                        else:
                            self._log_step("location", "WARN", "no_dropdown_option_clicked")
                    return
            except Exception:
                continue

        logger.warning("Could not find location field")

    def _click_search_button(self) -> bool:
        # Find and click the Search button on the jobs page.
        for selector in [
            "button.btn.btn--rounded.btn--large.btn__primary:has-text('Search')",
            "button[type='button']:has-text('Search')",
            "button:has-text('Search')",
            "button[aria-label*='Search']",
            "button[type='submit']:has-text('Search')",
            "[role='button']:has-text('Search')",
        ]:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=2500):
                    locator.click()
                    self._log_step("search", "SUCCESS", selector)
                    return True
            except Exception:
                continue

        # Fallback: press Enter in the search bar if explicit Search button is absent.
        for selector in [
            "input[type='search']",
            "input[placeholder*='Search']",
            "input[aria-label*='Search']",
        ]:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=1500):
                    locator.press("Enter")
                    self._log_step("search", "INFO", "pressed_enter_in_search_field")
                    return True
            except Exception:
                continue

        self._log_step("search", "WARN", "search_trigger_not_found")
        return False

    def _open_filter_dropdown(self, selectors: list) -> bool:
        # Open a filter dropdown panel using fallback button selectors.
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=2500):
                    locator.click()
                    time.sleep(0.6)
                    return True
            except Exception:
                continue
        return False

    def _option_already_selected(self, locator) -> bool:
        # Attempt to detect selected state to avoid toggling off multi-select choices.
        try:
            aria_selected = (locator.get_attribute("aria-selected") or "").lower()
            aria_checked = (locator.get_attribute("aria-checked") or "").lower()
            class_name = (locator.get_attribute("class") or "").lower()
            if aria_selected == "true" or aria_checked == "true":
                return True
            if any(flag in class_name for flag in ["selected", "checked", "active"]):
                return True
        except Exception:
            pass
        return False

    def _select_filter_value(self, text_value: str, for_id: str = "", input_value: str = "") -> bool:
        # Select one filter option with explicit label/checkbox fallbacks.
        # `for_id` targets labels like <label for="past_ten_days">.
        # `input_value` targets checkbox/radio values like "hybrid".

        # 1) Explicit "for" id label path.
        if for_id:
            try:
                label = self.page.locator(f"label[for='{for_id}']").first
                if label.is_visible(timeout=1200):
                    target_input = self.page.locator(f"#{for_id}").first
                    already = False
                    try:
                        already = target_input.is_checked()
                    except Exception:
                        already = self._option_already_selected(label)
                    if not already:
                        label.click()
                        time.sleep(0.25)
                    return True
            except Exception:
                pass

        # 2) Input value path (checkbox/radio + dynamic id suffix labels).
        if input_value:
            for label_selector in [
                f"label[for$='_{input_value}']",
                f"label[for*='_{input_value}']",
                f"label[for*='{input_value}']",
            ]:
                try:
                    label = self.page.locator(label_selector).first
                    if label.is_visible(timeout=1200):
                        for_attr = label.get_attribute("for") or ""
                        already = False
                        if for_attr:
                            try:
                                already = self.page.locator(f"#{for_attr}").first.is_checked()
                            except Exception:
                                already = False
                        if not already:
                            label.click()
                            time.sleep(0.25)
                        return True
                except Exception:
                    continue

            for input_selector in [
                f"input[value='{input_value}']",
                f"input[type='checkbox'][value='{input_value}']",
                f"input[type='radio'][value='{input_value}']",
                f"input[id$='_{input_value}']",
                f"input[id*='_{input_value}']",
            ]:
                try:
                    inp = self.page.locator(input_selector).first
                    if inp.count() > 0:
                        checked = False
                        try:
                            checked = inp.is_checked()
                        except Exception:
                            checked = False
                        if not checked:
                            try:
                                inp.check(force=True)
                            except Exception:
                                inp.click(force=True)
                            time.sleep(0.25)
                        return True
                except Exception:
                    continue

        # 3) Generic text-based path.
        option_selectors = [
            f"label:has-text('{text_value}')",
            f"[role='option']:has-text('{text_value}')",
            f"button:has-text('{text_value}')",
            f"div:has-text('{text_value}')",
            f"span:has-text('{text_value}')",
        ]

        for selector in option_selectors:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=1500):
                    if self._option_already_selected(locator):
                        return True
                    try:
                        locator.click()
                    except Exception:
                        locator.click(force=True)
                    time.sleep(0.25)
                    return True
            except Exception:
                continue

        return False

    def _filter_date_posted(self):
        # Required selection: Date posted -> Within 1 day.
        # Some sessions already carry this filter from prior searches.
        try:
            already_selected = self.page.locator(
                "div.flex.items-center.justify-center.gap-1.w-full:has-text('Date posted (Within 1 day)'), "
                "button:has-text('Date posted (Within 1 day)'), "
                "button:has-text('Date Posted (Within 1 day)'), "
                "div:has-text('Within 1 day')"
            )
            if already_selected.count() > 0 and already_selected.first.is_visible(timeout=1200):
                self._log_step("date_filter", "SUCCESS", "Within 1 day (already_selected)")
                return
        except Exception:
            pass

        dropdown_selectors = [
            "div.flex.items-center.justify-center.gap-1.w-full:has-text('Date posted')",
            "div.flex:has-text('Date posted')",
            "div[class*='items-center']:has-text('Date posted')",
            "button:has-text('Date posted')",
            "button:has-text('Date Posted')",
            "label:has-text('Date Posted')",
            "[aria-label*='Date Posted']",
            "button:has-text('Date')",
        ]

        opened = self._open_filter_dropdown(dropdown_selectors)
        if not opened:
            self._log_step("date_filter", "WARN", "dropdown_not_opened")
            return

        selected = False
        for attempt in range(2):
            selected = (
                self._select_filter_value("Within 1 day", for_id="past_day")
                or self._select_filter_value("Within 1 day", for_id="past_one_day")
                or self._select_filter_value("Within 1 day", for_id="within_1_day")
                or self._select_filter_value("Within 1 day")
                or self._select_filter_value("Within one day")
                or self._select_filter_value("Past day")
                or self._select_filter_value("Last 24 hours")
                or self._select_filter_value("1 day")
            )
            if selected:
                break
            if attempt == 0:
                # Re-open once because this menu can auto-close after interactions.
                self._open_filter_dropdown(dropdown_selectors)
                time.sleep(0.4)

        # Final direct fallback for label-for patterns observed on this UI.
        if not selected:
            for for_id in ["past_day", "past_one_day", "within_1_day"]:
                try:
                    exact_label = self.page.locator(f"label.cursor-pointer.w-full[for='{for_id}']").first
                    if exact_label.is_visible(timeout=1000):
                        exact_label.click()
                        selected = True
                        break
                except Exception:
                    continue

        # XPath fallback shared by user for the "Within 1 day" option.
        if not selected:
            try:
                x_label = self.page.locator("xpath=//*[@id='radix-:rv:']/div/div/div[1]/label").first
                if x_label.is_visible(timeout=1000):
                    x_label.click()
                    selected = True
            except Exception:
                pass

        if selected:
            self._log_step("date_filter", "SUCCESS", "Within 1 day")
            return

        # Last confirmation: if chip text now reflects the target, do not warn.
        try:
            selected_chip = self.page.locator(
                "div:has-text('Date posted (Within 1 day)'), "
                "button:has-text('Date posted (Within 1 day)'), "
                "div:has-text('Within 1 day')"
            ).first
            if selected_chip.count() > 0 and selected_chip.is_visible(timeout=1200):
                self._log_step("date_filter", "SUCCESS", "Within 1 day (chip_text)")
                return
        except Exception:
            pass

        self._log_step("date_filter", "WARN", "option_not_found")

    def _filter_work_type(self):
        # Required selections: Work type -> Hybrid and In-person.
        dropdown_selectors = [
            "div.flex.items-center.justify-center.gap-1.w-full:has-text('Work type')",
            "div.flex:has-text('Work type')",
            "div[class*='items-center']:has-text('Work type')",
            "button:has-text('Work Type')",
            "button:has-text('Work type')",
            "[aria-label*='Work Type']",
        ]
        opened = self._open_filter_dropdown(dropdown_selectors)
        if not opened:
            # XPath fallback provided by user (may be dynamic across sessions).
            try:
                self.page.locator("xpath=//*[@id='radix-:r2n:']/span/div").first.click(timeout=2000)
                time.sleep(0.6)
                opened = True
            except Exception:
                pass

        if not opened:
            self._log_step("work_type_filter", "WARN", "dropdown_not_opened")
            return

        hybrid = (
            self._select_filter_value("Hybrid", input_value="hybrid")
            or self._select_filter_value("Hybrid")
        )
        if not hybrid:
            # Re-open dropdown in case first selection closed the menu.
            self._open_filter_dropdown(dropdown_selectors)
            hybrid = (
                self._select_filter_value("Hybrid", input_value="hybrid")
                or self._select_filter_value("Hybrid")
            )

        in_person = (
            self._select_filter_value("In-person", input_value="in_person")
            or self._select_filter_value("In person", input_value="in_person")
            or self._select_filter_value("In-person")
            or self._select_filter_value("In person")
            or self._select_filter_value("On-site", input_value="onsite")
            or self._select_filter_value("Onsite", input_value="onsite")
        )
        if not in_person:
            # Re-open dropdown in case first selection closed the menu.
            self._open_filter_dropdown(dropdown_selectors)
            in_person = (
                self._select_filter_value("In-person", input_value="in_person")
                or self._select_filter_value("In person", input_value="in_person")
                or self._select_filter_value("In-person")
                or self._select_filter_value("In person")
                or self._select_filter_value("On-site", input_value="onsite")
                or self._select_filter_value("Onsite", input_value="onsite")
            )

        # XPath fallback provided by user for Hybrid / In-person options.
        if not hybrid:
            try:
                hybrid_xpath = self.page.locator("xpath=//*[@id=':r3k:']/div[2]/label").first
                if hybrid_xpath.is_visible(timeout=1400):
                    hybrid_xpath.click()
                    hybrid = True
            except Exception:
                pass

        if not in_person:
            try:
                in_person_xpath = self.page.locator("xpath=//*[@id=':r3k:']/div[3]").first
                if in_person_xpath.is_visible(timeout=1400):
                    in_person_xpath.click()
                    in_person = True
            except Exception:
                pass

        if hybrid and in_person:
            self._log_step("work_type_filter", "SUCCESS", "Hybrid + In-person")
        else:
            self._log_step("work_type_filter", "WARN", "one_or_more_options_missing")

    def _filter_employment_type(self):
        # Required selection: Employment type -> Full time.
        dropdown_selectors = [
            "div.flex.items-center.justify-center.gap-1.w-full:has-text('Employment type')",
            "div.flex:has-text('Employment type')",
            "div[class*='items-center']:has-text('Employment type')",
            "button:has-text('Employment Type')",
            "button:has-text('Employment type')",
            "[aria-label*='Employment Type']",
            "button:has-text('Job Type')",
        ]
        opened = self._open_filter_dropdown(dropdown_selectors)
        if not opened:
            # XPath fallback provided by user (may be dynamic across sessions).
            try:
                self.page.locator("xpath=//*[@id='radix-:r3o:']/span/div").first.click(timeout=2000)
                time.sleep(0.6)
                opened = True
            except Exception:
                pass

        if not opened:
            self._log_step("employment_filter", "WARN", "dropdown_not_opened")
            return

        selected = (
            self._select_filter_value("Full time", input_value="full_time")
            or self._select_filter_value("Full-time", input_value="full_time")
            or self._select_filter_value("Full time")
            or self._select_filter_value("Full Time")
        )
        if not selected:
            # Re-open dropdown in case it auto-closed.
            self._open_filter_dropdown(dropdown_selectors)
            selected = (
                self._select_filter_value("Full time", input_value="full_time")
                or self._select_filter_value("Full-time", input_value="full_time")
                or self._select_filter_value("Full time")
                or self._select_filter_value("Full Time")
            )

        # XPath fallback provided by user for Full time option.
        if not selected:
            try:
                full_time_xpath = self.page.locator("xpath=//*[@id=':r3n:']/div[1]/label").first
                if full_time_xpath.is_visible(timeout=1400):
                    full_time_xpath.click()
                    selected = True
            except Exception:
                pass

        if selected:
            self._log_step("employment_filter", "SUCCESS", "Full time")
        else:
            self._log_step("employment_filter", "WARN", "option_not_found")

    # Job discovery and processing
    def _get_job_cards(self) -> list:
        # Return actionable result cards that have both title and view-job link.
        try:
            self.page.wait_for_selector("[data-provides='search-result']", timeout=12000)
            raw_cards = self.page.query_selector_all("[data-provides='search-result']")
            cards = []
            for card in raw_cards:
                try:
                    has_title = card.query_selector("h4.section-title, h4[title], h4") is not None
                    has_view = card.query_selector(
                        "a:has-text('View job'), a.btn[href*='boards.greenhouse.io'], a[href*='gh_jid='], a[href*='/jobs/']"
                    ) is not None
                    if has_title and has_view:
                        cards.append(card)
                except Exception:
                    continue

            logger.info(f"Found {len(cards)} actionable job cards on this page")
            return cards
        except Exception as e:
            self._log_step("get_job_cards", "FAIL", str(e))
            return []

    def _switch_to_new_page(self, previous_pages: list, timeout_seconds: int = 10, log_if_missing: bool = True) -> bool:
        # Poll until a new tab/window appears (up to timeout_seconds), then switch to it.
        # A single instant check can miss tabs that open slightly later.
        try:
            new_pages = []
            checks = max(1, timeout_seconds * 2)  # poll every 0.5s
            for _ in range(checks):
                current_pages = list(self.page.context.pages)
                new_pages = [pg for pg in current_pages if pg not in previous_pages]
                if new_pages:
                    break
                time.sleep(0.5)

            if not new_pages:
                if log_if_missing:
                    self._log_step("switch_page", "INFO", "no_new_tab_appeared")
                return False

            new_page = new_pages[-1]
            self.page = new_page
            self.form_filler.page = new_page
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass

            # Extra settle for JS-heavy boards pages.
            time.sleep(1.2)
            self._log_step("switch_page", "SUCCESS", f"new_tab={self.page.url}")
            return True
        except Exception as e:
            self._log_step("switch_page", "WARN", str(e))
            return False

    def _close_extra_tabs(self, keep_pages: list):
        # Close stray tabs so View-job popups do not accumulate across jobs.
        try:
            keep = {pg for pg in keep_pages if pg is not None}
            for pg in list(self.page.context.pages):
                if pg in keep:
                    continue
                try:
                    pg.close()
                except Exception:
                    continue
        except Exception:
            pass

    def _ensure_post_viewjob_context(self, listing_url: str, job_url: str = ""):
        # After clicking View job, ensure we moved away from search/listing context.
        # Greenhouse may open either a new tab or same-tab detail page.
        try:
            current = (self.page.url or "").strip()
            if current and current != listing_url and "search" not in current:
                self._log_step("post_view_job", "SUCCESS", f"url={current}")
                return

            # Give same-tab navigation a short chance to complete.
            start = time.time()
            while time.time() - start < 4.0:
                current = (self.page.url or "").strip()
                if current and current != listing_url and "search" not in current:
                    self._log_step("post_view_job", "SUCCESS", f"url={current}")
                    return
                time.sleep(0.4)

            # Last-resort fallback: open job URL directly.
            if job_url:
                self.page.goto(job_url, wait_until="domcontentloaded")
                time.sleep(1.0)
                self._log_step("post_view_job", "INFO", "fallback_goto_job_url")
        except Exception as e:
            self._log_step("post_view_job", "WARN", str(e))

    def _open_application_form(self) -> bool:
        # On job detail pages, click Apply before attempting form fill.
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass

        # High-priority controls seen most often after View job.
        priority_selectors = [
            "xpath=/html/body/main/div/div[1]/div[2]/button",  # Apply
            "xpath=/html/body/main/div/div[3]/div/div[2]/div[1]/button",  # Autofill with MyGreenhouse
            "button:has-text('Autofill with MyGreenhouse')",
            "button:has-text('Find Autofill with MyGreenhouse')",
            "button[aria-label='Apply']",
            "button.btn--pill:has-text('Apply')",
            "button:has-text('Apply for this job')",
        ]

        apply_selectors = [
            # Exact XPath fallbacks provided by user from failing new-tab page.
            "xpath=/html/body/main/div/div[1]/div[2]/button",
            "xpath=/html/body/main/div/div[3]/div/div[2]/div[1]/button",
            "button:has-text('Autofill with MyGreenhouse')",
            "button:has-text('Find Autofill with MyGreenhouse')",
            "button.btn.btn--pill[aria-label='Apply']",
            "button[aria-label='Apply']",
            "button.btn--pill:has-text('Apply')",
            "button:has-text('Apply for this job')",
            "a#apply_button",
            "a:has-text('Apply for this job')",
            "button:has-text('Apply Now')",
            "button:has-text('Apply now')",
            "button:has-text('Start application')",
            "a:has-text('Start application')",
            "button:has-text('Apply')",
            "a:has-text('Apply Now')",
            "a:has-text('Apply now')",
            "a:has-text('Apply')",
            "a[href*='application']",
            "a[href*='apply']",
            "[role='button']:has-text('Apply')",
            "button[type='button']:has-text('Apply')",
        ]

        # Poll for a few seconds because Apply controls often render after page shell load.
        deadline = time.time() + 15
        while time.time() < deadline:
            # Some pages render the Apply CTA inside an iframe.
            targets = [self.page, *list(self.page.frames)]

            # Phase 1: prioritize Apply / Autofill controls.
            for target in targets:
                for selector in priority_selectors:
                    try:
                        locator = target.locator(selector).first
                        if locator.count() == 0:
                            continue
                        if not locator.is_visible(timeout=400):
                            continue

                        try:
                            locator.scroll_into_view_if_needed(timeout=1000)
                        except Exception:
                            pass

                        previous_pages = list(self.page.context.pages)
                        try:
                            locator.click(timeout=1800)
                        except Exception:
                            locator.click(timeout=1800, force=True)
                        time.sleep(1.2)
                        self._switch_to_new_page(previous_pages, timeout_seconds=1, log_if_missing=False)
                        time.sleep(1.0)

                        if self._is_on_application_form():
                            self._log_step("open_application_form", "SUCCESS", f"priority={selector}")
                            return True
                    except Exception:
                        continue

            # Phase 2: broader generic apply controls.
            for target in targets:
                for selector in apply_selectors:
                    try:
                        locator = target.locator(selector).first
                        if locator.count() == 0:
                            continue
                        if not locator.is_visible(timeout=400):
                            continue

                        try:
                            locator.scroll_into_view_if_needed(timeout=1000)
                        except Exception:
                            pass

                        previous_pages = list(self.page.context.pages)
                        try:
                            locator.click(timeout=1800)
                        except Exception:
                            locator.click(timeout=1800, force=True)
                        time.sleep(1.2)

                        # Some Apply flows open another tab/window.
                        self._switch_to_new_page(previous_pages, timeout_seconds=1, log_if_missing=False)

                        # MyGreenhouse autofill pages can take a beat to mount form fields.
                        time.sleep(1.0)

                        if self._is_on_application_form():
                            self._log_step("open_application_form", "SUCCESS", selector)
                            return True

                        # Some flows need an additional beat after click.
                        time.sleep(0.8)
                        if self._is_on_application_form():
                            self._log_step("open_application_form", "SUCCESS", selector)
                            return True
                    except Exception:
                        continue
            time.sleep(0.5)

            # Generic fuzzy CTA fallback for variant job pages.
            for target in [self.page, *list(self.page.frames)]:
                try:
                    candidates = target.locator("button, a, [role='button']")
                    total = min(candidates.count(), 80)
                except Exception:
                    continue

                for i in range(total):
                    try:
                        candidate = candidates.nth(i)
                        if not candidate.is_visible(timeout=150):
                            continue

                        text = (candidate.inner_text() or "").strip().lower()
                        if not text:
                            continue

                        # Positive keywords for application CTAs.
                        positive = any(
                            k in text
                            for k in [
                                "apply",
                                "autofill",
                                "start application",
                                "continue application",
                                "continue",
                                "quick apply",
                                "submit application",
                            ]
                        )
                        # Exclude common non-application controls.
                        negative = any(
                            k in text
                            for k in [
                                "search",
                                "filter",
                                "save search",
                                "sign in",
                                "log in",
                                "cancel",
                                "close",
                            ]
                        )
                        if not positive or negative:
                            continue

                        previous_pages = list(self.page.context.pages)
                        try:
                            candidate.click(timeout=1200)
                        except Exception:
                            candidate.click(timeout=1200, force=True)

                        time.sleep(1.0)
                        self._switch_to_new_page(previous_pages, timeout_seconds=1, log_if_missing=False)

                        if self._is_on_application_form():
                            self._log_step("open_application_form", "SUCCESS", f"fuzzy_text={text[:40]}")
                            return True
                    except Exception:
                        continue

        # Some jobs open directly on application form; allow pass-through.
        if self._is_on_application_form():
            self._log_step("open_application_form", "INFO", "already_on_application_form")
            return True

        self._log_step("open_application_form", "WARN", f"apply_button_not_found url={self.page.url}")
        try:
            self.page.screenshot(path="screenshots/debug_apply_not_found.png")
        except Exception:
            pass
        return False

    def _is_on_application_form(self) -> bool:
        # Guard to ensure filling runs on application form pages only.
        # Accept both question_* layouts and MyGreenhouse profile-field layouts.
        try:
            form_signals = self.page.locator(
                "input[id^='question_'], "
                "textarea[id^='question_'], "
                "input#first_name, input#last_name, input[type='email'], input[type='tel'], "
                "input[name='first_name'], input[name='last_name'], input[name='email'], input[name='phone'], "
                "input[type='file'], "
                "button:has-text('Submit application'), "
                "button:has-text('Submit Application'), "
                "button:has-text('Submit'), "
                "button[type='submit'], "
                "input[type='submit']"
            )
            if form_signals.count() > 0:
                return True

            # URL-level signal for MyGreenhouse application pages.
            if "/applications/" in self.page.url:
                return True
        except Exception:
            pass
        return False


    def _confirm_submit(self, job_title: str) -> bool:
        # Pause before submit so user can review fields in the browser.
        if not self.confirm_before_submit:
            return True

        print("\n" + "=" * 60)
        print("ACTION REQUIRED: Review the application form before submit.")
        print(f"Job: {job_title}")
        print("Type 'submit' to continue, or anything else to skip this job.")
        print("=" * 60)
        choice = input("Your choice: ").strip().lower()
        return choice == "submit"

    def _pause_after_dry_run(self, job_title: str):
        # Keep the form open in dry-run so user can manually review/click before moving on.
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: Dry-run review mode.")
        print(f"Job: {job_title}")
        print("Review this application page now. Press Enter when ready for next job.")
        print("=" * 60)
        input("Press Enter to continue: ")

    def process_jobs(self) -> int:
        # Iterate cards, open jobs, fill forms, submit/simulate, and persist results.
        applied_count = 0
        listing_url = self.page.url

        try:
            job_cards = self._get_job_cards()
            if not job_cards:
                self._log_step("process_jobs", "WARN", "no_job_cards")
                return 0

            for i in range(len(job_cards)):
                if self.jobs_processed >= self.application_limit:
                    self._log_step(
                        "process_jobs",
                        "INFO",
                        f"limit_reached {self.jobs_processed}/{self.application_limit}",
                    )
                    break

                try:
                    job_cards = self._get_job_cards()
                    if i >= len(job_cards):
                        self._log_step("process_jobs", "INFO", "end_of_cards")
                        break

                    card = job_cards[i]
                    job_url = self._get_url_from_card(card)

                    if job_url and (
                        job_url in self.visited_urls
                        or self.app_service.already_applied(job_url)
                    ):
                        self._log_step("process_job", "INFO", f"skip_duplicate {job_url}")
                        continue

                    job_title = self._get_title_from_card(card)
                    self._log_step("process_job", "START", f"{i + 1}: {job_title}")
                    # Count each processed job card toward per-run limit.
                    self.jobs_processed += 1

                    listing_url = self.page.url
                    listing_page = self.page
                    previous_pages = list(self.page.context.pages)

                    if not self._click_view_job(card):
                        if job_url:
                            try:
                                self.page.goto(job_url, wait_until="domcontentloaded")
                                time.sleep(1.2)
                                self._log_step("process_job", "INFO", "view_click_fallback_goto")
                            except Exception:
                                self._log_step("process_job", "FAIL", f"view_click_failed {job_title}")
                                continue
                        else:
                            self._log_step("process_job", "FAIL", f"view_click_failed {job_title}")
                            continue

                    # Skip slow tab polling here; most View job links are same-tab in this flow.
                    # `_ensure_post_viewjob_context` handles same-tab and direct URL fallback.
                    self._ensure_post_viewjob_context(listing_url, job_url)
                    self._close_extra_tabs([self.page, listing_page])

                    # Click Apply on job page before form filling.
                    if not self._open_application_form():
                        self._log_step("process_job", "FAIL", "apply_form_not_opened")
                        self._go_back(listing_url, listing_page)
                        continue

                    application_url = self.page.url
                    if (
                        application_url in self.visited_urls
                        or self.app_service.already_applied(application_url)
                    ):
                        self._log_step(
                            "process_job", "INFO", "skip_duplicate_application_url"
                        )
                        self._go_back(listing_url, listing_page)
                        continue

                    if not self._is_on_application_form():
                        self._log_step("process_job", "FAIL", "not_on_application_form")
                        self._go_back(listing_url, listing_page)
                        continue

                    fill_success = self.form_filler.fill_greenhouse_application()
                    if not fill_success:
                        self._log_step("process_job", "FAIL", f"form_fill_failed {job_title}")
                        if application_url:
                            self.app_service.save_job(
                                {
                                    "title": job_title,
                                    "company": "Greenhouse",
                                    "url": application_url,
                                },
                                status="failed",
                            )
                        self._go_back(listing_url, listing_page)
                        continue

                    safe_title = (
                        job_title[:20]
                        .replace(" ", "_")
                        .replace("/", "_")
                        .replace("\\", "_")
                    )
                    try:
                        self.page.evaluate("window.scrollTo(0, 0)")
                    except Exception:
                        pass
                    self.page.screenshot(
                        path=f"screenshots/before_submit_{safe_title}_{i}.png",
                        full_page=True,
                    )

                    if self.dry_run:
                        self._log_step("submit", "INFO", f"dry_run_skip_submit {job_title}")
                        self._pause_after_dry_run(job_title)
                        try:
                            self.page.evaluate("window.scrollTo(0, 0)")
                        except Exception:
                            pass
                        self.page.screenshot(
                            path=f"screenshots/after_review_{safe_title}_{i}.png",
                            full_page=True,
                        )
                        self.app_service.save_job(
                            {
                                "title": job_title,
                                "company": "Greenhouse",
                                "url": application_url,
                            },
                            status="dry_run",
                        )
                        # Dry-run still counts as a processed outcome.
                        applied_count += 1
                        self.applications_submitted += 1
                        self.visited_urls.add(application_url)
                        self._go_back(listing_url, listing_page)
                        continue

                    if not self._confirm_submit(job_title):
                        self._log_step("submit", "WARN", "manual_skip_before_submit")
                        self.app_service.save_job(
                            {
                                "title": job_title,
                                "company": "Greenhouse",
                                "url": application_url,
                            },
                            status="review_skipped",
                        )
                        self._go_back(listing_url, listing_page)
                        continue

                    submit_success = self.form_filler.submit_application()
                    if submit_success:
                        applied_count += 1
                        self.applications_submitted += 1
                        self.visited_urls.add(application_url)
                        self.app_service.save_job(
                            {
                                "title": job_title,
                                "company": "Greenhouse",
                                "url": application_url,
                            },
                            status="applied",
                        )
                        self._log_step(
                            "submit",
                            "SUCCESS",
                            f"{job_title} {self.applications_submitted}/{self.application_limit}",
                        )
                        try:
                            self.page.wait_for_load_state("domcontentloaded", timeout=12000)
                        except Exception:
                            pass
                        try:
                            self.page.evaluate("window.scrollTo(0, 0)")
                        except Exception:
                            pass
                        self.page.screenshot(
                            path=f"screenshots/after_submit_{safe_title}_{i}.png",
                            full_page=True,
                        )
                    else:
                        self._log_step("submit", "FAIL", f"button_not_found {job_title}")
                        self.app_service.save_job(
                            {
                                "title": job_title,
                                "company": "Greenhouse",
                                "url": application_url,
                            },
                            status="failed",
                        )

                    self.rate_limiter.wait()
                    self._go_back(listing_url, listing_page)

                except Exception as e:
                    self._log_step("process_job", "ERROR", f"job_index={i + 1} {e}")
                    try:
                        self._go_back(listing_url, listing_page)
                    except Exception:
                        pass
                    continue
        except Exception as e:
            self._log_step("process_jobs", "ERROR", str(e))

        return applied_count

    def _get_url_from_card(self, card) -> str:
        # Extract target job URL from a job card element.
        try:
            for selector in [
                "a.btn[href*='boards.greenhouse.io']",
                "a:has-text('View job')",
                "a[href*='gh_jid=']",
                "a[href*='/jobs/']",
                "a[href]",
            ]:
                try:
                    link = card.query_selector(selector)
                    if link:
                        return link.get_attribute("href") or ""
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    def _get_title_from_card(self, card) -> str:
        # Extract human-readable job title from a card.
        try:
            for selector in [
                "h4.section-title",
                "h4[title]",
                "h4",
                ".section-title",
                "[class*='title']",
            ]:
                try:
                    element = card.query_selector(selector)
                    if element:
                        text = (
                            element.get_attribute("title") or element.inner_text() or ""
                        ).strip()
                        if text:
                            return text
                except Exception:
                    continue
        except Exception:
            pass
        return "Unknown Title"

    def _click_view_job(self, card) -> bool:
        # Open job details from a card using robust selector fallbacks.
        try:
            for selector in [
                "a.btn:has-text('View job')",
                "a:has-text('View job')",
                "a[href*='boards.greenhouse.io']",
                "a[href*='gh_jid=']",
                "a[href*='/jobs/']",
                "a[href]",
            ]:
                try:
                    btn = card.query_selector(selector)
                    if not btn:
                        continue

                    href = (btn.get_attribute("href") or "").strip()
                    btn.scroll_into_view_if_needed()
                    time.sleep(0.2)
                    try:
                        btn.click()
                        return True
                    except Exception:
                        # Fallback to direct navigation when click interception occurs.
                        if href:
                            self.page.goto(href, wait_until="domcontentloaded")
                            return True
                except Exception:
                    continue
            return False
        except Exception as e:
            self._log_step("click_view_job", "FAIL", str(e))
            return False

    def _go_back(self, listing_url: str, listing_page=None):
        # Return to results listing page after processing one job.
        # If we are in a popup tab, close it and switch back to listing tab.
        try:
            if listing_page is not None and self.page != listing_page:
                try:
                    self.page.close()
                except Exception:
                    pass
                self.page = listing_page
                self.form_filler.page = listing_page
                try:
                    self.page.bring_to_front()
                except Exception:
                    pass
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                return

            self.page.go_back()
            self.page.wait_for_load_state("domcontentloaded")
            time.sleep(2)
        except Exception:
            try:
                target_page = listing_page if listing_page is not None else self.page
                self.page = target_page
                self.form_filler.page = target_page
                self.page.goto(listing_url, wait_until="domcontentloaded")
                time.sleep(2)
            except Exception as e:
                self._log_step("go_back", "WARN", str(e))

    # BasePlatform compatibility stubs (not used directly in this flow).
    def search_jobs(self, keywords: str, location: str) -> list:
        return []

    def apply_to_job(self, job: dict, user_data: dict) -> bool:
        return False

    # Top-level run loop
    def run(self) -> int:
        # Execute one full run across configured keywords and limits.
        self._log_step("run", "START", f"dry_run={self.dry_run}")
        total_applied = 0

        if not self.navigate_to_dashboard():
            self._log_step("run", "FAIL", "navigate_to_dashboard_failed")
            return 0

        time.sleep(2)
        self.navigate_to_jobs_section()

        for keyword in self.user_data.get("search_keywords", []):
            if self.jobs_processed >= self.application_limit:
                self._log_step("run", "INFO", "application_limit_reached")
                break

            logger.info(f"\n{'=' * 55}")
            logger.info(f"SEARCHING: {keyword}")
            logger.info(
                f"Progress: {self.jobs_processed}/{self.application_limit}"
            )
            logger.info(f"{'=' * 55}")

            # Re-open jobs page each keyword to avoid stale filter widgets.
            self.navigate_to_jobs_section()
            self.apply_search_filters(keyword)
            count = self.process_jobs()
            total_applied += count

            label = "simulated" if self.dry_run else "applied"
            logger.info(f"{label.capitalize()} {count} jobs for keyword: '{keyword}'")
            self.rate_limiter.long_wait()

        self._log_step("run", "SUCCESS", f"total={total_applied}")
        return total_applied
