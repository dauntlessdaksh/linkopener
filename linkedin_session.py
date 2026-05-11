"""
LinkedIn login gate using only Selenium + the persistent Chrome profile (user-data-dir).

Chrome stores the session on disk inside that folder — no manual cookies and no APIs.
We only call driver.get() and read the URL / page source to detect login vs gate pages.
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlparse

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.webdriver import WebDriver

import browser

# LinkedIn changes HTML often; these are optional hints after URL checks.
_FEED_HTML_MARKERS = (
    "global-nav__me",  # nav avatar area (name may change)
    "feed-identity-module",
)


def _login_wait_timeout_sec() -> float | None:
    """
    Max seconds to wait for manual login. None = wait until Ctrl+C.

    Set LINKOPENER_LOGIN_WAIT_SEC to a number (e.g. 1800). Empty or 0 = unlimited.
    """
    raw = os.environ.get("LINKOPENER_LOGIN_WAIT_SEC")
    if raw is None or not str(raw).strip():
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    if v <= 0:
        return None
    return v


def _logged_in_from_snapshot(url: str, html_lower: str) -> bool:
    """Interpret current URL + page HTML (already lowercased) as logged-in feed, without navigating."""
    if browser.is_auth_or_error_path(url):
        return False

    u = (url or "").lower()
    if "linkedin.com/feed" in u:
        return True

    try:
        path = (urlparse(url).path or "").lower()
        if path.startswith("/feed"):
            return True
    except Exception:
        pass

    for marker in _FEED_HTML_MARKERS:
        if marker.lower() in html_lower:
            return True

    return False


def probe_logged_in(driver: WebDriver, *, navigate: bool = True) -> bool:
    """
    If navigate=True: open the feed once (follows redirects to login when logged out).

    If navigate=False: only read the current tab — used while the user is typing their password.
    Repeated driver.get() during login would refresh the page every few seconds and boot them out
    of the form fields.
    """
    if navigate:
        try:
            driver.get("https://www.linkedin.com/feed/")
        except TimeoutException:
            return False
        except WebDriverException:
            return False
        browser.wait_linkedin_navigation_settle(driver)
    else:
        # Tiny pause so SPA navigations can settle before we read URL/DOM.
        time.sleep(0.1)

    try:
        url = driver.current_url or ""
        html = (driver.page_source or "").lower()
    except WebDriverException:
        return False

    return _logged_in_from_snapshot(url, html)


def ensure_linkedin_session(driver: WebDriver, *, session_expired: bool = False) -> None:
    """
    Block until LinkedIn accepts the feed URL (logged-in session), or timeout (if configured).

    Console output matches the product spec for first run vs later runs.
    """
    if probe_logged_in(driver, navigate=True):
        if not session_expired:
            print("Existing LinkedIn session found")
        print("Starting profile loading...")
        browser.keep_browser_in_background(driver)
        return

    if session_expired:
        print("LinkedIn session expired or invalid.")
        print("Please log in again in the Chrome window...")
    else:
        print("Opening Chrome profile...")
        print("Please log in to LinkedIn manually in the Chrome window...")
    print("Waiting for successful login...")
    print("(The script will not reload this tab while you type — it only checks the URL in the background.)")
    # Let you stay in Terminal / other apps; open Chrome from the Dock when you want to sign in.
    browser.keep_browser_in_background(driver)

    wait_budget = _login_wait_timeout_sec()
    deadline = (time.time() + wait_budget) if wait_budget is not None else None
    poll_sec = 2.0

    while True:
        if deadline is not None and time.time() > deadline:
            raise TimeoutError(
                "Timed out waiting for LinkedIn login. "
                "Increase LINKOPENER_LOGIN_WAIT_SEC or log in faster, then rerun."
            )

        # IMPORTANT: do not call driver.get() here — that was refreshing the login page every poll.
        if probe_logged_in(driver, navigate=False):
            print("Login detected!")
            # Chrome persists cookies/session inside user-data-dir; brief pause helps disk flush.
            time.sleep(0.6)
            print("Starting profile loading...")
            browser.keep_browser_in_background(driver)
            return

        time.sleep(poll_sec)
