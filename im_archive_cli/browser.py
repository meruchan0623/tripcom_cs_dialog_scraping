from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.remote.webdriver import WebDriver

from .config import AppConfig


@contextmanager
def persistent_context(config: AppConfig, headless: bool | None = None) -> Iterator[WebDriver]:
    profile_dir = Path(config.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    mode = config.headless if headless is None else headless
    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir.resolve()}")
    options.add_argument("--window-size=1440,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    if mode:
        options.add_argument("--headless=new")
    driver = uc.Chrome(options=options, use_subprocess=True)
    try:
        yield driver
    finally:
        driver.quit()


def get_or_create_page(driver: WebDriver, url: str | None = None) -> WebDriver:
    if url:
        driver.get(url)
    return driver


def require_logged_page(driver: WebDriver, expected_host: str) -> None:
    host = driver.current_url.lower()
    if expected_host not in host:
        raise RuntimeError(f"当前页面不是目标站点: {driver.current_url}")


def execute_js(driver: WebDriver, func_source: str, *args):
    script = f"return ({func_source}).apply(null, arguments);"
    return driver.execute_script(script, *args)


def execute_js_async(driver: WebDriver, func_source: str, *args):
    script = f"""
    const done = arguments[arguments.length - 1];
    const params = Array.prototype.slice.call(arguments, 0, arguments.length - 1);
    Promise.resolve(({func_source}).apply(null, params))
      .then(result => done({{ ok: true, result }}))
      .catch(error => done({{ ok: false, error: String(error && error.message ? error.message : error) }}));
    """
    result = driver.execute_async_script(script, *args)
    if not isinstance(result, dict):
        return result
    if result.get("ok"):
        return result.get("result")
    raise RuntimeError(result.get("error") or "JavaScript async execution failed")
