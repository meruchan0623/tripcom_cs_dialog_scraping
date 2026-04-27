from __future__ import annotations

import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from im_archive_cli.config import load_or_create_config
from im_archive_cli.imx_cli import (
    cmd_auth_login,
    cmd_chrome_start,
    cmd_import_links,
    cmd_roles_list,
    cmd_roles_select,
    cmd_run_collect,
    cmd_run_export,
    cmd_state_watch,
)
from im_archive_cli.utils import setup_logger


class TkLogger:
    def __init__(self, text: tk.Text):
        self._text = text
        self._lock = threading.Lock()

    def info(self, msg: str) -> None:
        self._append(msg)

    def _append(self, msg: str) -> None:
        def _write() -> None:
            with self._lock:
                self._text.insert(tk.END, msg + "\n")
                self._text.see(tk.END)

        self._text.after(0, _write)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("IMX CDP 控制台")
        self.geometry("980x720")
        self._running = False

        self.config_path = tk.StringVar(value="config.yaml")
        self.chrome_headed = tk.BooleanVar(value=False)
        self.page_size = tk.StringVar(value="100")
        self.max_pages = tk.StringVar(value="")
        self.roles_include = tk.StringVar(value="")
        self.import_file = tk.StringVar(value="")

        self._build_ui()

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Config:").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.config_path, width=66).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="选择", command=self._choose_config).pack(side=tk.LEFT)
        ttk.Checkbutton(top, text="Chrome有头启动", variable=self.chrome_headed).pack(side=tk.RIGHT)

        row1 = ttk.LabelFrame(self, text="Chrome / 认证", padding=8)
        row1.pack(fill=tk.X, padx=10, pady=6)
        ttk.Button(row1, text="启动Chrome+扩展", command=lambda: self._run_async(self._chrome_start)).pack(side=tk.LEFT)
        ttk.Button(row1, text="登录 (auth login)", command=lambda: self._run_sync(self._auth_login)).pack(side=tk.LEFT, padx=8)
        ttk.Button(row1, text="状态快照", command=lambda: self._run_async(self._state_once)).pack(side=tk.LEFT)

        row2 = ttk.LabelFrame(self, text="采集 / 角色", padding=8)
        row2.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(row2, text="page_size").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.page_size, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Label(row2, text="max_pages(提示用)").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.max_pages, width=10).pack(side=tk.LEFT, padx=4)
        ttk.Button(row2, text="run collect", command=lambda: self._run_async(self._run_collect)).pack(side=tk.LEFT, padx=8)
        ttk.Button(row2, text="roles list", command=lambda: self._run_async(self._roles_list)).pack(side=tk.LEFT, padx=8)
        ttk.Button(row2, text="roles select --all", command=lambda: self._run_async(self._roles_select_all)).pack(side=tk.LEFT)
        ttk.Label(row2, text="include(csv)").pack(side=tk.LEFT, padx=(10, 3))
        ttk.Entry(row2, textvariable=self.roles_include, width=26).pack(side=tk.LEFT)
        ttk.Button(row2, text="应用", command=lambda: self._run_async(self._roles_select_include)).pack(side=tk.LEFT, padx=6)

        row3 = ttk.LabelFrame(self, text="导入 / 导出", padding=8)
        row3.pack(fill=tk.X, padx=10, pady=6)
        ttk.Entry(row3, textvariable=self.import_file, width=54).pack(side=tk.LEFT)
        ttk.Button(row3, text="选择xlsx", command=self._choose_import_file).pack(side=tk.LEFT, padx=6)
        ttk.Button(row3, text="import preview", command=lambda: self._run_async(self._import_preview)).pack(side=tk.LEFT)
        ttk.Button(row3, text="import confirm", command=lambda: self._run_async(self._import_confirm)).pack(side=tk.LEFT, padx=6)

        row4 = ttk.LabelFrame(self, text="导出任务", padding=8)
        row4.pack(fill=tk.X, padx=10, pady=6)
        ttk.Button(row4, text="export singlefile", command=lambda: self._run_async(lambda: self._run_export("singlefile"))).pack(
            side=tk.LEFT
        )
        ttk.Button(row4, text="export structured", command=lambda: self._run_async(lambda: self._run_export("structured"))).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(row4, text="export links", command=lambda: self._run_async(lambda: self._run_export("links"))).pack(side=tk.LEFT)

        log_box = ttk.LabelFrame(self, text="运行日志", padding=8)
        log_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.log_text = tk.Text(log_box, wrap=tk.WORD, height=26)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(log_box, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.logger = TkLogger(self.log_text)

    def _choose_config(self) -> None:
        p = filedialog.askopenfilename(title="选择 config.yaml", filetypes=[("YAML", "*.yaml *.yml"), ("All files", "*.*")])
        if p:
            self.config_path.set(p)

    def _choose_import_file(self) -> None:
        p = filedialog.askopenfilename(title="选择 links xlsx", filetypes=[("Excel", "*.xlsx"), ("All files", "*.*")])
        if p:
            self.import_file.set(p)

    def _runtime(self):
        cfg = load_or_create_config(Path(self.config_path.get().strip() or "config.yaml"))
        logger = setup_logger(Path(cfg.log_dir))

        class CombinedLogger:
            def info(self_inner, msg: str) -> None:
                self.logger.info(msg)
                logger.info(msg)

        return cfg, CombinedLogger()

    def _set_running(self, value: bool) -> None:
        self._running = value
        self.config(cursor="watch" if value else "")

    def _run_async(self, fn) -> None:
        if self._running:
            messagebox.showwarning("请稍候", "已有任务在运行")
            return

        def worker() -> None:
            self._set_running(True)
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self.logger.info(traceback.format_exc())
                self.after(0, lambda: messagebox.showerror("执行失败", str(exc)))
            finally:
                self._set_running(False)

        threading.Thread(target=worker, daemon=True).start()

    def _run_sync(self, fn) -> None:
        if self._running:
            messagebox.showwarning("请稍候", "已有任务在运行")
            return
        self._set_running(True)
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self.logger.info(traceback.format_exc())
            messagebox.showerror("执行失败", str(exc))
        finally:
            self._set_running(False)

    def _chrome_start(self) -> None:
        cfg, logger = self._runtime()
        code = cmd_chrome_start(cfg, logger, headed=self.chrome_headed.get())
        self.logger.info(f"chrome start exit={code}")

    def _auth_login(self) -> None:
        cfg, logger = self._runtime()
        messagebox.showinfo("登录提示", "将打开有头 Chrome，请在页面完成登录，回到终端按回车确认。")
        code = cmd_auth_login(cfg, logger)
        self.logger.info(f"auth login exit={code}")

    def _run_collect(self) -> None:
        cfg, logger = self._runtime()
        ps = int(self.page_size.get().strip() or "100")
        max_pages = self.max_pages.get().strip()
        mp = int(max_pages) if max_pages else None
        code = cmd_run_collect(cfg, logger, page_size=ps, max_pages=mp)
        self.logger.info(f"run collect exit={code}")

    def _roles_list(self) -> None:
        cfg, _ = self._runtime()
        code = cmd_roles_list(cfg)
        self.logger.info(f"roles list exit={code}")

    def _roles_select_all(self) -> None:
        cfg, _ = self._runtime()
        code = cmd_roles_select(cfg, all_roles=True, include=None)
        self.logger.info(f"roles select all exit={code}")

    def _roles_select_include(self) -> None:
        cfg, _ = self._runtime()
        code = cmd_roles_select(cfg, all_roles=False, include=self.roles_include.get().strip())
        self.logger.info(f"roles select include exit={code}")

    def _import_preview(self) -> None:
        cfg, _ = self._runtime()
        fp = Path(self.import_file.get().strip())
        if not fp.exists():
            messagebox.showwarning("文件不存在", str(fp))
            return
        code = cmd_import_links(cfg, fp, preview_only=True, confirm=False)
        self.logger.info(f"import preview exit={code}")

    def _import_confirm(self) -> None:
        cfg, _ = self._runtime()
        fp = Path(self.import_file.get().strip())
        if not fp.exists():
            messagebox.showwarning("文件不存在", str(fp))
            return
        code = cmd_import_links(cfg, fp, preview_only=False, confirm=True)
        self.logger.info(f"import confirm exit={code}")

    def _run_export(self, kind: str) -> None:
        cfg, logger = self._runtime()
        code = cmd_run_export(cfg, logger, kind=kind)
        self.logger.info(f"run export {kind} exit={code}")

    def _state_once(self) -> None:
        cfg, _ = self._runtime()
        code = cmd_state_watch(cfg, interval_sec=1.0, once=True)
        self.logger.info(f"state watch --once exit={code}")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

