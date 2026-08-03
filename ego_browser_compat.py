"""Small Selenium-shaped adapter backed by the ego-browser CLI.

This module exists to keep the surrounding desktop GUI and data-processing
code stable while browser operations move from Selenium to ego-browser.
It intentionally implements only the WebDriver surface used by the personal
automation programs under this Develop directory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


_RESULT_MARKER = "__EGO_BROWSER_RESULT__"
_ELEMENT_KEY = "__ego_browser_element__"


class WebDriverException(RuntimeError):
    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.msg = message
        self.screen = None
        self.stacktrace = None


class TimeoutException(WebDriverException):
    pass


class NoSuchElementException(WebDriverException):
    pass


class ElementClickInterceptedException(WebDriverException):
    pass


class StaleElementReferenceException(WebDriverException):
    pass


class JavascriptException(WebDriverException):
    pass


class NoSuchWindowException(WebDriverException):
    pass


class InvalidArgumentException(WebDriverException):
    pass


class ElementNotInteractableException(WebDriverException):
    pass


class By:
    ID = "id"
    XPATH = "xpath"
    LINK_TEXT = "link text"
    PARTIAL_LINK_TEXT = "partial link text"
    NAME = "name"
    TAG_NAME = "tag name"
    CLASS_NAME = "class name"
    CSS_SELECTOR = "css selector"


class Keys:
    NULL = "\ue000"
    CANCEL = "\ue001"
    HELP = "\ue002"
    BACKSPACE = "\ue003"
    TAB = "\ue004"
    CLEAR = "\ue005"
    RETURN = "\ue006"
    ENTER = "\ue007"
    SHIFT = "\ue008"
    CONTROL = "\ue009"
    ALT = "\ue00a"
    PAUSE = "\ue00b"
    ESCAPE = "\ue00c"
    SPACE = "\ue00d"
    PAGE_UP = "\ue00e"
    PAGE_DOWN = "\ue00f"
    END = "\ue010"
    HOME = "\ue011"
    ARROW_LEFT = "\ue012"
    LEFT = ARROW_LEFT
    ARROW_UP = "\ue013"
    UP = ARROW_UP
    ARROW_RIGHT = "\ue014"
    RIGHT = ARROW_RIGHT
    ARROW_DOWN = "\ue015"
    DOWN = ARROW_DOWN
    INSERT = "\ue016"
    DELETE = "\ue017"
    SEMICOLON = "\ue018"
    EQUALS = "\ue019"
    COMMAND = "\ue03d"
    META = COMMAND


_KEY_NAMES = {
    Keys.BACKSPACE: "Backspace",
    Keys.TAB: "Tab",
    Keys.RETURN: "Enter",
    Keys.ENTER: "Enter",
    Keys.SHIFT: "Shift",
    Keys.CONTROL: "Control",
    Keys.ALT: "Alt",
    Keys.ESCAPE: "Escape",
    Keys.SPACE: " ",
    Keys.PAGE_UP: "PageUp",
    Keys.PAGE_DOWN: "PageDown",
    Keys.END: "End",
    Keys.HOME: "Home",
    Keys.ARROW_LEFT: "ArrowLeft",
    Keys.ARROW_UP: "ArrowUp",
    Keys.ARROW_RIGHT: "ArrowRight",
    Keys.ARROW_DOWN: "ArrowDown",
    Keys.INSERT: "Insert",
    Keys.DELETE: "Delete",
    Keys.COMMAND: "Meta",
}


class _Options:
    def __init__(self) -> None:
        self.arguments: list[str] = []
        self.experimental_options: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.binary_location: str | None = None

    def add_argument(self, argument: str) -> None:
        self.arguments.append(argument)

    def add_experimental_option(self, name: str, value: Any) -> None:
        self.experimental_options[name] = value

    def set_capability(self, name: str, value: Any) -> None:
        self.capabilities[name] = value


class ChromeOptions(_Options):
    pass


class EdgeOptions(_Options):
    pass


Options = ChromeOptions


class Service:
    def __init__(self, executable_path: str | None = None, *args, **kwargs) -> None:
        self.executable_path = executable_path


ChromeService = Service
EdgeService = Service


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _browser_pack_source() -> str:
    return r"""
