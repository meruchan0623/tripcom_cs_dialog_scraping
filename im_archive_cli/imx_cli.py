from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .cdp_plugin_controller import CDPPluginController
from .config import AppConfig, load_or_create_config
from .utils import setup_logger


def _parse_csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _make_controller(cfg: AppConfig) -> CDPPluginController:
    repo_root = Path(__file__).resolve().parents[1]
    if not cfg.extension_dir:
        cfg.extension_dir = str(repo_root)
    elif cfg.extension_dir == ".":
        cfg.extension_dir = str(repo_root)
    return CDPPluginController(cfg, repo_root=repo_root)


def _wait_for_task_done(controller: CDPPluginController, logger, timeout_sec: int = 7200) -> dict:
    last_log_count = 0
    started = time.time()
    while True:
        state = controller.get_state()
        running = bool(state.get("running"))
        logs = state.get("log") or []
        if isinstance(logs, list) and len(logs) > last_log_count:
            for line in logs[last_log_count:]:
                logger.info(line)
            last_log_count = len(logs)
        if not running:
            return state
        if time.time() - started > timeout_sec:
            raise TimeoutError("任务超时未完成")
        time.sleep(1.0)


def cmd_chrome_start(cfg: AppConfig, logger, headed: bool = False, debug: bool = False) -> int:
    controller = _make_controller(cfg)
    if debug:
        rt = controller.ensure_chrome(headed=headed)
        extension_id = controller.get_extension_id()
        logger.info(f"Chrome 已启动/连接(调试): pid={rt.pid}, port={rt.port}, extension_id={extension_id}")
    else:
        rt = controller.start_chrome_plain(headed=headed)
        logger.info(f"Chrome 已启动(非调试): pid={rt.pid}")
    return 0


def cmd_auth_login(cfg: AppConfig, logger) -> int:
    controller = _make_controller(cfg)
    controller.ensure_chrome(headed=True)
    tab_id = controller.open_vbooking_tab()
    logger.info(f"已打开登录页面 tabId={tab_id}，请手动登录并进入 IM 页面")
    input("登录完成后按 Enter 继续...")
    logger.info("登录会话已保存在持久 profile")
    return 0


def cmd_run_collect(cfg: AppConfig, logger, page_size: int | None, max_pages: int | None) -> int:
    controller = _make_controller(cfg)
    controller.ensure_chrome(headed=False)
    # 每次 collect 前同步节流配置到插件，确保 CLI 和 GUI 的默认节奏一致
    config_payload = {
        "concurrency": int(cfg.concurrency or 20),
        "delayBetweenSaves": int(max(1, int(cfg.window_sec or 20)) * 1000),
    }
    if page_size:
        config_payload["pageSize"] = int(page_size)
    if max_pages:
        logger.info("提示: max_pages 由插件内部 MAX_PAGES 控制，当前仅记录在日志，不会直接改插件常量")
    if config_payload:
        r = controller.call_extension("setConfig", {"config": config_payload})
        if r.get("status") != "ok":
            raise RuntimeError(r.get("message") or "setConfig 失败")

    tab_id = controller.get_active_vbooking_tab_id(force_open=True)
    logger.info(f"使用 tabId={tab_id} 发起会话采集")
    r = controller.call_extension("start", {"tabId": tab_id})
    if r.get("status") != "ok":
        raise RuntimeError(r.get("message") or "start 失败")
    final_state = _wait_for_task_done(controller, logger)
    logger.info(f"采集结束: phase={final_state.get('phase')} collected={final_state.get('collectedCount')}")
    return 0


def cmd_roles_list(cfg: AppConfig) -> int:
    controller = _make_controller(cfg)
    controller.ensure_chrome(headed=False)
    s = controller.get_state()
    available = s.get("availableCsRoles") or []
    selected = set(s.get("selectedCsRoles") or [])
    if not available:
        print("暂无角色，请先 run collect 或 import links")
        return 0
    for role in available:
        mark = "*" if role in selected else " "
        print(f"[{mark}] {role}")
    return 0


def cmd_roles_select(cfg: AppConfig, all_roles: bool, include: str | None) -> int:
    controller = _make_controller(cfg)
    controller.ensure_chrome(headed=False)
    s = controller.get_state()
    available = s.get("availableCsRoles") or []
    if not available:
        print("暂无可选角色")
        return 1
    chosen = available if all_roles else _parse_csv(include)
    r = controller.call_extension("setSelectedCsRoles", {"roles": chosen})
    if r.get("status") != "ok":
        raise RuntimeError(r.get("message") or "setSelectedCsRoles 失败")
    print("已选角色:")
    for x in r.get("selectedCsRoles") or []:
        print(f"- {x}")
    return 0


