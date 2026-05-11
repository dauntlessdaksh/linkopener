# LinkedIn URL checker (macOS)

Opens LinkedIn profile URLs from an Excel sheet in a dedicated Chrome window and writes a **`URL Status`** column back into the same file.

## Prerequisites

- macOS
- [Google Chrome](https://www.google.com/chrome/) installed
- Python **3.10+** (`python3 --version`)

## First-time setup

```bash
cd /path/to/linkopener
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Selenium 4 usually downloads a matching **ChromeDriver** the first time you run. If that fails, install ChromeDriver (for example `brew install chromedriver`) and align its major version with Chrome.

## Run

```bash
cd /path/to/linkopener
source .venv/bin/activate
python main.py
```

1. Select a **`.xlsx`** or **`.xlsm`** file. Row **1** must include a column header exactly **`LinkedIn URL`** (first sheet).
2. Enter **Start row** and **End row** (1-based, inclusive).
3. If Chrome opens and the terminal asks you to sign in, log in to LinkedIn in **that** window and wait until the script continues.
4. When it finishes, save output is in the workbook; Chrome may stay open for you to review tabs.
