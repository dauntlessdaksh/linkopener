"""
Selenium Chrome session in an isolated user-data-dir.

Only navigates and reads the DOM/URL — no clicks or LinkedIn automation.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

# Default: only high-confidence phrases (LinkedIn JS bundles often embed generic error copy).
_STRICT_CORRUPTED_SUBSTRINGS = (
    "Profile not found",
)
# Opt-in via LINKOPENER_STRICT_PAGE_TEXT=1 — catches more real error screens but can false-positive on valid profiles.
_BROAD_CORRUPTED_SUBSTRINGS = (
    "Profile not found",
    "This page doesn't exist",
    "This page doesn\u2019t exist",
    "couldn't find",
    "couldn\u2019t find",
    "Something went wrong",
    "Page not found",
)

DEFAULT_CHROME_USER_DATA_DIR = Path.home() / ".linkopener_selenium_chrome"

# Selenium's chromedriver Service.__del__ stops the driver process when the WebDriver is garbage-collected.
# Keeping a strong reference here prevents Chrome from vanishing as soon as main() returns.
_DRIVER_GC_GUARD: webdriver.Chrome | None = None


def retain_chrome_session(driver: webdriver.Chrome) -> None:
    """Keep the WebDriver (and its Service) alive for the lifetime of the Python process so Chrome stays open."""
    global _DRIVER_GC_GUARD
    _DRIVER_GC_GUARD = driver


def env_close_previous_chrome() -> bool:
    """
    If True (default), before starting Selenium, SIGTERM any Chrome still using this user-data-dir.

    Avoids "profile in use" / flaky sessions when a previous automation window was left open.
    Set LINKOPENER_CLOSE_PREVIOUS_CHROME=0 to skip (e.g. you intentionally run two flows — not recommended).
    """
    raw = os.environ.get("LINKOPENER_CLOSE_PREVIOUS_CHROME", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def close_chrome_processes_using_profile(user_data_dir: Path) -> int:
    """
    Best-effort: terminate Chrome browser processes whose command line includes this profile path.

    Does not kill chromedriver binaries. macOS/Linux only (Windows: no-op).
    """
    if not env_close_previous_chrome():
        return 0

    system = platform.system()
    if system not in ("Darwin", "Linux"):
        return 0

    resolved = str(user_data_dir.expanduser().resolve())
    try:
        proc = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0

    pids: set[int] = set()
    killed = 0
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip()
        if not line or resolved not in line:
            continue
        if "chromedriver" in line.lower():
            continue

        if system == "Darwin":
            if "Google Chrome.app/Contents/MacOS/Google Chrome" not in line:
                continue
        else:
            if not any(s in line for s in ("google-chrome", "/opt/google/chrome", "chromium", "/chrome")):
                continue

        try:
            pid = int(line.split(None, 1)[0])
        except (ValueError, IndexError):
            continue
        if pid == os.getpid() or pid in pids:
            continue
        pids.add(pid)
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except (ProcessLookupError, PermissionError):
            pass

    if killed:
        print(f"Closed {killed} prior automation Chrome process(es) still using this profile.")
        time.sleep(1.0)

    return killed


def env_stay_in_background() -> bool:
    """
    If True, minimize Chrome after navigations (opt-in).

    Default is off so Chrome behaves like a normal app — you choose minimize or leave it open.
    Set LINKOPENER_STAY_IN_BACKGROUND=1 only if you want the script to auto-minimize after each step.
    """
    raw = os.environ.get("LINKOPENER_STAY_IN_BACKGROUND", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def env_block_images() -> bool:
    """Block images for speed (set LINKOPENER_BLOCK_IMAGES=1). Default off — fewer SPA glitches."""
    raw = os.environ.get("LINKOPENER_BLOCK_IMAGES", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def env_strict_page_text() -> bool:
    """
    If True, scan page_source for a broader list of LinkedIn error phrases (stricter CORRUPTED).

    Can mark valid profiles CORRUPTED when those strings appear only inside bundled JS during load.
    Set LINKOPENER_STRICT_PAGE_TEXT=1 when you prefer fewer false OKs on real error pages.
    """
    raw = os.environ.get("LINKOPENER_STRICT_PAGE_TEXT", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _page_text_corruption_needles() -> tuple[str, ...]:
    return _BROAD_CORRUPTED_SUBSTRINGS if env_strict_page_text() else _STRICT_CORRUPTED_SUBSTRINGS


def navigate_settle_seconds() -> float:
    """Extra pause after URL + document settle (LinkedIn is a SPA)."""
    raw = os.environ.get("LINKOPENER_NAVIGATE_SETTLE_SEC", "0.35")
    try:
        v = float(raw)
    except ValueError:
        return 0.35
    return max(0.0, v)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def wait_linkedin_navigation_settle(driver: webdriver.Chrome) -> None:
    """
    After driver.get(), wait until linkedin.com shows a stable URL (handles pageLoadStrategy 'none').

    Much faster than a long fixed sleep while still letting client-side redirects finish.
    """
    max_total = max(0.08, _env_float("LINKOPENER_SPIN_MAX_SEC", 3.0))
    interval = max(0.02, _env_float("LINKOPENER_SPIN_POLL_SEC", 0.05))
    need_stable = max(2, _env_int("LINKOPENER_SPIN_STABLE_COUNT", 3))
    settle = navigate_settle_seconds()

    t_end = time.monotonic() + max_total
    last: str | object | None = None
    same = 0

    while time.monotonic() < t_end:
        try:
            u = driver.current_url or ""
        except WebDriverException:
            time.sleep(interval)
            continue

        ul = u.lower()
        if "linkedin.com" in ul and not ul.startswith("chrome://") and "about:blank" not in ul:
            if u == last:
                same += 1
                if same >= need_stable:
                    if settle > 0:
                        time.sleep(min(settle, 0.12))
                    return
            else:
                last = u
                same = 1
        else:
            last = None
            same = 0
        time.sleep(interval)

    if settle > 0:
        time.sleep(min(settle, 0.18))


def wait_document_ready(driver: webdriver.Chrome, *, timeout_sec: float | None = None) -> None:
    """
    Wait until document is at least interactive (LinkedIn often never reaches 'complete' due to analytics).

    Falls through after timeout so we do not block forever.
    """
    t = timeout_sec if timeout_sec is not None else _env_float("LINKOPENER_READY_TIMEOUT_SEC", 5.0)
    deadline = time.monotonic() + max(0.2, t)
    while time.monotonic() < deadline:
        try:
            state = driver.execute_script("return document.readyState")
            if state == "complete":
                return
            if state == "interactive":
                time.sleep(0.1)
                return
        except WebDriverException:
            pass
        time.sleep(0.06)


def keep_browser_in_background(driver: webdriver.Chrome) -> None:
    """Minimize Chrome when LINKOPENER_STAY_IN_BACKGROUND=1 (opt-in). No-op by default."""
    if not env_stay_in_background():
        return
    try:
        driver.minimize_window()
    except WebDriverException:
        pass


def build_chrome_options(user_data_dir: Path | None = None) -> Options:
    user_data_dir = user_data_dir or DEFAULT_CHROME_USER_DATA_DIR
    user_data_dir.mkdir(parents=True, exist_ok=True)

    options = Options()
    # Default eager: wait for DOMContentLoaded — fewer false reads than "none" on LinkedIn SPA.
    _pls = os.environ.get("LINKOPENER_PAGE_LOAD_STRATEGY", "eager").strip().lower()
    options.page_load_strategy = _pls if _pls in ("none", "eager", "normal") else "eager"
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument("--profile-directory=Default")
    # Safer defaults for automation-style launches on macOS
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    # Browser survives chromedriver disconnect (pairs with retain_chrome_session in main).
    options.add_experimental_option("detach", True)
    if env_block_images():
        options.add_experimental_option(
            "prefs",
            {"profile.managed_default_content_settings.images": 2},
        )
    return options


def create_driver(
    *,
    page_load_timeout_sec: float = 22.0,
    user_data_dir: Path | None = None,
) -> webdriver.Chrome:
    udir = (user_data_dir or DEFAULT_CHROME_USER_DATA_DIR).expanduser().resolve()
    close_chrome_processes_using_profile(udir)
    options = build_chrome_options(user_data_dir)
    # Selenium Manager resolves chromedriver in most setups.
    service = Service()
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(page_load_timeout_sec)
    driver.implicitly_wait(0)
    keep_browser_in_background(driver)
    return driver


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().split(":")[0]
    except Exception:
        return ""


def _path(url: str) -> str:
    try:
        p = urlparse(url).path or ""
        if not p.startswith("/"):
            p = "/" + p
        return p
    except Exception:
        return ""


def is_profile_url(url: str) -> bool:
    host = _host(url)
    if host not in {"www.linkedin.com", "linkedin.com", "mobile.linkedin.com"}:
        return False
    path = _path(url).lower()
    return path.startswith("/in/")


def is_auth_or_error_path(url: str) -> bool:
    """
    True when LinkedIn is showing a gate instead of content (login, checkpoint, authwall, etc.).

    /authwall/ often appears when logged out and hitting member content — treat like login wall.
    """
    u = url.lower()
    return (
        "/login" in u
        or "/checkpoint" in u
        or "/uas/login" in u
        or "/authwall" in u
        or "authwall" in u
    )


def classify_loaded_page(driver: webdriver.Chrome) -> tuple[str, str | None]:
    """
    Returns ("OK" | "CORRUPTED", reason_or_none).
    """
    try:
        current = driver.current_url or ""
    except WebDriverException as e:
        return ("CORRUPTED", f"current_url_error:{e.__class__.__name__}")

    if not current or current.startswith("chrome://") or current.startswith("about:blank"):
        return ("CORRUPTED", "empty_or_internal_url")

    if is_auth_or_error_path(current):
        return ("CORRUPTED", "auth_or_checkpoint_redirect")

    if not is_profile_url(current):
        return ("CORRUPTED", "not_profile_after_load")

    try:
        html = driver.page_source or ""
    except WebDriverException as e:
        return ("CORRUPTED", f"page_source_error:{e.__class__.__name__}")

    lower = html.lower()
    for needle in _page_text_corruption_needles():
        if needle.lower() in lower:
            return ("CORRUPTED", f"page_text:{needle}")

    return ("OK", None)


def open_new_tab(driver: webdriver.Chrome) -> str:
    """Return the new window handle after opening a blank tab."""
    existing = set(driver.window_handles)
    driver.execute_script("window.open('about:blank','_blank');")
    WebDriverWait(driver, 6, poll_frequency=0.05).until(lambda d: len(d.window_handles) > len(existing))
    new_handles = [h for h in driver.window_handles if h not in existing]
    if not new_handles:
        raise RuntimeError("Failed to open a new browser tab.")
    return new_handles[-1]


def navigate_and_classify(driver: webdriver.Chrome, url: str) -> tuple[str, str | None]:
    try:
        driver.get(url)
    except TimeoutException:
        return ("CORRUPTED", "page_load_timeout")
    except WebDriverException as e:
        return ("CORRUPTED", f"navigation_error:{e.__class__.__name__}")

    wait_linkedin_navigation_settle(driver)
    wait_document_ready(driver)
    extra = navigate_settle_seconds()
    if extra > 0:
        time.sleep(extra)

    out = classify_loaded_page(driver)
    # SPA can briefly expose error-like copy; one re-check without a full reload.
    if out[0] == "CORRUPTED" and out[1] and out[1].startswith("page_text:"):
        try:
            cur = driver.current_url or ""
        except WebDriverException:
            cur = ""
        if is_profile_url(cur):
            time.sleep(0.85)
            wait_document_ready(driver, timeout_sec=4.0)
            out = classify_loaded_page(driver)

    keep_browser_in_background(driver)
    return out


def delay_between_tabs(seconds: float) -> None:
    time.sleep(max(0.0, float(seconds)))


def env_tab_delay_seconds(default: float = 0.35) -> float:
    """Pause between opening/switching tabs — reduces renderer OOM when many LinkedIn tabs load at once."""
    raw = os.environ.get("LINKOPENER_TAB_DELAY_SEC")
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def env_max_open_tabs() -> int:
    """
    Max LinkedIn profile tabs to keep open at once. Beyond this we reuse tabs (round-robin) so Chrome does not crash.
    """
    v = _env_int("LINKOPENER_MAX_OPEN_TABS", 12)
    return max(1, min(40, v))