const __egoPack = (value, seen = new WeakSet()) => {
  if (value === undefined || value === null) return null;
  if (value instanceof Element) {
    if (!value.dataset.egoBridgeId) {
      value.dataset.egoBridgeId = crypto.randomUUID();
    }
    return {
      __ego_browser_element__: value.dataset.egoBridgeId,
      tag_name: value.tagName.toLowerCase(),
      text: value.innerText ?? value.textContent ?? ""
    };
  }
  if (value instanceof NodeList || value instanceof HTMLCollection) {
    return Array.from(value, item => __egoPack(item, seen));
  }
  if (Array.isArray(value)) return value.map(item => __egoPack(item, seen));
  if (typeof value === "object") {
    if (seen.has(value)) return null;
    seen.add(value);
    const output = {};
    for (const [key, item] of Object.entries(value)) {
      try { output[key] = __egoPack(item, seen); } catch (_) {}
    }
    return output;
  }
  if (typeof value === "function" || typeof value === "symbol") return String(value);
  return value;
};
"""


class EgoBrowserDriver:
    def __init__(self, *args, options: _Options | None = None, **kwargs) -> None:
        self.options = options or ChromeOptions()
        self._task_id: int | None = None
        self._task_name = f"automation-{Path(os.sys.argv[0]).stem}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._lock = threading.RLock()
        self._implicit_wait = 0.0
        self._page_load_timeout = 30.0
        self._frame_element_id: str | None = None
        self._closed = False
        self._ensure_task()

    def _node(self, body: str, *, timeout: float | None = None) -> Any:
        if self._closed:
            raise WebDriverException("ego-browser driver is already closed")
        selector = self._task_id if self._task_id is not None else self._task_name
        source = (
            f"const __egoTask = await useOrCreateTaskSpace({_json(selector)});"
            + body
            + f"\ncliLog({_json(_RESULT_MARKER)} + JSON.stringify(__egoResult ?? null));\n"
        )
        with self._lock:
            try:
                completed = subprocess.run(
                    ["ego-browser", "nodejs"],
                    input=source,
                    text=True,
                    capture_output=True,
                    timeout=timeout or max(self._page_load_timeout + 10, 40),
                    check=False,
                )
            except FileNotFoundError as exc:
                raise WebDriverException(
                    "未找到 ego-browser。请先安装并启动 ego lite，再重试。"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise TimeoutException("ego-browser operation timed out") from exc

        combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        marker_lines = [line for line in combined.splitlines() if _RESULT_MARKER in line]
        if completed.returncode != 0 or not marker_lines:
            message = combined.strip() or f"ego-browser exited with code {completed.returncode}"
            if "Unknown ref" in message or "not found" in message.lower():
                raise NoSuchElementException(message)
            raise WebDriverException(message)
        payload = marker_lines[-1].split(_RESULT_MARKER, 1)[1]
        try:
            return self._unpack(json.loads(payload))
        except json.JSONDecodeError as exc:
            raise WebDriverException(f"Invalid ego-browser response: {payload}") from exc

    def _ensure_task(self) -> None:
        result = self._node("const __egoResult = { taskId: __egoTask.id };")
        self._task_id = int(result["taskId"])

    def _unpack(self, value: Any) -> Any:
        if isinstance(value, dict) and _ELEMENT_KEY in value:
            return EgoWebElement(self, str(value[_ELEMENT_KEY]))
        if isinstance(value, list):
            return [self._unpack(item) for item in value]
        if isinstance(value, dict):
            return {key: self._unpack(item) for key, item in value.items()}
        return value

    def _root_source(self) -> str:
        if not self._frame_element_id:
            return "document"
        element_id = _json(self._frame_element_id)
        return (
            f"(document.querySelector('[data-ego-bridge-id=\"' + {element_id} + '\"]')"
            f"?.contentDocument)"
        )

    def _evaluate(self, expression: str, *, timeout: float | None = None) -> Any:
        source = (
            _browser_pack_source()
            + f"\nconst __egoValue = await js({_json(expression)});"
            + "\nconst __egoResult = __egoValue;"
        )
        return self._node(source, timeout=timeout)

    def _element_selector(self, element_id: str) -> str:
        return f'[data-ego-bridge-id="{element_id}"]'

    def _resolve_argument(self, value: Any) -> Any:
        if isinstance(value, EgoWebElement):
            return {_ELEMENT_KEY: value._element_id}
        if isinstance(value, list):
            return [self._resolve_argument(item) for item in value]
        if isinstance(value, tuple):
            return [self._resolve_argument(item) for item in value]
        if isinstance(value, dict):
            return {key: self._resolve_argument(item) for key, item in value.items()}
        return value

    def get(self, url: str) -> None:
        body = f"""
