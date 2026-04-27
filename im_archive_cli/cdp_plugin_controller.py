from __future__ import annotations

import base64
import json
import os
import re
import signal
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import websocket

from .config import AppConfig


def _json_get(url: str, timeout: float = 3.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _detect_chrome_binary(config_path: str) -> str:
    if config_path:
        p = Path(config_path)
        if p.exists():
            return str(p)
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        os.environ.get("EDGE_PATH", ""),
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/microsoft-edge",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise RuntimeError("未找到 Chrome/Edge 可执行文件，请在 config.yaml 设置 chrome_path")


def _spawn_detached_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        if creationflags:
            kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    return kwargs


def parse_extension_id_from_target_url(target_url: str) -> str:
    match = re.match(r"^chrome-extension://([a-p]{32})/", str(target_url or ""))
    if not match:
        raise ValueError(f"invalid extension url: {target_url}")
    return match.group(1)


@dataclass
class ChromeRuntime:
    pid: int
    port: int
    started_at: float


class CDPClient:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=10)
        self.seq = 0

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.seq += 1
        msg = {"id": self.seq, "method": method, "params": params or {}}
        self.ws.send(json.dumps(msg, ensure_ascii=False))
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") != self.seq:
                continue
            if "error" in data:
                raise RuntimeError(f"CDP error {method}: {data['error']}")
            return data.get("result", {})