def cmd_run_export(cfg: AppConfig, logger, kind: str) -> int:
    controller = _make_controller(cfg)
    controller.ensure_chrome(headed=False)
    mapping = {
        "singlefile": "archiveSingleFile",
        "structured": "exportStructured",
        "links": "exportLinksWorkbook",
    }
    msg_type = mapping.get(kind)
    if not msg_type:
        raise RuntimeError(f"未知导出类型: {kind}")
    r = controller.call_extension(msg_type)
    if r.get("status") != "ok":
        raise RuntimeError(r.get("message") or f"{msg_type} 失败")
    final_state = _wait_for_task_done(controller, logger)
    logger.info(
        f"导出结束: kind={kind} phase={final_state.get('phase')} "
        f"success={final_state.get('completedSessions')} failed={final_state.get('failedSessions')}"
    )
    return 0


def cmd_import_links(cfg: AppConfig, file_path: Path, preview_only: bool, confirm: bool) -> int:
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    controller = _make_controller(cfg)
    controller.ensure_chrome(headed=False)
    preview = controller.import_links_preview(file_path)
    if preview.get("status") != "ok":
        raise RuntimeError(preview.get("message") or "import preview 失败")
    preview_data = preview.get("preview") or {}
    print(f"会话总数: {preview_data.get('totalSessions', 0)}")
    print(f"客服数量: {preview_data.get('totalRoles', 0)}")
    print("客服明细:")
    for role in preview_data.get("roles") or []:
        print(f"- {role.get('csName')}: {role.get('count')}")

    if preview_only and not confirm:
        return 0
    if not confirm:
        ans = input("确认导入并覆盖当前会话池? [y/N]: ").strip().lower()
        if ans not in {"y", "yes"}:
            print("已取消导入")
            return 3
    result = controller.import_links_apply(file_path)
    if result.get("status") != "ok":
        raise RuntimeError(result.get("message") or "import apply 失败")
    print(result.get("message") or "导入成功")
    return 0


def cmd_state_watch(cfg: AppConfig, interval_sec: float, once: bool = False) -> int:
    controller = _make_controller(cfg)
    controller.ensure_chrome(headed=False)
    while True:
        state = controller.get_state()
        print(
            f"phase={state.get('phase')} running={state.get('running')} paused={state.get('paused')} "
            f"total={state.get('totalSessions')} done={state.get('completedSessions')} fail={state.get('failedSessions')} "
            f"collected={state.get('collectedCount')}"
        )
        if once:
            return 0
        time.sleep(interval_sec)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="imx", description="CDP-driven Chrome extension controller")
    parser.add_argument("--config", default="config.yaml", help="config.yaml path")
    sub = parser.add_subparsers(dest="command")

    chrome = sub.add_parser("chrome")
    chrome_sub = chrome.add_subparsers(dest="chrome_command")
    chrome_start = chrome_sub.add_parser("start")
    chrome_start.add_argument("--headed", action="store_true", help="启动有头 Chrome")
    chrome_start.add_argument("--debug", action="store_true", help="使用 CDP 调试模式启动（供自动化命令控制）")

    auth = sub.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="auth_command")
    auth_sub.add_parser("login")

    run = sub.add_parser("run")
    run_sub = run.add_subparsers(dest="run_command")
    collect = run_sub.add_parser("collect")
    collect.add_argument("--page-size", type=int, default=None)
    collect.add_argument("--max-pages", type=int, default=None)
    export = run_sub.add_parser("export")
    export.add_argument("--kind", choices=["singlefile", "structured", "links"], required=True)

    roles = sub.add_parser("roles")
    roles_sub = roles.add_subparsers(dest="roles_command")
    roles_sub.add_parser("list")
    roles_select = roles_sub.add_parser("select")
    roles_select.add_argument("--all", action="store_true")
    roles_select.add_argument("--include", default=None, help="逗号分隔角色名")

    imp = sub.add_parser("import")
    imp_sub = imp.add_subparsers(dest="import_command")
    imp_links = imp_sub.add_parser("links")
    imp_links.add_argument("--file", required=True)
    imp_links.add_argument("--preview", action="store_true")
    imp_links.add_argument("--confirm", action="store_true")

    state = sub.add_parser("state")
    state_sub = state.add_subparsers(dest="state_command")
    watch = state_sub.add_parser("watch")
    watch.add_argument("--interval-sec", type=float, default=1.0)
    watch.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_or_create_config(Path(args.config))
    logger = setup_logger(Path(cfg.log_dir))

    if args.command == "chrome" and args.chrome_command == "start":
        return cmd_chrome_start(cfg, logger, headed=args.headed, debug=args.debug)
    if args.command == "auth" and args.auth_command == "login":
        return cmd_auth_login(cfg, logger)
    if args.command == "run" and args.run_command == "collect":
        return cmd_run_collect(cfg, logger, args.page_size, args.max_pages)
    if args.command == "run" and args.run_command == "export":
        return cmd_run_export(cfg, logger, args.kind)
    if args.command == "roles" and args.roles_command == "list":
        return cmd_roles_list(cfg)
    if args.command == "roles" and args.roles_command == "select":
        return cmd_roles_select(cfg, args.all, args.include)
    if args.command == "import" and args.import_command == "links":
        return cmd_import_links(cfg, Path(args.file), preview_only=args.preview, confirm=args.confirm)
    if args.command == "state" and args.state_command == "watch":
        return cmd_state_watch(cfg, args.interval_sec, once=args.once)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
