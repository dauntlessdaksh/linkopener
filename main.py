#!/usr/bin/env python3
"""
LinkedIn URL checker for macOS.

Opens profile URLs in an isolated Chrome profile for manual review only.
Does not click Connect, send messages, or automate LinkedIn actions.
"""

from __future__ import annotations

import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox
from openpyxl import load_workbook

from selenium.webdriver.chrome.webdriver import WebDriver

import browser
import excel_io
import linkedin_session
import url_rules


def _schedule_destroy_row_dialog(win: tk.Toplevel, parent: tk.Tk) -> None:
    """
    Destroy the row dialog on the next Tk idle pass.

    On macOS, destroying a still-``-topmost`` Toplevel synchronously from the OK button
    sometimes leaves a stuck overlay; deferring teardown fixes it.
    """

    def _go() -> None:
        try:
            win.attributes("-topmost", False)
        except tk.TclError:
            pass
        try:
            win.withdraw()
        except tk.TclError:
            pass
        try:
            win.update_idletasks()
        except tk.TclError:
            pass
        try:
            win.destroy()
        except tk.TclError:
            pass
        try:
            parent.update_idletasks()
        except tk.TclError:
            pass

    parent.after_idle(_go)


def _collect_workbook_path(parent: tk.Tk) -> Path | None:
    """
    macOS: a fully ``withdraw()`` root often does not get app activation, so the first click only
    focuses Tk and the file sheet needs a second click. Briefly map the root off-screen, lift it,
    then open the native dialog so one click on \"Open\" works reliably.
    """
    parent.update_idletasks()
    try:
        parent.attributes("-topmost", False)
    except tk.TclError:
        pass
    parent.geometry("1x1+-2500+-2500")
    parent.deiconify()
    parent.lift()
    try:
        parent.focus_force()
    except tk.TclError:
        pass
    try:
        parent.update()
    except tk.TclError:
        pass

    path_str = filedialog.askopenfilename(
        parent=parent,
        title="Select Excel file",
        filetypes=[("Excel files", "*.xlsx *.xlsm"), ("All files", "*.*")],
    )

    try:
        parent.withdraw()
        parent.update_idletasks()
    except tk.TclError:
        pass

    if not path_str:
        return None
    return Path(path_str).expanduser().resolve()