class CDPPluginController:
    def __init__(self, cfg: AppConfig, repo_root: Path):
        self.cfg = cfg
        self.repo_root = repo_root
        self._profile_path = Path(cfg.profile_dir).expanduser()
        if not self._profile_path.is_absolute():
            self._profile_path = (repo_root / self._profile_path).resolve()
        self.state_file = Path(cfg.chrome_state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    @property
    def _version_url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.cdp_port}/json/version"

    @property
    def _targets_url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.cdp_port}/json/list"

    def _is_cdp_alive(self) -> bool:
        try:
            _json_get(self._version_url, timeout=1.5)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _load_runtime(self) -> ChromeRuntime | None:
        if not self.state_file.exists():
            return None
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return ChromeRuntime(pid=int(data["pid"]), port=int(data["port"]), started_at=float(data["started_at"]))
        except Exception:  # noqa: BLE001
            return None

    def _save_runtime(self, rt: ChromeRuntime) -> None:
        self.state_file.write_text(
            json.dumps({"pid": rt.pid, "port": rt.port, "started_at": rt.started_at}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def start_chrome_plain(self, headed: bool = True) -> ChromeRuntime:
        chrome = _detect_chrome_binary(self.cfg.chrome_path)
        profile = self._profile_path
        profile.mkdir(parents=True, exist_ok=True)
        extension_arg = self._resolve_load_extension_arg()
        args = [
            chrome,
            f"--user-data-dir={profile}",
            f"--load-extension={extension_arg}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if not headed:
            args.append("--headless=new")
        proc = subprocess.Popen(args, **_spawn_detached_kwargs())  # noqa: S603
        rt = ChromeRuntime(pid=proc.pid, port=0, started_at=time.time())
        self._save_runtime(rt)
        return rt

    def start_chrome(self, headed: bool = False, force_new: bool = False) -> ChromeRuntime:
        if self._is_cdp_alive() and not force_new:
            rt = self._load_runtime()
            if rt:
                return rt
        if self._is_cdp_alive() and force_new:
            raise RuntimeError(
                f"CDP 端口 {self.cfg.cdp_port} 已被占用，请先关闭占用进程后重试，或修改 config.yaml 中 cdp_port"
            )
        chrome = _detect_chrome_binary(self.cfg.chrome_path)
        profile = self._profile_path
        profile.mkdir(parents=True, exist_ok=True)
        extension_arg = self._resolve_load_extension_arg()
        args = [
            chrome,
            f"--remote-debugging-port={self.cfg.cdp_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            f"--load-extension={extension_arg}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if not headed:
            args.append("--headless=new")
        proc = subprocess.Popen(args, **_spawn_detached_kwargs())  # noqa: S603
        rt = ChromeRuntime(pid=proc.pid, port=self.cfg.cdp_port, started_at=time.time())
        self._save_runtime(rt)
        for _ in range(50):
            if self._is_cdp_alive():
                self._wait_extension_ready(timeout_sec=10.0)
                return rt
            time.sleep(0.2)
        raise RuntimeError("Chrome CDP 启动超时")

    def _resolve_load_extension_arg(self) -> str:
        """
        Resolve and sanitize extension directories for --load-extension.
        Chrome rejects names that start with "_", so we only keep paths that:
        1) are directories
        2) contain manifest.json
        3) directory name does not start with "_"
        """
        raw = str(self.cfg.extension_dir or "").strip()
        if not raw or raw == ".":
            candidates = [self.repo_root]
        else:
            # allow comma-separated paths in config
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            candidates = [Path(p) for p in parts] if parts else [self.repo_root]

        resolved: list[Path] = []
        for c in candidates:
            p = c.expanduser()
            if not p.is_absolute():
                p = (self.repo_root / p).resolve() if str(c) == "." else p.resolve()
            else:
                p = p.resolve()
            if p.is_file():
                p = p.parent
            if p.name.startswith("_"):
                continue
            if (p / "manifest.json").exists():
                resolved.append(p)

        # hard fallback to repo root extension
        if not resolved and (self.repo_root / "manifest.json").exists() and not self.repo_root.name.startswith("_"):
            resolved = [self.repo_root.resolve()]

        if not resolved:
            raise RuntimeError(
                "未找到可加载的扩展目录。请在 config.yaml 设置 extension_dir 为包含 manifest.json 的目录。"
            )
        return ",".join(str(p) for p in resolved)

    def _wait_extension_ready(self, timeout_sec: float = 10.0) -> str:
        deadline = time.time() + timeout_sec
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                return self._find_extension_id()
            except Exception as err:  # noqa: BLE001
                last_err = err
                time.sleep(0.2)
        raise RuntimeError(f"扩展未就绪，请检查 extension_dir 配置: {last_err}")

    def ensure_chrome(self, headed: bool = False) -> ChromeRuntime:
        rt = self._load_runtime()
        if rt and rt.port == 0 and rt.pid > 0:
            # A previous non-debug launch may hold the profile lock.
            self._terminate_pid(rt.pid)
            self._wait_cdp_down(timeout_sec=2.0)
        if self._is_cdp_alive():
            try:
                self._wait_extension_ready(timeout_sec=5.0)
                if rt:
                    return rt
                rt = ChromeRuntime(pid=-1, port=self.cfg.cdp_port, started_at=time.time())
                self._save_runtime(rt)
                return rt
            except Exception:  # noqa: BLE001
                if rt and rt.pid > 0:
                    self._terminate_pid(rt.pid)
                    self._wait_cdp_down(timeout_sec=8.0)
                    return self.start_chrome(headed=headed, force_new=True)
                raise RuntimeError(
                    f"检测到 CDP 端口 {self.cfg.cdp_port} 已有 Chrome 但未加载本插件。"
                    "请关闭该 Chrome 后重试，或修改 config.yaml 的 cdp_port 为新端口。"
                )
        return self.start_chrome(headed=headed)

    def _terminate_pid(self, pid: int) -> None:
        if pid <= 0:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)  # noqa: S603
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass

    def _wait_cdp_down(self, timeout_sec: float = 8.0) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if not self._is_cdp_alive():
                return
            time.sleep(0.2)

    def _get_browser_ws_url(self) -> str:
        version = _json_get(self._version_url)
        ws = version.get("webSocketDebuggerUrl")
        if not ws:
            raise RuntimeError("CDP version 中未找到 webSocketDebuggerUrl")
        return str(ws)

    def _list_targets(self) -> list[dict[str, Any]]:
        return list(_json_get(self._targets_url))

    def _find_extension_id(self) -> str:
        targets = self._list_targets()
        for t in targets:
            url = str(t.get("url") or "")
            if not url.startswith("chrome-extension://"):
                continue
            return parse_extension_id_from_target_url(url)
        ext_id = self._find_extension_id_from_preferences()
        if ext_id:
            return ext_id
        raise RuntimeError("未发现扩展 target，请确认扩展已通过 --load-extension 加载")

    def _find_extension_id_from_preferences(self) -> str | None:
        pref_path = self._profile_path / "Default" / "Preferences"
        if not pref_path.exists():
            return None
        try:
            data = json.loads(pref_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        settings = (((data.get("extensions") or {}).get("settings")) or {})
        if not isinstance(settings, dict):
            return None
        extension_roots = [p.strip().lower().replace("/", "\\") for p in self._resolve_load_extension_arg().split(",")]
        for ext_id, meta in settings.items():
            if not re.fullmatch(r"[a-p]{32}", str(ext_id)):
                continue
            if not isinstance(meta, dict):
                continue
            path = str(meta.get("path") or "").lower().replace("/", "\\")
            location = int(meta.get("location") or 0)
            state = int(meta.get("state") or 0)
            if state != 1:
                continue
            if location == 4 and path and any(root and root in path for root in extension_roots):
                return str(ext_id)
        # fallback: any enabled unpacked extension
        for ext_id, meta in settings.items():
            if not re.fullmatch(r"[a-p]{32}", str(ext_id)):
                continue
            if not isinstance(meta, dict):
                continue
            location = int(meta.get("location") or 0)
            state = int(meta.get("state") or 0)
            if location == 4 and state == 1:
                return str(ext_id)
        return None

    def get_extension_id(self) -> str:
        return self._wait_extension_ready(timeout_sec=10.0)

    def _open_popup_and_get_page_ws(self, extension_id: str) -> str:
        browser = CDPClient(self._get_browser_ws_url())
        popup_url = f"chrome-extension://{extension_id}/popup.html"
        target_id = None
        try:
            created = browser.call("Target.createTarget", {"url": popup_url})
            target_id = created.get("targetId")
            if not target_id:
                raise RuntimeError("创建 popup target 失败")
        finally:
            browser.close()

        for _ in range(50):
            targets = self._list_targets()
            # Preferred: the exact target we just created
            for t in targets:
                if t.get("targetId") == target_id:
                    ws = t.get("webSocketDebuggerUrl")
                    if ws:
                        return str(ws)
            # Fallback: any popup.html page target for this extension
            for t in targets:
                url = str(t.get("url") or "")
                if url.startswith(popup_url):
                    ws = t.get("webSocketDebuggerUrl")
                    if ws:
                        return str(ws)
            time.sleep(0.1)
        raise RuntimeError("等待 popup target webSocketDebuggerUrl 超时")

    def _eval_in_popup(self, expression: str, await_promise: bool = True) -> Any:
        extension_id = self._find_extension_id()
        ws = self._open_popup_and_get_page_ws(extension_id)
        page = CDPClient(ws)
        try:
            page.call("Runtime.enable")
            result = page.call(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "awaitPromise": await_promise,
                    "returnByValue": True,
                },
            )
            if result.get("exceptionDetails"):
                raise RuntimeError(f"popup evaluate 异常: {result['exceptionDetails']}")
            return result.get("result", {}).get("value")
        finally:
            page.close()

    def call_extension(self, msg_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = {"type": msg_type, **(payload or {})}
        encoded = json.dumps(data, ensure_ascii=False)
        expr = f"""
        (async () => {{
          const req = {encoded};
          return await chrome.runtime.sendMessage(req);
        }})()
        """
        result = self._eval_in_popup(expr, await_promise=True)
        if not isinstance(result, dict):
            raise RuntimeError(f"插件响应格式异常: {result}")
        return result

    def get_state(self) -> dict[str, Any]:
        result = self.call_extension("getState")
        if result.get("status") != "ok":
            raise RuntimeError(result.get("message") or "getState failed")
        return dict(result.get("data") or {})

    def wait_until(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout_sec: float = 300.0,
        interval_sec: float | None = None,
    ) -> dict[str, Any]:
        interval = interval_sec or float(self.cfg.cdp_poll_interval_sec)
        deadline = time.time() + timeout_sec
        last_state: dict[str, Any] = {}
        while time.time() < deadline:
            last_state = self.get_state()
            if predicate(last_state):
                return last_state
            time.sleep(interval)
        raise TimeoutError(f"等待状态超时，最后状态: {last_state}")

    def open_vbooking_tab(self) -> int:
        expr_open = f"""
        (async () => {{
          const tab = await chrome.tabs.create({{ url: {json.dumps(self.cfg.vbooking_url)}, active: true }});
          return tab.id;
        }})()
        """
        tab_id = self._eval_in_popup(expr_open, await_promise=True)
        return int(tab_id)

    def get_active_vbooking_tab_id(self, force_open: bool = False) -> int:
        expr = """
        (async () => {
          const tabs = await chrome.tabs.query({ currentWindow: true, active: true });
          const tab = tabs && tabs.length ? tabs[0] : null;
          return tab ? { id: tab.id, url: tab.url || "" } : null;
        })()
        """
        value = self._eval_in_popup(expr, await_promise=True)
        if isinstance(value, dict) and "vbooking.ctrip.com" in str(value.get("url", "")):
            return int(value["id"])

        # try all tabs
        expr_all = """
        (async () => {
          const tabs = await chrome.tabs.query({});
          const target = tabs.find(t => (t.url || "").includes("vbooking.ctrip.com"));
          return target ? { id: target.id, url: target.url || "" } : null;
        })()
        """
        value = self._eval_in_popup(expr_all, await_promise=True)
        if isinstance(value, dict) and value.get("id") is not None:
            return int(value["id"])
        if force_open:
            expr_open = f"""
            (async () => {{
              const tab = await chrome.tabs.create({{ url: {json.dumps(self.cfg.vbooking_url)}, active: true }});
              return tab.id;
            }})()
            """
            tab_id = self._eval_in_popup(expr_open, await_promise=True)
            return int(tab_id)
        raise RuntimeError("未找到 vbooking 标签页，请先登录并打开目标页面")

    def import_links_preview(self, file_path: Path) -> dict[str, Any]:
        base64_data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return self.call_extension("importLinksWorkbookPreview", {"filename": file_path.name, "base64": base64_data})

    def import_links_apply(self, file_path: Path) -> dict[str, Any]:
        base64_data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return self.call_extension("importLinksWorkbook", {"filename": file_path.name, "base64": base64_data})
