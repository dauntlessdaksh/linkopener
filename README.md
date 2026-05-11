# LinkedIn URL checker (macOS)

Small Python utility that:

1. Asks you to pick an **Excel** file (`.xlsx` / `.xlsm`).
2. Asks for a **start** and **end** row number (1-based, same numbers you see in Excel’s row gutter).
3. Reads profile links from the column **`LinkedIn URL`**.
4. Opens each candidate URL in **Google Chrome** using a **dedicated Selenium profile** (separate user data directory so it does not use your everyday Chrome profile).
5. Classifies each row and writes results into a **`URL Status`** column in the **same workbook file** (no duplicate copy).

**This tool does not automate LinkedIn.** It only navigates with `driver.get()` and inspects the final URL / page text so you can manually review tabs yourself.

## LinkedIn login (first run vs later runs)

The script uses a **dedicated Chrome profile folder** (`~/.linkopener_selenium_chrome` by default). Chrome saves your LinkedIn session there automatically — **no cookie files, no LinkedIn APIs**, only Selenium + that profile.

**First time (or after you clear that folder):**

1. The terminal prints that Chrome is opening and asks you to **log in manually** in that window.
2. The script waits until you reach the **feed** (it checks the current URL in the background). It does **not** reload the login page every few seconds while you type.
3. When login is detected, it continues opening your `/in/...` profile tabs.

**Later runs:**

- If the saved session is still valid, you should see **`Existing LinkedIn session found`** and it goes straight to loading profile tabs.

**Session expired mid-run:**

- If several profile URLs hit LinkedIn’s login / auth wall in a row, the script asks you to **log in again** in the same Chrome window, then continues.

If you see many false **`CORRUPTED`** rows with auth-related reasons, log in (or stay logged in) in **that** automation Chrome window — not your separate daily Chrome.

## What gets written in `URL Status`