def _collect_row_range(parent: tk.Tk, *, default_start: int = 2) -> tuple[int | None, int | None]:
    """
    One modal window for both start and end rows (avoids multiple destroy()/Tk() cycles on macOS).

    Note: On macOS, ``transient`` + ``grab_set`` on a child of a *withdrawn* root often breaks after the
    native file picker — the row window never maps or ignores clicks. We briefly move the root
    off-screen and deiconify it, skip ``transient``, avoid modal grab, then hide the root again.
    """
    result: dict[str, int | bool | None] = {"start": None, "end": None, "ok": False}

    # Let Tk map a real toplevel group; keep the empty root off-screen so it is not noticeable.
    # Do not mark these as system-wide always-on-top — that can leave a ghost "Row range" shell on macOS.
    parent.update_idletasks()
    try:
        parent.attributes("-topmost", False)
    except tk.TclError:
        pass
    parent.geometry("1x1+10000+10000")
    parent.deiconify()
    parent.lift()

    win = tk.Toplevel(parent)
    win.title("Row range")
    # Intentionally no transient(parent) — breaks when parent was withdrawn during filedialog.
    # Intentionally no grab_set() — can deadlock or eat events after Cocoa file dialog on macOS.

    tk.Label(win, text="Start row (1-based, inclusive):").grid(row=0, column=0, padx=8, pady=4, sticky="w")
    start_var = tk.StringVar(value=str(default_start))
    tk.Entry(win, textvariable=start_var, width=12).grid(row=0, column=1, padx=8, pady=4)

    tk.Label(win, text="End row (1-based, inclusive):").grid(row=1, column=0, padx=8, pady=4, sticky="w")
    end_var = tk.StringVar(value=str(default_start))
    tk.Entry(win, textvariable=end_var, width=12).grid(row=1, column=1, padx=8, pady=4)

    def on_ok() -> None:
        try:
            s = int(str(start_var.get()).strip())
            e = int(str(end_var.get()).strip())
        except ValueError:
            messagebox.showerror("Invalid input", "Enter whole numbers for start and end rows.", parent=win)
            return
        if s < 1 or e < 1:
            messagebox.showerror("Invalid input", "Row numbers must be at least 1.", parent=win)
            return
        if e < s:
            messagebox.showerror("Invalid range", "End row must be greater than or equal to start row.", parent=win)
            return
        result["start"] = s
        result["end"] = e
        result["ok"] = True
        _schedule_destroy_row_dialog(win, parent)

    def on_cancel() -> None:
        result["ok"] = False
        _schedule_destroy_row_dialog(win, parent)

    win.protocol("WM_DELETE_WINDOW", on_cancel)

    btns = tk.Frame(win)
    btns.grid(row=2, column=0, columnspan=2, pady=10)
    tk.Button(btns, text="OK", width=10, command=on_ok).pack(side="left", padx=6)
    tk.Button(btns, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)

    win.resizable(False, False)
    win.update_idletasks()
    # Center on screen (parent is parked off-screen, so do not position relative to it).
    rw = win.winfo_reqwidth()
    rh = win.winfo_reqheight()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = max(0, (sw - rw) // 2)
    y = max(0, (sh - rh) // 2)
    win.geometry(f"+{x}+{y}")
    win.lift()
    win.focus_force()

    def _focus_first_entry() -> None:
        for w in win.winfo_children():
            if isinstance(w, tk.Entry):
                w.focus_set()
                return

    win.after(80, _focus_first_entry)

    parent.wait_window(win)
    # Process deferred destroy + any follow-up events before continuing.
    try:
        parent.update_idletasks()
        parent.update()
    except tk.TclError:
        pass

    # macOS: ensure the row window is gone before Chrome starts (avoid a stuck always-on-top shell).
    try:
        if win.winfo_exists():
            win.attributes("-topmost", False)
            win.destroy()
    except tk.TclError:
        pass
    parent.attributes("-topmost", False)
    parent.withdraw()
    parent.update_idletasks()

    if not result["ok"]:
        return None, None
    return int(result["start"]), int(result["end"])


def _show_error_dialog(title: str, message: str) -> None:
    """After the main Tk root is destroyed (e.g. Selenium phase), use a short-lived root for errors."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        messagebox.showerror(title, message, parent=root)
    finally:
        root.destroy()


def main() -> int:
    print("LinkedIn URL checker — manual review only (no LinkedIn automation).")

    # One Tk root for all pre-Chrome dialogs avoids macOS focus bugs from create/destroy per prompt.
    ui_root = tk.Tk()
    ui_root.withdraw()
    # Avoid always-on-top on the hidden root — it can make child dialogs stick above Chrome on macOS.
    ui_root.attributes("-topmost", False)

    xlsx_path: Path | None = None
    start_row: int | None = None
    end_row: int | None = None

    try:
        xlsx_path = _collect_workbook_path(ui_root)
        if not xlsx_path:
            print("No file selected. Exiting.")
            return 1

        if xlsx_path.suffix.lower() not in {".xlsx", ".xlsm"}:
            messagebox.showerror(
                "Unsupported file",
                "Please choose a .xlsx or .xlsm file.",
                parent=ui_root,
            )
            return 1

        start_row, end_row = _collect_row_range(ui_root, default_start=2)
        if start_row is None or end_row is None:
            print("Row range not provided. Exiting.")
            return 1

        try:
            df = excel_io.validate_linkedin_column_with_pandas(xlsx_path, sheet_name=0)
            ctx = excel_io.prepare_workbook_columns(xlsx_path, sheet_index=0)
        except Exception as e:
            messagebox.showerror("Excel error", str(e), parent=ui_root)
            print(f"ERROR: {e}")
            return 1
    finally:
        try:
            ui_root.attributes("-topmost", False)
        except tk.TclError:
            pass
        ui_root.destroy()

    tab_delay = browser.env_tab_delay_seconds()

    driver: WebDriver | None = None
    wb = None

    try:
        wb = load_workbook(xlsx_path, read_only=False, data_only=False)
        driver = browser.create_driver(user_data_dir=None)
        browser.retain_chrome_session(driver)

        max_profile_tabs = browser.env_max_open_tabs()
        print(f"Using Chrome user-data-dir: {browser.DEFAULT_CHROME_USER_DATA_DIR}")
        print(f"Tab delay: {tab_delay}s | Max profile tabs: {max_profile_tabs} (then reuse tabs to avoid Chrome crashes)")

        try:
            linkedin_session.ensure_linkedin_session(driver, session_expired=False)
        except TimeoutError as e:
            print(f"ERROR: {e}")
            _show_error_dialog("Login timeout", str(e))
            return 1

        print(f"Processing rows {start_row}–{end_row} on sheet '{ctx.sheet_name}'")

        auth_streak = 0
        profile_tab_handles: list[str] = []
        tab_round_robin = 0
        opened_profile_index = 0

        for excel_row in range(start_row, end_row + 1):
            raw_url = excel_io.read_url_for_excel_row(df=df, excel_row=excel_row, ctx=ctx, wb=wb)

            if url_rules.is_blank(raw_url):
                auth_streak = 0
                print(f"[row {excel_row}] SKIPPED — empty")
                excel_io.write_status_cell(wb=wb, ctx=ctx, excel_row=excel_row, status="SKIPPED")
                continue

            ok_open, pre_reason = url_rules.precheck_linkedin_profile_url(raw_url)
            if not ok_open:
                auth_streak = 0
                print(f"[row {excel_row}] CORRUPTED — precheck:{pre_reason}")
                excel_io.write_status_cell(wb=wb, ctx=ctx, excel_row=excel_row, status="CORRUPTED")
                continue

            url = url_rules.normalize_url(str(raw_url))

            if opened_profile_index == 0:
                new_handle = browser.open_new_tab(driver)
                driver.switch_to.window(new_handle)
                profile_tab_handles.append(new_handle)
            else:
                browser.delay_between_tabs(tab_delay)
                if len(profile_tab_handles) < max_profile_tabs:
                    new_handle = browser.open_new_tab(driver)
                    driver.switch_to.window(new_handle)
                    profile_tab_handles.append(new_handle)
                else:
                    reuse = profile_tab_handles[tab_round_robin % len(profile_tab_handles)]
                    tab_round_robin += 1
                    driver.switch_to.window(reuse)

            opened_profile_index += 1

            print(f"[row {excel_row}] OPENED — {url}")
            status, reason = browser.navigate_and_classify(driver, url)

            if status == "CORRUPTED" and reason == "auth_or_checkpoint_redirect":
                auth_streak += 1
            else:
                auth_streak = 0

            if auth_streak >= 2:
                print("LinkedIn session may have expired — please log in again in the Chrome window...")
                try:
                    linkedin_session.ensure_linkedin_session(driver, session_expired=True)
                except TimeoutError as e:
                    print(f"ERROR: {e}")
                    _show_error_dialog("Login timeout", str(e))
                    return 1
                auth_streak = 0
                status, reason = browser.navigate_and_classify(driver, url)
                if status == "CORRUPTED" and reason == "auth_or_checkpoint_redirect":
                    auth_streak = 1
                else:
                    auth_streak = 0

            excel_io.write_status_cell(wb=wb, ctx=ctx, excel_row=excel_row, status=status)

            if status == "OK":
                print(f"[row {excel_row}] OK")
            else:
                print(f"[row {excel_row}] CORRUPTED — {reason}")

        excel_io.save_workbook(wb, xlsx_path)
        wb.close()
        wb = None

        print("")
        print("Done. Results saved into the SAME Excel file (URL Status column).")
        print("Browser left open for manual review — close Chrome when finished.")
        return 0

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        try:
            if wb is not None:
                excel_io.save_workbook(wb, xlsx_path)
                wb.close()
        except Exception:
            traceback.print_exc()
        print("Partial progress saved (best effort). Browser left open.")
        return 130

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        _show_error_dialog("Run failed", str(e))
        try:
            if wb is not None:
                excel_io.save_workbook(wb, xlsx_path)
                wb.close()
        except Exception:
            pass
        return 1

    finally:
        if driver is not None:
            browser.retain_chrome_session(driver)
            try:
                _ = driver.window_handles
                print("(Not calling driver.quit(); Chrome stays open.)")
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
