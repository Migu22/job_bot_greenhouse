# Abstract platform contract.
# Every platform implementation should expose a job search and apply interface so
# main workflow code can stay platform-agnostic.

from abc import ABC, abstractmethod


class BasePlatform(ABC):
    # Common interface all platform bots must implement.

    def __init__(self, driver, rate_limiter):
        # `driver` is reserved for implementations that use Selenium-like drivers.
        # Greenhouse currently uses Playwright page objects.
        self.driver = driver
        self.rate_limiter = rate_limiter

    @abstractmethod
    def search_jobs(self, keywords: str, location: str) -> list:
        # Return a list of discovered jobs for the provided query.
        pass

    @abstractmethod
    def apply_to_job(self, job: dict, user_data: dict) -> bool:
        # Apply to a specific job and return success/failure.
        pass