| Value        | Meaning |
|-------------|---------|
| `SKIPPED`   | Empty / whitespace URL in that row. |
| `CORRUPTED` | Invalid URL, failed load, redirect away from a profile, “Profile not found”-style copy, login/checkpoint/**authwall** gate, etc. |
| `OK`        | After load, the page still looks like a LinkedIn `/in/...` profile and no obvious error phrases matched. |

## Requirements

- macOS with **Google Chrome** installed.
- **Python 3.10+** recommended.

## Setup

```bash
cd /path/to/linkopener
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Chromedriver

### Option A — Selenium Manager (default, easiest)

Selenium 4 ships **Selenium Manager**, which usually downloads a matching **ChromeDriver** automatically the first time you run the script. If that works, you do **not** need Homebrew chromedriver.

### Option B — Homebrew

```bash
brew install chromedriver
```

If macOS blocks the binary (“damaged” / quarantine):

```bash
xattr -dr com.apple.quarantine "$(which chromedriver)"
```

### Option C — Manual download

1. Check your Chrome version: **Chrome → About Google Chrome**.
2. Download a matching **Chrome for Testing** / **ChromeDriver** build from Google’s official channels for your Chrome major version.
3. Put the `chromedriver` binary on your `PATH`, or pass a `Service(executable_path=...)` if you customize the code.

Keep **Chrome** and **ChromeDriver** major versions aligned.

## How to run

```bash
source .venv/bin/activate
python main.py
```

1. Choose your Excel file.
2. Enter **Start row** and **End row** in the single **Row range** window (inclusive, 1-based).
3. Complete LinkedIn login in the automation Chrome window if the script is waiting (first run or expired session).
4. Watch the terminal for lines like **`OPENED`**, **`OK`**, **`CORRUPTED`**, **`SKIPPED`**.
5. When finished, the workbook is saved in place. **Chrome stays open** on purpose so you can manually visit each tab.

## Excel layout

- First sheet is used (sheet index `0`).
- Row **1** must contain headers; one header cell must be exactly **`LinkedIn URL`**.
- The script creates **`URL Status`** on row **1** if it is missing, then fills rows in your selected range.

## Tabs, speed, and Chrome stability

Opening **dozens of LinkedIn tabs at once** often crashes the renderer (**“Aw, snap” / Error code 5 / tab crashed**). The script therefore:

- Waits **`LINKOPENER_TAB_DELAY_SEC`** (default **0.35s**) between tab actions.
- Keeps at most **`LINKOPENER_MAX_OPEN_TABS`** (default **12**) **profile** tabs; after that it **reuses** tabs in round-robin (each tab’s URL updates, but Chrome stays alive). Your **feed/login tab** is left alone.

```bash
export LINKOPENER_MAX_OPEN_TABS=8
export LINKOPENER_TAB_DELAY_SEC=0.5
python main.py
```

## Page checks (why `CORRUPTED` looked wrong before)

LinkedIn ships huge JavaScript bundles that contain generic strings like **“Something went wrong”** even on **valid** profiles while the SPA is loading. By default the script only treats **`Profile not found`** (plus URL-based gates like login/authwall) as text proof of a bad profile.

**Stricter HTML scan (optional):** set **`LINKOPENER_STRICT_PAGE_TEXT=1`** to also flag **“Something went wrong”**, **“This page doesn’t exist”**, **“Page not found”**, etc. That catches more real error screens but can mark good profiles **CORRUPTED** again during load.

**`driver.quit()` is never called.** The script also **pins the WebDriver in memory** and enables Chrome **`detach`** so that when Python exits the idle REPL / process teardown, Selenium does not garbage-collect chromedriver and accidentally close your window. If Chrome still vanishes, it is usually a **renderer crash** (too many heavy tabs).

## Load / timing tuning (optional)

Defaults aim for **reliable `OK`/`CORRUPTED`** without melting Chrome: **`pageLoadStrategy: eager`**, **`document.readyState` wait**, URL spin, and a short post-settle sleep.

| Variable | Default | Meaning |
|----------|---------|--------|
| `LINKOPENER_PAGE_LOAD_STRATEGY` | `eager` | `none` for fastest (riskier mis-reads); `normal` slowest. |
| `LINKOPENER_SPIN_MAX_SEC` | `3.0` | Max seconds to poll `current_url` after `none` navigations. |
| `LINKOPENER_SPIN_POLL_SEC` | `0.05` | Poll interval for that spin. |
| `LINKOPENER_SPIN_STABLE_COUNT` | `3` | Consecutive identical URLs before “settled”. |
| `LINKOPENER_NAVIGATE_SETTLE_SEC` | `0.35` | Extra sleep after readyState for SPA paint. |
| `LINKOPENER_READY_TIMEOUT_SEC` | `5` | Max wait while polling `document.readyState` (returns earlier at `interactive` / `complete`). |
| `LINKOPENER_BLOCK_IMAGES` | (off) | Set to `1` to block images (faster, can make LinkedIn flakier). |
| `LINKOPENER_STAY_IN_BACKGROUND` | (off) | Set to `1` to auto-minimize Chrome after each step. |
| `LINKOPENER_STRICT_PAGE_TEXT` | (off) | Set to `1` to scan for **broad** error phrases in HTML (stricter `CORRUPTED`, more false positives). |
| `LINKOPENER_CLOSE_PREVIOUS_CHROME` | `1` | Set to `0` to **not** SIGTERM an old automation Chrome still using the same profile before a new run. |

Example (more aggressive):

```bash
export LINKOPENER_PAGE_LOAD_STRATEGY=none
export LINKOPENER_NAVIGATE_SETTLE_SEC=0.15
python main.py
```

Example (stricter error-page detection):

```bash
export LINKOPENER_STRICT_PAGE_TEXT=1
python main.py
```

## Login wait timeout (optional)

By default the script waits **as long as needed** for you to finish manual login (or press **Ctrl+C** to abort).

To cap the wait (seconds):

```bash
export LINKOPENER_LOGIN_WAIT_SEC=1800
python main.py
```

Use **`0`** or leave unset for unlimited wait.

## Isolated Chrome profile location

By default the profile folder is:

`~/.linkopener_selenium_chrome`

This is **not** your normal Chrome profile.

Each new run **closes any Chrome still using that folder** (so the profile is not locked). To keep an old window and start a second session anyway, run with `LINKOPENER_CLOSE_PREVIOUS_CHROME=0` (not recommended).

## Troubleshooting

- **`Missing "LinkedIn URL" column`**: fix the header text in row 1 (exact name, trimming spaces is OK when matching via pandas).
- **Gatekeeper / security prompts**: allow Chrome/Terminal to control your computer if macOS asks (System Settings → Privacy & Security).
- **Many false `CORRUPTED` results**: ensure you are logged in; avoid `LINKOPENER_PAGE_LOAD_STRATEGY=none` with very low settles unless you know what you are doing.
- **Chrome “Aw, snap” / tab crashed**: lower **`LINKOPENER_MAX_OPEN_TABS`** (e.g. `8`) and/or raise **`LINKOPENER_TAB_DELAY_SEC`** (e.g. `0.5`).
- **`authwall` in the address bar**: you are not logged in (or LinkedIn is blocking the session); sign in when prompted, or log in once and rerun.

## Legal / etiquette

Use LinkedIn in line with their terms and your local laws. This script is meant for **read-only navigation** to support **manual** outreach workflows.
