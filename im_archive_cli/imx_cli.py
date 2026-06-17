from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .cdp_proxy_export import CdpProxyClient, export_singlefile_via_cdp_proxy, export_structured_via_cdp_proxy
from .ctrip_http import CtripImCdpFetchClient, CtripImHttpClient, default_date_range
from .cdp_plugin_controller import CDPPluginController
from .config import AppConfig, load_or_create_config
from .models import RunSummary, SessionRecord
from .state import StateStore
from .utils import setup_logger
from .xlsx_io import export_links_xlsx, import_links_xlsx, preview_sessions


def _parse_csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _make_controller(cfg: AppConfig) -> CDPPluginController:
    repo_root = Path(__file__).resolve().parents[1]
    if not cfg.extension_dir:
        cfg.extension_dir = str(repo_root)
    elif cfg.extension_dir == ".":
        cfg.extension_dir = str(repo_root)
    return CDPPluginController(cfg, repo_root=repo_root)


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def cmd_run_collect_http(
    cfg: AppConfig,
    logger,
    page_size: int | None,
    max_pages: int | None,
    start_date: str | None,
    end_date: str | None,
    include: str | None,
    via: str = "http",
) -> int:
    if not start_date or not end_date:
        default_start, default_end = default_date_range()
        start_date = start_date or default_start
        end_date = end_date or default_end
    size = int(page_size or cfg.page_size)
    max_page = int(max_pages or cfg.max_pages)
    include_roles = set(_parse_csv(include)) if include else None
    if via == "cdp":
        client = CtripImCdpFetchClient(cfg, log=logger.info)
        logger.info(f"使用 CDP 页面上下文模拟前端请求采集: {start_date} -> {end_date}, page_size={size}, max_pages={max_page}")
    else:
        client = CtripImHttpClient(cfg, log=logger.info)
        logger.info(f"使用 HTTP 模拟前端请求采集: {start_date} -> {end_date}, page_size={size}, max_pages={max_page}")
    sessions = client.collect_sessions(start_date, end_date, page_size=size, max_pages=max_page, include_roles=include_roles)
    StateStore(Path(cfg.state_file)).set_sessions(sessions, auto_select_all=True)
    logger.info(f"采集结束: collected={len(sessions)}")
    return 0


def cmd_run_collect_browser(cfg: AppConfig, logger, page_size: int | None, max_pages: int | None) -> int:
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
    raw_sessions: list = []
    collected = controller.call_extension("getCollectedSessions")
    if collected.get("status") == "ok" and isinstance(collected.get("sessions"), list):
        raw_sessions = collected.get("sessions") or []
    if isinstance(raw_sessions, list):
        sessions = [SessionRecord.from_dict(item) for item in raw_sessions if isinstance(item, dict)]
        StateStore(Path(cfg.state_file)).set_sessions(sessions, auto_select_all=True)
        logger.info(f"已同步插件采集结果到 Python state: {len(sessions)} 条")
    logger.info(f"采集结束: phase={final_state.get('phase')} collected={final_state.get('collectedCount')}")
    return 0


def cmd_roles_list(cfg: AppConfig) -> int:
    store = StateStore(Path(cfg.state_file))
    state = store.load()
    available = state.get("available_roles") or []
    selected = set(state.get("selected_roles") or [])
    if not available:
        print("暂无角色，请先 run collect 或 import links")
        return 0
    for role in available:
        mark = "*" if role in selected else " "
        print(f"[{mark}] {role}")
    return 0


def cmd_roles_select(cfg: AppConfig, all_roles: bool, include: str | None) -> int:
    store = StateStore(Path(cfg.state_file))
    state = store.load()
    available = state.get("available_roles") or []
    if not available:
        print("暂无可选角色")
        return 1
    chosen = available if all_roles else _parse_csv(include)
    selected = store.set_selected_roles(chosen)
    print("已选角色:")
    for x in selected:
        print(f"- {x}")
    return 0


