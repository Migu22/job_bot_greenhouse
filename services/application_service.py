# Data persistence service.
# Handles reading user config and writing runtime data to CSV files:
# - applied/simulated jobs
# - run and step events

import json
import os
from datetime import datetime

import pandas as pd

from core.logger import get_logger

logger = get_logger()

JOBS_CSV = "data/jobs.csv"
LOGS_CSV = "data/logs.csv"
CUSTOM_ANSWERS_JSON = "data/custom_answers.json"


class ApplicationService:
    # Read/write service for bot input and output data files.

    def __init__(self):
        # Ensure output files exist before any reads/writes.
        self._ensure_files()

    def _ensure_files(self):
        # Create/reset CSV headers when files are missing or empty.
        if not os.path.exists(JOBS_CSV) or os.path.getsize(JOBS_CSV) == 0:
            pd.DataFrame(
                columns=["timestamp", "title", "company", "url", "status", "applied_at"]
            ).to_csv(JOBS_CSV, index=False)
            logger.info(f"Created/Reset headers: {JOBS_CSV}")

        if not os.path.exists(LOGS_CSV) or os.path.getsize(LOGS_CSV) == 0:
            pd.DataFrame(columns=["timestamp", "event", "details"]).to_csv(
                LOGS_CSV, index=False
            )
            logger.info(f"Created/Reset headers: {LOGS_CSV}")

    def load_user_data(self) -> dict:
        # Load user profile JSON and merge secrets from `.env`.
        try:
            from dotenv import load_dotenv

            env_path = os.path.join("job_bot", ".env")
            load_dotenv(env_path)

            with open("data/user_data.json", "r", encoding="utf-8") as f:
                data = json.load(f)

            # Load custom answers from separate file for cleaner structure.
            custom_answers = {}
            try:
                if os.path.exists(CUSTOM_ANSWERS_JSON):
                    with open(CUSTOM_ANSWERS_JSON, "r", encoding="utf-8") as cf:
                        loaded = json.load(cf)
                    if isinstance(loaded, dict):
                        custom_answers = loaded
            except Exception as e:
                logger.warning(f"Could not load {CUSTOM_ANSWERS_JSON}: {e}")

            # Backward compatibility: keep inline custom_answers if present,
            # but prefer values from the dedicated file.
            inline_answers = data.get("custom_answers", {})
            if isinstance(inline_answers, dict):
                merged_answers = inline_answers.copy()
                merged_answers.update(custom_answers)
                custom_answers = merged_answers

            data["custom_answers"] = custom_answers
            data["greenhouse_email"] = os.getenv("GREENHOUSE_EMAIL", "")
            data["resume_path"] = os.getenv("RESUME_PATH", data.get("resume_path", ""))
            data["cover_letter_path"] = os.getenv("COVER_LETTER_PATH", data.get("cover_letter_path", ""))

            logger.info(
                f"User data loaded for: {data.get('first_name', '')} {data.get('last_name', '')}"
            )
            return data

        except FileNotFoundError:
            logger.error("data/user_data.json not found. Please create it")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"user_data.json has invalid JSON: {e}")
            return {}

    def save_job(self, job: dict, status: str = "applied"):
        # Append one job outcome row to `data/jobs.csv`.
        # For dry-run rows, skip duplicates to keep CSV readable across repeated tests.
        try:
            df = pd.read_csv(JOBS_CSV)
            now = datetime.now().isoformat()

            title = str(job.get("title", "Unknown")).strip()
            company = str(job.get("company", "Unknown")).strip()
            url = str(job.get("url", "") or "").strip()

            if status == "dry_run" and not df.empty:
                probe = df.copy()
                probe["title"] = probe["title"].fillna("").astype(str).str.strip()
                probe["company"] = probe["company"].fillna("").astype(str).str.strip()
                probe["url"] = probe["url"].fillna("").astype(str).str.strip()
                probe["status"] = probe["status"].fillna("").astype(str).str.strip()

                duplicate_by_url = bool(url) and (probe["url"] == url).any()
                duplicate_by_title_company = (
                    (probe["status"] == "dry_run")
                    & (probe["title"] == title)
                    & (probe["company"] == company)
                ).any()

                if duplicate_by_url or duplicate_by_title_company:
                    logger.info(f"Skipped duplicate dry_run row: {title}")
                    return

            row = {
                "timestamp": now,
                "title": title,
                "company": company,
                "url": url,
                "status": status,
                "applied_at": now,
            }
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            df.to_csv(JOBS_CSV, index=False)
            logger.info(f"Saved to jobs.csv: {row['title']}")
        except Exception as e:
            logger.error(f"Could not save job: {e}")

    def already_applied(self, url: str) -> bool:
        # Check if a job URL already exists in persisted records.
        try:
            if not url:
                return False
            df = pd.read_csv(JOBS_CSV)
            return url in df["url"].fillna("").values
        except Exception:
            return False

    def log_event(self, event: str, details: str = ""):
        # Append one structured event row to `data/logs.csv`.
        try:
            df = pd.read_csv(LOGS_CSV)
            row = {
                "timestamp": datetime.now().isoformat(),
                "event": event,
                "details": details,
            }
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            df.to_csv(LOGS_CSV, index=False)
        except Exception as e:
            logger.error(f"Could not log event: {e}")
