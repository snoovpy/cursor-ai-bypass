# main.py

import sys
import os
import yaml
import json
import argparse
import logging
import platform
from pathlib import Path

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.service import Service as FirefoxService
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.firefox import GeckoDriverManager

# Local modules
from machine_reset import reset_machine_identifiers
from version_bypass import reset_current_version
from cursor_registration import register_cursor_account

# Only Temp-Mail-Plus, no mail.tm API
from email_automation import get_temp_email, wait_for_confirmation_email, confirm_email_link

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        logging.error(f"Config file not found: {path}")
        sys.exit(1)

    if p.suffix in (".yaml", ".yml"):
        with p.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    elif p.suffix == ".json":
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    else:
        logging.error("Unsupported config format; use .yaml, .yml, or .json")
        sys.exit(1)


def get_webdriver(browser: str, driver_path: str, headless: bool):
    """
    Instantiate a “stealthy” Selenium WebDriver for Chrome or Firefox.
    """
    try:
        if browser.lower() == "chrome":
            from selenium.webdriver.chrome.options import Options

            opts = Options()
            if headless:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)

            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=opts)

            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                    """
                }
            )
            return driver

        elif browser.lower() == "firefox":
            from selenium.webdriver.firefox.options import Options

            opts = Options()
            opts.headless = headless
            opts.set_preference("dom.webdriver.enabled", False)
            opts.set_preference("useAutomationExtension", False)

            service = FirefoxService(GeckoDriverManager().install())
            driver = webdriver.Firefox(service=service, options=opts)

            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            return driver

        else:
            logging.error("Unsupported browser specified. Use 'chrome' or 'firefox'.")
            sys.exit(1)

    except Exception as e:
        logging.error(f"Failed to start WebDriver ({browser}): {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Cursor Automation (Reset + Bypass + Register)")
    parser.add_argument(
        "--config", "-c", default="config.yaml",
        help="Path to config file (YAML or JSON)."
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Only reset Cursor machine IDs/storage/DB (no registration)."
    )
    parser.add_argument(
        "--bypass-version", action="store_true",
        help="Only bypass Cursor version (no machine reset / registration)."
    )
    parser.add_argument(
        "--register", action="store_true",
        help="Only register a new Cursor account via temp-mail-plus & confirm it."
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run everything: reset, bypass-version, register & confirm."
    )
    args = parser.parse_args()

    config = load_config(args.config)

    # Expand any tilde‐prefixed paths
    for key in ("cursor_path", "storage_json_path", "sqlite_db_path", "machine_id_path"):
        if config.get(key):
            config[key] = Path(os.path.expanduser(config[key]))

    do_reset = args.reset or args.all
    do_bypass = args.bypass_version or args.all
    do_register = args.register or args.all

    if not (do_reset or do_bypass or do_register):
        logging.error("No action specified. Use --reset, --bypass-version, --register, or --all.")
        sys.exit(1)

    # ──────────── 1) RESET MACHINE IDs & STORAGE/DB ───────────────────────────────
    if do_reset:
        reset_machine_identifiers(config)

    # ──────────── 2) BYPASS VERSION ────────────────────────────────────────────────
    if do_bypass:
        cp = config.get("cursor_path")
        if not cp:
            os_type = platform.system()
            if os_type == "Windows":
                cp = Path(os.getenv("ProgramFiles", "C:\\Program Files")) / "Cursor"
            elif os_type == "Darwin":
                cp = Path("/Applications/Cursor.app/Contents/Resources/app")
            elif os_type == "Linux":
                possible = Path("/opt/Cursor/squashfs-root/usr/share/cursor/resources/app")
                cp = possible if possible.exists() else None
            else:
                cp = None

        if not cp or not cp.exists():
            logging.error("Cannot bypass version; cursor_path unknown or not found.")
        else:
            logging.info(f"➜  Bypassing version (force {config.get('target_version')}) at {cp}...")
            success = reset_current_version(cp, config.get("target_version", "0.48.7"))
            if success:
                logging.info("✔ Version bypass complete.")
            else:
                logging.error("✘ Version bypass failed.")

    # ──────────── 3) REGISTRATION + EMAIL CONFIRMATION ──────────────────────────────
    if do_register and not config.get("skip_registration", False):
        browser = config.get("browser", "chrome")
        driver_path = config.get("driver_path")
        headless = config.get("headless", False)

        driver = get_webdriver(browser, driver_path, headless)
        try:
            # 3a) Get a Temp-Mail-Plus address
            temp_email = get_temp_email(driver, timeout=30)
            if not temp_email:
                logging.error("Cannot obtain a Temp-Mail-Plus address. Aborting.")
                driver.quit()
                return
            logging.info(f"  ↪ Using disposable email: {temp_email}")

            # 3b) Sign up on Cursor
            first_name = config.get("first_name", "").strip()
            last_name = config.get("last_name", "").strip()
            if not first_name or not last_name:
                logging.error("Please specify 'first_name' and 'last_name' in config.")
                driver.quit()
                return

            cursor_pass = config.get("password")
            if not cursor_pass:
                logging.error("No 'password' in config. Aborting registration.")
                driver.quit()
                return

            success = register_cursor_account(driver, first_name, last_name, temp_email, cursor_pass)
            if not success:
                logging.error("Cursor registration failed. Aborting.")
                driver.quit()
                return

            # 3c) Poll Temp-Mail-Plus for the confirmation email
            confirm_url = wait_for_confirmation_email(
                driver,
                timeout=config.get("email_poll_timeout", 180),
                poll_interval=config.get("email_poll_interval", 5)
            )
            if not confirm_url:
                logging.error("Did not receive confirmation email. Aborting.")
                driver.quit()
                return

            # 3d) Click the confirmation link
            confirmed = confirm_email_link(driver, confirm_url)
            if not confirmed:
                logging.error("Email confirmation failed.")
            else:
                logging.info("✔ Registration & email confirmation succeeded.")

        finally:
            try:
                driver.quit()
            except Exception:
                pass

    elif do_register and config.get("skip_registration", False):
        logging.info("Skipping registration (skip_registration=True).")

    logging.info("All requested actions complete.")


if __name__ == "__main__":
    main()
