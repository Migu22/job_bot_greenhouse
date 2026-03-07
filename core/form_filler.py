# Form automation helpers.
# This module encapsulates application form interactions so platform workflow
# code can call high-level methods (fill + submit) without selector retry logic
# everywhere.

import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from core.logger import get_logger

logger = get_logger()


class FormFiller:
    # Fill and submit application forms using resilient selector fallbacks.

    def __init__(self, page: Page, user_data: dict):
        self.page = page
        self.user_data = user_data
        self.answer_memory_path = "data/custom_answer_memory.json"
        self.answer_memory = self._load_answer_memory()

    # Safe low-level element actions
    def _safe_fill(self, selectors: list, value: str, only_if_empty: bool = True, warn_on_fail: bool = False) -> bool:
        # Fill a text input using selector fallbacks.
        # When only_if_empty=True, prefilled fields are left unchanged.
        if not value:
            return False

        found_candidate = False
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                locator.wait_for(state="visible", timeout=2500)
                locator.scroll_into_view_if_needed()
                time.sleep(0.1)
                found_candidate = True

                if only_if_empty:
                    current_value = ""
                    try:
                        current_value = (locator.evaluate("el => el.value || ''") or "").strip()
                    except Exception:
                        try:
                            current_value = (locator.input_value() or "").strip()
                        except Exception:
                            current_value = ""
                    if current_value:
                        logger.info(f"Field auto-filled; leaving as-is: {selector}")
                        return True

                locator.click()
                locator.click(click_count=3)
                locator.fill(value)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue

        if found_candidate and warn_on_fail:
            logger.warning(f"Could not fill field. Value was: {value}")
        elif found_candidate:
            logger.info(f"Could not fill optional field (continuing): {value}")
        else:
            logger.info(f"Field not present on this form variant (skipping): {value}")
        return False

    def _safe_click(self, selectors: list, warn_on_fail: bool = False) -> bool:
        # Click an element using selector fallbacks.
        found_candidate = False
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                locator.wait_for(state="attached", timeout=2500)
                locator.scroll_into_view_if_needed()
                time.sleep(0.1)
                found_candidate = True
                locator.click()
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue

        if found_candidate and warn_on_fail:
            logger.warning("Could not click element")
        elif found_candidate:
            logger.info("Optional click target not activated (continuing)")
        else:
            logger.info("Optional click target not present on this form variant")
        return False

    def _safe_select(self, selectors: list, value: str, warn_on_fail: bool = False) -> bool:
        # Select dropdown option by label first, then by value.
        found_candidate = False
        for selector in selectors:
            locator = self.page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=2500)
                found_candidate = True
                locator.select_option(label=value)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                try:
                    locator.wait_for(state="visible", timeout=2500)
                    found_candidate = True
                    locator.select_option(value=value)
                    return True
                except Exception:
                    continue

        if found_candidate and warn_on_fail:
            logger.warning(f"Could not select: {value}")
        elif found_candidate:
            logger.info(f"Optional select not set (continuing): {value}")
        else:
            logger.info(f"Optional select not present on this form variant: {value}")
        return False

    def _upload_file(self, selectors: list, file_path: str, warn_on_fail: bool = False, file_label: str = "File") -> bool:
        # Upload a local file to file-input fields.
        if not file_path or not os.path.exists(file_path):
            logger.error(f"{file_label} not found at: {file_path}")
            logger.error("Please update the corresponding path in your config (.env or user_data.json)")
            return False

        found_candidate = False
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                locator.wait_for(state="attached", timeout=2500)
                found_candidate = True
                locator.set_input_files(file_path)
                logger.info(f"{file_label} uploaded: {file_path}")
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue

        if found_candidate and warn_on_fail:
            logger.warning(f"Could not upload {file_label.lower()}")
        elif found_candidate:
            logger.info(f"{file_label} upload control found but could not set file (continuing)")
        else:
            logger.info(f"{file_label} upload field not present or already handled by autofill")
        return False

    def _load_answer_memory(self) -> dict:
        # Load persisted question->answer mappings from disk.
        try:
            path = Path(self.answer_memory_path)
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_answer_memory(self):
        # Persist in-session learned answers to disk.
        try:
            path = Path(self.answer_memory_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self.answer_memory, indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _remember_custom_answer(self, question_text: str, answer: str):
        # Store non-empty answer for future auto-fill on similar questions.
        q = (question_text or "").strip().lower()
        a = (answer or "").strip()
        if not q or not a:
            return
        if len(a) > 500:
            return

        # Keep memory small and stable.
        self.answer_memory[q] = a
        if len(self.answer_memory) > 500:
            # Drop oldest inserted key by recreating dict from tail.
            items = list(self.answer_memory.items())[-500:]
            self.answer_memory = dict(items)
        self._save_answer_memory()

    def _location_fit_answer(self, question_text: str) -> str:
        # Answer location/onsite compatibility questions based on Florida-only preference.
        q = (question_text or "").lower()
        if not q:
            return ""

        # Permanent location text fields should return explicit city/state string.
        if "permanent" in q and "location" in q and ("city" in q or "state" in q):
            city = str(self.user_data.get("city", "")).strip()
            state = str(self.user_data.get("state", "")).strip()
            if city and state:
                return f"{city}, {state}"

        location_intent = any(
            k in q
            for k in [
                "are you local",
                "currently located",
                "able to work from",
                "work from our",
                "onsite",
                "on-site",
                "in-person",
                "office",
                "commute",
            ]
        )
        if not location_intent:
            return ""

        florida_tokens = [
            "florida",
            " fl ",
            "fl,",
            "miami",
            "orlando",
            "tampa",
            "jacksonville",
            "hollywood",
            "fort lauderdale",
            "boca",
        ]
        non_florida_tokens = [
            "sf bay area",
            "bay area",
            "oakland",
            "san francisco",
            "california",
            " ca ",
            "ca,",
            "new york",
            "nyc",
            "seattle",
            "austin",
            "sterling, va",
            "virginia",
            "va ",
            "va,",
        ]

        if any(tok in q for tok in florida_tokens):
            return "Yes"
        if any(tok in q for tok in non_florida_tokens):
            return "No"
        return ""

    def _normalize_text_for_match(self, text: str) -> str:
        # Normalize text for resilient keyword matching across punctuation/case variants.
        t = (text or "").lower()
        t = re.sub(r"[^a-z0-9]+", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _lookup_custom_answer(self, question_text: str) -> str:
        # Return auto-answer for a custom question based on rules + config + memory.
        q_raw = question_text or ""
        q = self._normalize_text_for_match(q_raw)

        # Rule-based location answer should override broad generic templates.
        location_answer = self._location_fit_answer(q_raw)
        if location_answer:
            return location_answer

        def _best_match(mapping: dict) -> str:
            if not isinstance(mapping, dict) or not mapping:
                return ""

            # 1) Direct normalized substring match first.
            for key, answer in mapping.items():
                try:
                    a = str(answer).strip()
                    k = self._normalize_text_for_match(str(key))
                    if a and k and (k in q or q in k):
                        return a
                except Exception:
                    continue

            # 2) Token-overlap fallback for wording variants.
            q_tokens = set(q.split())
            best_answer = ""
            best_score = 0.0
            for key, answer in mapping.items():
                try:
                    a = str(answer).strip()
                    k = self._normalize_text_for_match(str(key))
                    if not a or not k:
                        continue
                    k_tokens = set(k.split())
                    if not k_tokens:
                        continue
                    overlap = len(q_tokens & k_tokens)
                    score = overlap / max(1, len(k_tokens))
                    # Require meaningful overlap to avoid false positives.
                    if overlap >= 2 and score > best_score:
                        best_score = score
                        best_answer = a
                except Exception:
                    continue

            if best_score >= 0.6:
                return best_answer
            return ""

        # Prefer explicit custom_answers file mappings, then learned memory.
        custom = self.user_data.get("custom_answers", {}) or {}
        ans = _best_match(custom)
        if ans:
            return ans

        memory = self.answer_memory if isinstance(self.answer_memory, dict) else {}
        ans = _best_match(memory)
        if ans:
            return ans

        return ""

    # High-level form workflow
    def fill_greenhouse_application(self) -> bool:
        # Fill commonly expected Greenhouse application fields.
        # Prefilled fields from MyGreenhouse are left untouched.
        try:
            time.sleep(1.5)
            logger.info("Filling application form...")

            self._safe_fill(
                [
                    "input#first_name",
                    "input[name='first_name']",
                    "input[placeholder*='First']",
                    "label:has-text('First Name') + input",
                    "label:has-text('First') + input",
                ],
                self.user_data.get("first_name", ""),
            )

            self._safe_fill(
                [
                    "input#last_name",
                    "input[name='last_name']",
                    "input[placeholder*='Last']",
                    "label:has-text('Last Name') + input",
                ],
                self.user_data.get("last_name", ""),
            )

            self._safe_fill(
                [
                    "input[name*='preferred']",
                    "input[id*='preferred']",
                    "label:has-text('Preferred') + input",
                ],
                self.user_data.get("first_name", ""),
            )

            self._safe_fill(
                [
                    "input[type='email']",
                    "input#email",
                    "input[name='email']",
                    "input[placeholder*='Email']",
                    "label:has-text('Email') + input",
                ],
                self.user_data.get("email", ""),
            )

            self._safe_fill(
                [
                    "input[type='tel']",
                    "input#phone",
                    "input[name='phone']",
                    "input[placeholder*='Phone']",
                    "label:has-text('Phone') + input",
                ],
                self.user_data.get("phone", ""),
            )

            city = self.user_data.get("city", "")
            state = self.user_data.get("state", "")
            location_string = f"{city}, {state}" if city and state else city
            self._safe_fill(
                [
                    "input#location",
                    "input[name='location']",
                    "input[placeholder*='City']",
                    "input[placeholder*='Location']",
                    "label:has-text('Location') + input",
                ],
                location_string,
            )

            resume_path = self.user_data.get("resume_path", "")
            # Resume upload: avoid generic file inputs first so we do not hit cover-letter fields.
            resume_uploaded = self._upload_file(
                [
                    "input#resume",
                    "input[name='resume']",
                    "input[name*='resume']",
                    "input[id*='resume']",
                    "input[aria-label*='Resume'][type='file']",
                    "label:has-text('Resume') + input[type='file']",
                    "label:has-text('CV') + input[type='file']",
                    "label:has-text('Curriculum Vitae') + input[type='file']",
                ],
                resume_path,
                file_label="Resume",
            )

            cover_letter_path = self.user_data.get("cover_letter_path", "")
            # Cover-letter upload: targeted selectors so resume and cover do not get swapped.
            cover_uploaded = self._upload_file(
                [
                    "input[name='cover_letter']",
                    "input[name*='cover'][type='file']",
                    "input[id*='cover'][type='file']",
                    "input[aria-label*='Cover'][type='file']",
                    "label:has-text('Cover Letter') + input[type='file']",
                    "label:has-text('Cover letter') + input[type='file']",
                ],
                cover_letter_path,
                file_label="Cover letter",
            )

            # Controlled fallback: use generic single file input only when there is exactly one upload field.
            if not resume_uploaded and resume_path:
                try:
                    file_inputs = self.page.locator("input[type='file']")
                    if file_inputs.count() == 1:
                        self._upload_file(["input[type='file']"], resume_path, file_label="Resume")
                except Exception:
                    pass

            if not cover_uploaded and cover_letter_path:
                logger.info("Cover letter field not detected on this form variant (continuing)")

            self._safe_fill(
                [
                    "input[placeholder*='LinkedIn']",
                    "input[id*='linkedin']",
                    "input[name*='linkedin']",
                    "label:has-text('LinkedIn') + input",
                ],
                self.user_data.get("linkedin", ""),
            )

            self._safe_fill(
                [
                    "input[placeholder*='GitHub']",
                    "input[id*='github']",
                    "input[name*='github']",
                    "label:has-text('GitHub') + input",
                ],
                self.user_data.get("github", ""),
            )

            self._safe_fill(
                [
                    "input[placeholder*='Website']",
                    "input[placeholder*='Portfolio']",
                    "input[id*='website']",
                    "label:has-text('Website') + input",
                    "label:has-text('Portfolio') + input",
                ],
                self.user_data.get("portfolio", ""),
            )

            self._handle_work_authorization()
            self._handle_sponsorship()
            self._handle_custom_questions()
            self._scan_and_fill_question_fields()
            self._prompt_for_open_ended_questions()

            logger.info("Form filled successfully")
            return True
        except Exception as e:
            logger.error(f"Form fill error: {e}")
            return False

    def _handle_work_authorization(self):
        # Answer work authorization using radio and select fallbacks.
        try:
            answer = self.user_data.get("work_authorization", "Yes")
            self._safe_click(
                [
                    f"input[type='radio'][value='{answer}']",
                    f"input[type='radio'][value='{answer.lower()}']",
                    f"label:has-text('{answer}') input[type='radio']",
                ]
            )
            self._safe_select(
                [
                    "select[id*='authorization']",
                    "select[name*='authorization']",
                    "select[id*='work_auth']",
                ],
                answer,
            )
        except Exception:
            pass

    def _handle_sponsorship(self):
        # Answer sponsorship question using radio/select fallbacks.
        try:
            answer = self.user_data.get("require_sponsorship", "No")
            self._safe_click(
                [
                    f"input[type='radio'][value='{answer}']",
                    f"input[type='radio'][value='{answer.lower()}']",
                    f"label:has-text('{answer}') input[type='radio']",
                ]
            )
            self._safe_select(
                [
                    "select[id*='sponsorship']",
                    "select[name*='sponsorship']",
                ],
                answer,
            )
        except Exception:
            pass

    def _handle_custom_questions(self):
        # Fill optional short custom fields with known profile values.
        try:
            self._safe_fill(
                [
                    "input[placeholder*='years']",
                    "input[placeholder*='Years']",
                    "input[id*='years']",
                    "label:has-text('Years of Experience') + input",
                ],
                self.user_data.get("years_of_experience", ""),
            )

            self._safe_fill(
                [
                    "input[placeholder*='salary']",
                    "input[placeholder*='Salary']",
                    "input[id*='salary']",
                    "label:has-text('Salary') + input",
                ],
                self.user_data.get("salary_expectation", ""),
            )

            self._safe_fill(
                [
                    "input[placeholder*='title']",
                    "input[id*='title']",
                    "label:has-text('Current Title') + input",
                ],
                self.user_data.get("current_title", ""),
            )
        except Exception:
            pass

    def _scan_and_fill_question_fields(self):
        # Scan question_* inputs on application pages and fill known profile data
        # only when the field is currently empty.
        profile_rules = [
            (["first name"], self.user_data.get("first_name", "")),
            (["last name"], self.user_data.get("last_name", "")),
            (["preferred first name", "preferred name"], self.user_data.get("first_name", "")),
            (["email"], self.user_data.get("email", "")),
            (["phone"], self.user_data.get("phone", "")),
            (["linkedin"], self.user_data.get("linkedin", "")),
            (["github"], self.user_data.get("github", "")),
            (["website", "portfolio"], self.user_data.get("portfolio", "")),
            (["years of experience"], self.user_data.get("years_of_experience", "")),
            (["salary", "compensation"], self.user_data.get("salary_expectation", "")),
            (["current title"], self.user_data.get("current_title", "")),
            (["work authorization", "authorized to work"], self.user_data.get("work_authorization", "")),
            (["sponsorship", "visa"], self.user_data.get("require_sponsorship", "")),
        ]

        try:
            fields = self.page.locator(
                "input[id^='question_'], textarea[id^='question_'], "
                "input[name*='question'], textarea[name*='question']"
            )
            count = fields.count()
            if count == 0:
                return

            for i in range(count):
                field = fields.nth(i)
                try:
                    if not field.is_visible() or not field.is_enabled():
                        continue

                    field_id = (field.get_attribute("id") or "").strip()
                    question = (
                        (field.get_attribute("aria-label") or "")
                        or (field.get_attribute("placeholder") or "")
                    ).strip()

                    if not question and field_id:
                        label = self.page.locator(f"label[for='{field_id}'], #{field_id}-label").first
                        if label.count() > 0:
                            question = (label.inner_text() or "").strip()

                    question_l = question.lower()
                    if not question_l:
                        continue

                    current_value = ""
                    try:
                        current_value = (field.evaluate("el => el.value || ''") or "").strip()
                    except Exception:
                        try:
                            current_value = (field.input_value() or "").strip()
                        except Exception:
                            current_value = (field.inner_text() or "").strip()
                    if current_value:
                        continue

                    chosen_value = ""
                    for keywords, value in profile_rules:
                        if value and any(k in question_l for k in keywords):
                            chosen_value = str(value)
                            break

                    if not chosen_value:
                        continue

                    role = (field.get_attribute("role") or "").lower()
                    css_class = (field.get_attribute("class") or "").lower()
                    is_combobox = role == "combobox" or "select__input" in css_class

                    field.click()
                    if is_combobox:
                        field.fill("")
                        field.type(chosen_value, delay=20)
                        time.sleep(0.4)
                        option = self.page.locator(
                            f"[role='option']:has-text('{chosen_value}'), "
                            f"[id*='-option-']:has-text('{chosen_value}')"
                        ).first
                        if option.count() > 0:
                            option.click()
                        else:
                            field.press("Enter")
                    else:
                        field.fill(chosen_value)
                except Exception:
                    continue
        except Exception:
            pass

    def _prompt_for_open_ended_questions(self):
        # Scan the application form for still-empty custom fields and prompt user input.
        # This supports text inputs, textareas, selects, and React combobox fields.
        SKIP_MARKERS = {
            # Core profile/contact fields.
            "first_name", "last_name", "first name", "last name",
            "preferred", "email", "phone", "resume", "cover_letter",
            "cover letter", "linkedin", "github", "website", "portfolio",
            "location", "city", "state", "zip", "address",
            "salary", "compensation", "years_of_experience", "years of experience",
            "current_title", "current title",
            "work_authorization", "work authorization", "authorized to work",
            "sponsorship", "require_sponsorship", "visa",
            "education", "degree", "school", "university", "gpa",
            # Voluntary self-identification / EEO / OFCCP sections.
            "voluntary self-identification", "self-identification",
            "equal employment opportunity", "eeo", "ofccp",
            "cc-305", "omb control number", "vevraa",
            "race & ethnicity definitions", "race and ethnicity definitions",
            "gender", "are you hispanic/latino", "hispanic/latino",
            "please identify your race", "race", "ethnicity",
            "sexual orientation", "transgender",
            "veteran status", "protected veteran", "disabled veteran",
            "recently separated veteran", "active duty wartime",
            "armed forces service medal veteran",
            "disability status", "do you have a disability",
            "i do not have a disability", "have not had one in the past",
        }

        seen_questions: set = set()

        def _get_label(field, field_id: str, field_name: str) -> str:
            # Best-effort label text for a field.
            for attr in ["aria-label", "placeholder", "title"]:
                val = (field.get_attribute(attr) or "").strip()
                if val:
                    return val
            for lookup in [field_id, field_name]:
                if not lookup:
                    continue
                try:
                    lbl = self.page.locator(f"label[for='{lookup}']").first
                    if lbl.count() > 0:
                        text = (lbl.inner_text() or "").strip()
                        if text:
                            return text
                except Exception:
                    pass
            return ""

        def _is_standard_field(label: str, field_id: str, field_name: str) -> bool:
            combined = f"{label} {field_id} {field_name}".lower()
            return any(marker in combined for marker in SKIP_MARKERS)

        def _current_value(field, tag: str) -> str:
            # Read live DOM value, reliable for React-controlled inputs.
            try:
                if tag == "select":
                    idx = field.evaluate("el => el.selectedIndex")
                    if idx and idx > 0:
                        return (field.evaluate("el => el.options[el.selectedIndex].text || ''") or "").strip()
                    return ""
                return (field.evaluate("el => el.value || ''") or "").strip()
            except Exception:
                try:
                    return (field.input_value() or "").strip()
                except Exception:
                    return ""

        try:
            fields = self.page.locator(
                # Question-prefixed fields first.
                "input[id^='question_']:not([type='hidden']):not([type='file']):not([type='submit']):not([type='button']):not([type='checkbox']):not([type='radio']), "
                "textarea[id^='question_'], "
                "select[id^='question_'], "
                "input[name*='question']:not([type='hidden']):not([type='file']):not([type='submit']):not([type='button']):not([type='checkbox']):not([type='radio']), "
                "textarea[name*='question'], "
                "select[name*='question'], "
                # Generic fallback fields for non-question_* application layouts.
                "input:not([type='hidden']):not([type='file']):not([type='submit']):not([type='button']):not([type='checkbox']):not([type='radio']), "
                "textarea, "
                "select"
            )
            count = fields.count()
            if count == 0:
                logger.info("No unresolved question_* fields detected for manual prompting")
                return

            for i in range(count):
                field = fields.nth(i)
                try:
                    if not field.is_visible() or not field.is_enabled():
                        continue

                    tag = (field.evaluate("el => el.tagName.toLowerCase()") or "input")
                    field_id = (field.get_attribute("id") or "").strip()
                    field_name = (field.get_attribute("name") or "").strip()

                    label = _get_label(field, field_id, field_name)
                    if _is_standard_field(label, field_id, field_name):
                        continue

                    if _current_value(field, tag):
                        continue

                    question_key = label or f"field_{i}"
                    if question_key in seen_questions:
                        continue
                    seen_questions.add(question_key)

                    display_question = label or f"Open-ended question #{i + 1}"
                    auto_answer = self._lookup_custom_answer(display_question)

                    if tag == "select":
                        try:
                            options = field.evaluate(
                                "el => Array.from(el.options).map((o,i) => ({index:i, text:o.text, value:o.value}))"
                            )
                            real_options = [o for o in options if (o.get("text") or "").strip() and o.get("index", 0) > 0]
                        except Exception:
                            real_options = []

                        if auto_answer:
                            answer = auto_answer.strip()
                            logger.info(f"Auto-answered custom select from custom_answers: {display_question}")
                        else:
                            print("\n" + "=" * 60)
                            print("ACTION REQUIRED: Please answer this application question.")
                            print(f"Question: {display_question}")
                            if real_options:
                                print("\nOptions:")
                                for opt in real_options:
                                    print(f"  {opt['index']}) {opt['text']}")
                                print("\nType the NUMBER of your choice, or press Enter to skip.")
                            else:
                                print("(No options found - press Enter to skip.)")
                            print("=" * 60)
                            answer = input("Your choice: ").strip()

                        if not answer:
                            continue

                        selected_ok = False
                        try:
                            idx = int(answer)
                            match = [o for o in real_options if o.get("index") == idx]
                            if match:
                                field.select_option(value=match[0]["value"])
                                logger.info(f"Selected option: {match[0]['text']}")
                                self._remember_custom_answer(display_question, str(match[0]['text']))
                                selected_ok = True
                        except (ValueError, Exception):
                            pass

                        if not selected_ok:
                            try:
                                field.select_option(label=answer)
                                self._remember_custom_answer(display_question, answer)
                                selected_ok = True
                            except Exception:
                                pass

                        if not selected_ok:
                            logger.info(f"Could not auto-select value for: {display_question} (continuing)")
                        continue

                    role = (field.get_attribute("role") or "").lower()
                    css_class = (field.get_attribute("class") or "").lower()
                    is_combobox = role == "combobox" or "select__input" in css_class

                    if auto_answer:
                        answer = auto_answer.strip()
                        logger.info(f"Auto-answered custom field from custom_answers: {display_question}")
                    else:
                        print("\n" + "=" * 60)
                        print("ACTION REQUIRED: Please answer this application question.")
                        print(f"Question: {display_question}")
                        print("Leave blank and press Enter to skip this field.")
                        print("=" * 60)
                        answer = input("Your answer: ").strip()

                    if not answer:
                        continue

                    fill_ok = False
                    try:
                        field.click()
                        if is_combobox:
                            field.fill("")
                            field.type(answer, delay=20)
                            time.sleep(0.5)
                            option = self.page.locator(
                                f"[role='option']:has-text('{answer}'), [id*='-option-']:has-text('{answer}')"
                            ).first
                            if option.count() > 0:
                                option.click()
                            else:
                                field.press("Enter")
                        else:
                            field.fill(answer)
                        fill_ok = True
                    except Exception:
                        try:
                            field.click()
                            self.page.keyboard.type(answer)
                            if is_combobox:
                                self.page.keyboard.press("Enter")
                            fill_ok = True
                        except Exception:
                            fill_ok = False

                    if fill_ok:
                        self._remember_custom_answer(display_question, answer)
                    else:
                        logger.info(f"Could not fill custom field: {display_question} (continuing)")
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"_prompt_for_open_ended_questions error: {e}")

    def submit_application(self) -> bool:
        # Attempt to click submit using selector fallbacks.
        try:
            submitted = self._safe_click(
                [
                    "button.btn.btn--pill[type='submit']:has-text('Submit application')",
                    "button:has-text('Submit application')",
                    "button:has-text('Submit Application')",
                    "button:has-text('Submit')",
                    "input[type='submit']",
                    "button[type='submit']",
                    "[type='submit']",
                ],
                warn_on_fail=True,
            )

            if submitted:
                time.sleep(3)
                logger.info("Application submitted successfully")
                return True

            logger.warning("Could not find the Submit button")
            return False
        except Exception as e:
            logger.error(f"Submit error: {e}")
            return False