def cmd_run_export(cfg: AppConfig, logger, kind: str, formats: str | None = None, output: str | None = None) -> int:
    store = StateStore(Path(cfg.state_file))
    sessions = store.filtered_sessions()
    if not sessions:
        raise RuntimeError("未选中任何客服角色或所选角色下无会话，请先 run collect/import links 并 roles select")
    summary = RunSummary(kind=kind, started_at=_now_utc_iso(), finished_at="", total=len(sessions), success=0, failed=0)
    repo_root = Path(__file__).resolve().parents[1]
    if kind == "links":
        out_path = Path(output) if output else Path(cfg.output_dir) / f"{cfg.output_prefix}_links.xlsx"
        export_links_xlsx(out_path, sessions)
        summary.success = len(sessions)
        summary.failed = 0
        summary.finished_at = _now_utc_iso()
        store.set_summary(summary)
        logger.info(f"链接表导出完成: {out_path} ({len(sessions)} 条)")
        return 0

    proxy = CdpProxyClient(cfg.cdp_proxy_base_url)
    if kind == "singlefile":
        success, failed = export_singlefile_via_cdp_proxy(proxy, repo_root, cfg, sessions, logger.info)
    elif kind == "structured":
        selected_formats = _parse_csv(formats) or ["json"]
        invalid = [f for f in selected_formats if f not in {"json", "markdown"}]
        if invalid:
            raise RuntimeError(f"未知结构化导出格式: {', '.join(invalid)}")
        success, failed = export_structured_via_cdp_proxy(proxy, repo_root, cfg, sessions, selected_formats, logger.info)
    else:
        raise RuntimeError(f"未知导出类型: {kind}")
    summary.success = success
    summary.failed = failed
    summary.finished_at = _now_utc_iso()
    store.set_summary(summary)
    logger.info(f"导出结束: kind={kind} success={success} failed={failed}")
    return 0


def cmd_import_links(cfg: AppConfig, file_path: Path, preview_only: bool, confirm: bool) -> int:
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    sessions = import_links_xlsx(file_path)
    preview = preview_sessions(sessions)
    print(f"会话总数: {preview.total_sessions}")
    print(f"客服数量: {preview.total_roles}")
    print("客服明细:")
    for role in preview.roles:
        print(f"- {role.cs_name}: {role.count}")

    if preview_only and not confirm:
        return 0
    if not confirm:
        ans = input("确认导入并覆盖当前会话池? [y/N]: ").strip().lower()
        if ans not in {"y", "yes"}:
            print("已取消导入")
            return 3
    StateStore(Path(cfg.state_file)).set_sessions(sessions, auto_select_all=True)
    print(f"导入成功：{len(sessions)} 条会话")
    return 0


def cmd_state_watch(cfg: AppConfig, interval_sec: float, once: bool = False) -> int:
    store = StateStore(Path(cfg.state_file))
    while True:
        state = store.load()
        all_sessions = state.get("collected_sessions") or []
        selected = set(state.get("selected_roles") or [])
        done = 0
        fail = 0
        summary = state.get("last_run_summary") or {}
        if isinstance(summary, dict):
            done = int(summary.get("success") or 0)
            fail = int(summary.get("failed") or 0)
        print(
            f"phase=ready running=False paused=False total={len(all_sessions)} done={done} fail={fail} "
            f"collected={len(all_sessions)} selectedRoles={len(selected)}"
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
    collect.add_argument("--start-date", default=None, help="YYYY-MM-DD，默认昨天")
    collect.add_argument("--end-date", default=None, help="YYYY-MM-DD，默认同 start-date")
    collect.add_argument("--include", default=None, help="逗号分隔客服账号/昵称/显示名")
    collect.add_argument("--via", choices=["cdp", "http", "browser"], default="cdp", help="cdp=已登录页面内 fetch；http=纯 requests；browser=旧扩展点击采集")
    export = run_sub.add_parser("export")
    export.add_argument("--kind", choices=["singlefile", "structured", "links"], required=True)
    export.add_argument("--formats", default=None, help="structured 导出格式，逗号分隔: json,markdown")
    export.add_argument("--output", default=None, help="links 导出路径")

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
        if args.via == "browser":
            return cmd_run_collect_browser(cfg, logger, args.page_size, args.max_pages)
        return cmd_run_collect_http(cfg, logger, args.page_size, args.max_pages, args.start_date, args.end_date, args.include, via=args.via)
    if args.command == "run" and args.run_command == "export":
        return cmd_run_export(cfg, logger, args.kind, formats=args.formats, output=args.output)
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