let __egoTab = await ensureRealTab();
if (__egoTab) {{
  await gotoAndWait({_json(url)}, {{ timeout: {max(1, int(self._page_load_timeout))}, settle: 1 }});
}} else {{
  __egoTab = await openOrReuseTab({_json(url)}, {{ wait: true, timeout: {max(1, int(self._page_load_timeout))} }});
  for (const __egoOther of await listTabs()) {{
    if (__egoOther.targetId !== __egoTab.targetId && __egoOther.url.startsWith('chrome://newtab')) {{
      await closeTab(__egoOther.targetId);
    }}
  }}
}}
const __egoResult = true;
"""
        self._node(body)
        self._frame_element_id = None

    def find_element(self, by: str = By.ID, value: str | None = None) -> "EgoWebElement":
        deadline = time.monotonic() + self._implicit_wait
        while True:
            elements = self.find_elements(by, value)
            if elements:
                return elements[0]
            if time.monotonic() >= deadline:
                raise NoSuchElementException(f"No element found by {by}: {value}")
            time.sleep(0.1)

    def find_elements(self, by: str = By.ID, value: str | None = None) -> list["EgoWebElement"]:
        return self._find_elements(self._root_source(), by, value or "")

    def _find_elements(self, root_source: str, by: str, value: str) -> list["EgoWebElement"]:
        expression = f"""(() => {{
{_browser_pack_source()}
const root = {root_source};
if (!root) return [];
const by = {_json(by)};
const value = {_json(value)};
let found = [];
if (by === "xpath") {{
  const result = document.evaluate(value, root, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
  for (let i = 0; i < result.snapshotLength; i++) found.push(result.snapshotItem(i));
}} else if (by === "id") {{
  const item = root.querySelector("#" + CSS.escape(value));
  if (item) found = [item];
}} else if (by === "name") {{
  found = Array.from(root.querySelectorAll("[name=" + JSON.stringify(value) + "]"));
}} else if (by === "tag name") {{
  found = Array.from(root.getElementsByTagName(value));
}} else if (by === "class name") {{
  found = Array.from(root.getElementsByClassName(value));
}} else if (by === "link text" || by === "partial link text") {{
  found = Array.from(root.querySelectorAll("a")).filter(a =>
    by === "link text" ? a.textContent.trim() === value : a.textContent.includes(value)
  );
}} else {{
  found = Array.from(root.querySelectorAll(value));
}}
return __egoPack(found);
}})()"""
        result = self._evaluate(expression)
        return result if isinstance(result, list) else []

    def execute_script(self, script: str, *args) -> Any:
        packed_args = self._resolve_argument(list(args))
        expression = f"""(() => {{
{_browser_pack_source()}
const resolve = value => {{
  if (Array.isArray(value)) return value.map(resolve);
  if (value && typeof value === "object" && value.{_ELEMENT_KEY}) {{
    return document.querySelector('[data-ego-bridge-id="' + value.{_ELEMENT_KEY} + '"]');
  }}
  if (value && typeof value === "object") {{
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, resolve(v)]));
  }}
  return value;
}};
const args = resolve({_json(packed_args)});
const fn = new Function("args", "return (function(){{\\n" + {_json(script)} + "\\n}}).apply(null, args)");
return __egoPack(fn(args));
}})()"""
        return self._evaluate(expression)

    def execute_async_script(self, script: str, *args) -> Any:
        packed_args = self._resolve_argument(list(args))
        expression = f"""(async () => {{
{_browser_pack_source()}
const resolve = value => {{
  if (Array.isArray(value)) return value.map(resolve);
  if (value && typeof value === "object" && value.{_ELEMENT_KEY}) {{
    return document.querySelector('[data-ego-bridge-id="' + value.{_ELEMENT_KEY} + '"]');
  }}
  return value;
}};
const args = resolve({_json(packed_args)});
const result = await new Promise((resolvePromise, rejectPromise) => {{
  const callback = value => resolvePromise(value);
  try {{
    const fn = new Function("args", "callback", "return (function(){{\\n" + {_json(script)} + "\\n}}).apply(null, [...args, callback])");
    fn(args, callback);
  }} catch (error) {{ rejectPromise(error); }}
}});
return __egoPack(result);
}})()"""
        return self._evaluate(expression, timeout=max(self._page_load_timeout + 10, 40))

    def execute_cdp_cmd(self, method: str, params: dict | None = None) -> Any:
        body = f"const __egoResult = await cdp({_json(method)}, {_json(params or {})});"
        return self._node(body)

    def implicitly_wait(self, seconds: float) -> None:
        self._implicit_wait = max(0.0, float(seconds))

    def set_page_load_timeout(self, seconds: float) -> None:
        self._page_load_timeout = max(1.0, float(seconds))

    def maximize_window(self) -> None:
        return None

    def set_window_size(self, width: int, height: int) -> None:
        self.execute_cdp_cmd(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": int(width),
                "height": int(height),
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )

    def refresh(self) -> None:
        self._node("await gotoAndWait((await pageInfo()).url, { timeout: 30, settle: 1 }); const __egoResult = true;")

    def back(self) -> None:
        self._evaluate("history.back(); true")
        time.sleep(0.5)

    def forward(self) -> None:
        self._evaluate("history.forward(); true")
        time.sleep(0.5)

    @property
    def current_url(self) -> str:
        return str(self._node("const __egoResult = (await pageInfo()).url;"))

    @property
    def title(self) -> str:
        return str(self._node("const __egoResult = (await pageInfo()).title;"))

    @property
    def page_source(self) -> str:
        return str(self._evaluate("document.documentElement.outerHTML"))

    @property
    def window_handles(self) -> list[str]:
        result = self._node("const __egoResult = (await listTabs()).map(tab => tab.targetId);")
        return [str(item) for item in result]

    @property
    def current_window_handle(self) -> str:
        result = self._node(
            "const __egoResult = (await listTabs()).find(tab => tab.active)?.targetId ?? null;"
        )
        if not result:
            raise WebDriverException("No active ego-browser tab")
        return str(result)

    @property
    def switch_to(self) -> "_SwitchTo":
        return _SwitchTo(self)

    def close(self) -> None:
        self._node("await closeTab(); const __egoResult = true;")
        self._frame_element_id = None

    def quit(self) -> None:
        if self._closed:
            return
        task_id = self._task_id
        if task_id is None:
            self._closed = True
            return
        source = (
            f"const __egoDone = await completeTaskSpace({_json(task_id)}, {{ keep: false }});"
            f"\ncliLog({_json(_RESULT_MARKER)} + JSON.stringify(__egoDone));\n"
        )
        with self._lock:
            completed = subprocess.run(
                ["ego-browser", "nodejs"],
                input=source,
                text=True,
                capture_output=True,
                timeout=40,
                check=False,
            )
        self._closed = True
        if completed.returncode != 0:
            raise WebDriverException((completed.stderr or completed.stdout).strip())

    def get_cookies(self) -> list[dict]:
        result = self.execute_cdp_cmd("Storage.getCookies", {})
        return list(result.get("cookies", [])) if isinstance(result, dict) else []

    def add_cookie(self, cookie: dict) -> None:
        params = dict(cookie)
        if "expiry" in params:
            params["expires"] = params.pop("expiry")
        allowed = {
            "name",
            "value",
            "url",
            "domain",
            "path",
            "secure",
            "httpOnly",
            "sameSite",
            "expires",
            "priority",
            "sameParty",
            "sourceScheme",
            "sourcePort",
            "partitionKey",
        }
        params = {key: value for key, value in params.items() if key in allowed}
        if "url" not in params and "domain" not in params:
            params["url"] = self.current_url
        result = self.execute_cdp_cmd("Network.setCookie", params)
        if isinstance(result, dict) and result.get("success") is False:
            raise WebDriverException(f"Unable to add cookie {params.get('name')}")

    def save_screenshot(self, filename: str) -> bool:
        result = self._node("const __egoResult = await captureScreenshot();")
        shutil.copyfile(str(result), filename)
        return True

    @property
    def log_types(self) -> list[str]:
        return ["performance"]

    def get_log(self, log_type: str) -> list[dict]:
        if log_type != "performance":
            return []
        events = self._node("const __egoResult = await drainEvents();")
        output = []
        for event in events or []:
            output.append(
                {
                    "level": "INFO",
                    "timestamp": int(time.time() * 1000),
                    "message": json.dumps({"message": event}, ensure_ascii=False),
                }
            )
        return output


class EgoWebElement:
    def __init__(self, driver: EgoBrowserDriver, element_id: str) -> None:
        self._driver = driver
        self._element_id = element_id

    @property
    def id(self) -> str:
        return self._element_id

    def _expression(self, body: str) -> Any:
        selector = _json(self._driver._element_selector(self._element_id))
        expression = f"""(() => {{
{_browser_pack_source()}
const element = document.querySelector({selector});
if (!element) throw new Error("Stale ego-browser element: {self._element_id}");
return __egoPack((() => {{ {body} }})());
}})()"""
        try:
            return self._driver._evaluate(expression)
        except WebDriverException as exc:
            if "Stale ego-browser element" in str(exc):
                raise StaleElementReferenceException(str(exc)) from exc
            raise

    @property
    def text(self) -> str:
        return str(self._expression("return element.innerText ?? element.textContent ?? '';"))

    @property
    def tag_name(self) -> str:
        return str(self._expression("return element.tagName.toLowerCase();"))

    @property
    def size(self) -> dict:
        return dict(
            self._expression(
                "const r = element.getBoundingClientRect(); return {width: r.width, height: r.height};"
            )
        )

    @property
    def rect(self) -> dict:
        return dict(
            self._expression(
                "const r = element.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height};"
            )
        )

    def find_element(self, by: str = By.ID, value: str | None = None) -> "EgoWebElement":
        elements = self.find_elements(by, value)
        if not elements:
            raise NoSuchElementException(f"No descendant found by {by}: {value}")
        return elements[0]

    def find_elements(self, by: str = By.ID, value: str | None = None) -> list["EgoWebElement"]:
        selector = _json(self._driver._element_selector(self._element_id))
        root = f"document.querySelector({selector})"
        return self._driver._find_elements(root, by, value or "")

    def click(self) -> None:
        selector = self._driver._element_selector(self._element_id)
        body = f"await click({_json(selector)}); const __egoResult = true;"
        try:
            self._driver._node(body)
        except WebDriverException as exc:
            raise ElementClickInterceptedException(str(exc)) from exc

    def clear(self) -> None:
        self._expression(
            "element.focus(); element.value = ''; "
            "element.dispatchEvent(new InputEvent('input', {bubbles:true,inputType:'deleteContentBackward'})); "
            "element.dispatchEvent(new Event('change', {bubbles:true})); return true;"
        )

    def send_keys(self, *values) -> None:
        text = "".join(str(value) for value in values)
        selector = self._driver._element_selector(self._element_id)
        if not any(char in _KEY_NAMES or char == Keys.NULL for char in text):
            body = (
                f"const __egoCurrent = await js({_json(f'document.querySelector({_json(selector)})?.value ?? \"\"')});"
                f"await fillInput({_json(selector)}, String(__egoCurrent ?? '') + {_json(text)});"
                "const __egoResult = true;"
            )
            self._driver._node(body)
            return
        actions = [f"await click({_json(selector)});"]
        plain = ""
        for char in text:
            key_name = _KEY_NAMES.get(char)
            if key_name:
                if plain:
                    actions.append(f"await typeText({_json(plain)});")
                    plain = ""
                actions.append(f"await pressKey({_json(key_name)});")
            elif char != Keys.NULL:
                plain += char
        if plain:
            actions.append(f"await typeText({_json(plain)});")
        actions.append("const __egoResult = true;")
        self._driver._node("".join(actions))

    def submit(self) -> None:
        self._expression(
            "const form = element.closest('form'); "
            "if (form?.requestSubmit) form.requestSubmit(); else if (form) form.submit(); "
            "return Boolean(form);"
        )

    def get_attribute(self, name: str) -> Any:
        return self._expression(
            f"return element.getAttribute({_json(name)}) ?? "
            f"({_json(name)} in element ? element[{_json(name)}] : null);"
        )

    def get_property(self, name: str) -> Any:
        return self._expression(f"return element[{_json(name)}];")

    def value_of_css_property(self, name: str) -> str:
        return str(
            self._expression(f"return getComputedStyle(element).getPropertyValue({_json(name)});")
        )

    def is_displayed(self) -> bool:
        return bool(
            self._expression(
                "const s=getComputedStyle(element),r=element.getBoundingClientRect();"
                "return s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)!==0&&r.width>0&&r.height>0;"
            )
        )

    def is_enabled(self) -> bool:
        return bool(self._expression("return !element.disabled && element.getAttribute('aria-disabled') !== 'true';"))

    def is_selected(self) -> bool:
        return bool(self._expression("return Boolean(element.selected || element.checked);"))


class _SwitchTo:
    def __init__(self, driver: EgoBrowserDriver) -> None:
        self._driver = driver

    def window(self, handle: str) -> None:
        self._driver._node(f"await switchTab({_json(handle)}); const __egoResult = true;")
        self._driver._frame_element_id = None

    def new_window(self, kind: str = "tab") -> None:
        result = self._driver._node(
            "const __egoTab = await openOrReuseTab("
            "'data:text/html,<title>New%20Tab</title>#' + Date.now() + Math.random(), "
            "{ wait: true, timeout: 10 });"
            "const __egoResult = __egoTab;"
        )
        target_id = result.get("targetId") if isinstance(result, dict) else None
        if not target_id:
            raise WebDriverException("ego-browser could not create a new tab")
        self._driver._frame_element_id = None

    def frame(self, frame_reference: EgoWebElement) -> None:
        self._driver._frame_element_id = frame_reference._element_id

    def default_content(self) -> None:
        self._driver._frame_element_id = None


class WebDriverWait:
    def __init__(
        self,
        driver: EgoBrowserDriver | EgoWebElement,
        timeout: float,
        poll_frequency: float = 0.2,
        ignored_exceptions: tuple[type[Exception], ...] | None = None,
    ) -> None:
        self._driver = driver
        self._timeout = float(timeout)
        self._poll = float(poll_frequency)
        self._ignored = ignored_exceptions or (NoSuchElementException,)

    def until(self, method: Callable, message: str = "") -> Any:
        deadline = time.monotonic() + self._timeout
        last_exception: Exception | None = None
        while time.monotonic() < deadline:
            try:
                value = method(self._driver)
                if value:
                    return value
            except self._ignored as exc:
                last_exception = exc
            time.sleep(self._poll)
        detail = message or str(last_exception or "condition was not met")
        raise TimeoutException(detail)

    def until_not(self, method: Callable, message: str = "") -> Any:
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                value = method(self._driver)
                if not value:
                    return value
            except self._ignored:
                return True
            time.sleep(self._poll)
        raise TimeoutException(message or "condition remained true")


class _ExpectedConditions:
    @staticmethod
    def presence_of_element_located(locator: tuple[str, str]) -> Callable:
        return lambda driver: driver.find_element(*locator)

    @staticmethod
    def presence_of_all_elements_located(locator: tuple[str, str]) -> Callable:
        return lambda driver: driver.find_elements(*locator) or False

    @staticmethod
    def visibility_of_element_located(locator: tuple[str, str]) -> Callable:
        def predicate(driver):
            element = driver.find_element(*locator)
            return element if element.is_displayed() else False

        return predicate

    @staticmethod
    def element_to_be_clickable(mark: tuple[str, str] | EgoWebElement) -> Callable:
        def predicate(driver):
            element = driver.find_element(*mark) if isinstance(mark, tuple) else mark
            return element if element.is_displayed() and element.is_enabled() else False

        return predicate

    @staticmethod
    def none_of(*conditions: Callable) -> Callable:
        return lambda driver: not any(condition(driver) for condition in conditions)


EC = expected_conditions = _ExpectedConditions()


class ActionChains:
    def __init__(self, driver: EgoBrowserDriver) -> None:
        self._driver = driver
        self._actions: list[tuple] = []
        self._current_element: EgoWebElement | None = None

    def move_to_element(self, element: EgoWebElement) -> "ActionChains":
        self._current_element = element
        self._actions.append(("hover", element, None, None))
        return self

    def move_to_element_with_offset(
        self, element: EgoWebElement, xoffset: float, yoffset: float
    ) -> "ActionChains":
        self._current_element = element
        self._actions.append(("hover", element, float(xoffset), float(yoffset)))
        return self

    def move_by_offset(self, xoffset: float, yoffset: float) -> "ActionChains":
        self._actions.append(("move-offset", float(xoffset), float(yoffset), None))
        return self

    def click(self, on_element: EgoWebElement | None = None) -> "ActionChains":
        self._actions.append(("click", on_element or self._current_element, None, None))
        return self

    def pause(self, seconds: float) -> "ActionChains":
        self._actions.append(("pause", float(seconds), None, None))
        return self

    def send_keys(self, *keys_to_send) -> "ActionChains":
        self._actions.append(("keys", "".join(str(value) for value in keys_to_send), None, None))
        return self

    def key_down(self, value: str, element: EgoWebElement | None = None) -> "ActionChains":
        self._actions.append(("down", value, element, None))
        return self

    def key_up(self, value: str, element: EgoWebElement | None = None) -> "ActionChains":
        self._actions.append(("up", value, element, None))
        return self

    def perform(self) -> None:
        body: list[str] = []
        for action, value, extra, extra2 in self._actions:
            if action == "hover":
                selector = self._driver._element_selector(value._element_id)
                if extra is None:
                    body.append(f"await hover({_json(selector)});")
                else:
                    body.append(
                        f"await hover({{selector:{_json(selector)},x:{extra},y:{extra2 or 0}}});"
                    )
            elif action == "move-offset":
                body.append(f"await hover([{value}, {extra}]);")
            elif action == "click":
                if value is None:
                    body.append("await pressKey('Enter');")
                else:
                    selector = self._driver._element_selector(value._element_id)
                    body.append(f"await click({_json(selector)});")
            elif action == "pause":
                body.append(f"await wait({value});")
            elif action == "keys":
                plain = ""
                for char in value:
                    key_name = _KEY_NAMES.get(char)
                    if key_name:
                        if plain:
                            body.append(f"await typeText({_json(plain)});")
                            plain = ""
                        body.append(f"await pressKey({_json(key_name)});")
                    elif char != Keys.NULL:
                        plain += char
                if plain:
                    body.append(f"await typeText({_json(plain)});")
            elif action in {"down", "up"}:
                key_name = _KEY_NAMES.get(value, value)
                event_type = "keyDown" if action == "down" else "keyUp"
                body.append(
                    f"await dispatchKey({_json(key_name)}, {{ type: {_json(event_type)} }});"
                )
        body.append("const __egoResult = true;")
        self._driver._node("".join(body))
        self._actions.clear()


class Select:
    def __init__(self, element: EgoWebElement) -> None:
        self._element = element

    def select_by_visible_text(self, text: str) -> None:
        self._element._expression(
            f"const option=Array.from(element.options).find(o=>o.text.trim()==={_json(text)});"
            "if(!option) throw new Error('Option not found');"
            "element.value=option.value;element.dispatchEvent(new Event('change',{bubbles:true}));return true;"
        )

    def select_by_value(self, value: str) -> None:
        self._element._expression(
            f"element.value={_json(value)};element.dispatchEvent(new Event('change',{{bubbles:true}}));return true;"
        )

    def select_by_index(self, index: int) -> None:
        self._element._expression(
            f"element.selectedIndex={int(index)};element.dispatchEvent(new Event('change',{{bubbles:true}}));return true;"
        )


class _WebDriverNamespace:
    Chrome = EgoBrowserDriver
    Edge = EgoBrowserDriver
    ChromeOptions = ChromeOptions
    EdgeOptions = EdgeOptions


webdriver = _WebDriverNamespace()
WebDriver = EgoBrowserDriver
