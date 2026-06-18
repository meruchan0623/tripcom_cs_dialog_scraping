from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .cdp_proxy_export import CdpProxyClient, export_singlefile_via_cdp_proxy, export_structured_via_cdp_proxy
from .ctrip_http import (
    CtripImCdpFetchClient,
    CtripImDetailHttpClient,
    CtripImHttpClient,
    CtripRequestBudget,
    default_date_range,
    inspect_auth_sources,
)
from .cdp_plugin_controller import CDPPluginController
from .config import AppConfig, load_or_create_config, save_config
from .detail_discovery import discover_detail_xhr_via_cdp, discover_detail_xhr_via_proxy, inspect_cdp_status, inspect_proxy_status
from .http_export import export_structured_via_http
from .models import RunSummary, SessionRecord
from .selftest import run_http_export_selftest
from .state import StateStore
from .utils import setup_logger
from .xlsx_io import export_links_xlsx, import_links_xlsx, preview_sessions


MAX_CTRIP_REQUEST_BUDGET = 30


def _normalize_request_budget(value: int | None, context: str) -> int | None:
    if value is None:
        return None
    limit = int(value)
    if limit < 0:
        raise RuntimeError(f"{context} request-budget 不能为负数")
    if limit > MAX_CTRIP_REQUEST_BUDGET:
        raise RuntimeError(f"为避免超过携程限制，{context} request-budget 不能超过 {MAX_CTRIP_REQUEST_BUDGET}")
    return limit


def _make_request_budget(limit: int | None, ledger_path: str | None, context: str) -> CtripRequestBudget | None:
    if ledger_path and limit is None:
        raise RuntimeError(f"{context} request-ledger 必须配合 request-budget 使用")
    if limit is None:
        return None
    path = Path(ledger_path) if ledger_path else None
    return CtripRequestBudget(limit, ledger_path=path)


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


def cmd_auth_status(cfg: AppConfig) -> int:
    print(json.dumps(inspect_auth_sources(cfg), ensure_ascii=False, indent=2))
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
    request_budget: int | None = None,
    request_ledger: str | None = None,
) -> int:
    if not start_date or not end_date:
        default_start, default_end = default_date_range()
        start_date = start_date or default_start
        end_date = end_date or default_end
    size = int(page_size or cfg.page_size)
    max_page = int(max_pages or cfg.max_pages)
    include_roles = set(_parse_csv(include)) if include else None
    budget_limit = _normalize_request_budget(request_budget, "collect")
    budget = _make_request_budget(budget_limit, request_ledger, "collect")
    if via == "cdp":
        client = CtripImCdpFetchClient(cfg, log=logger.info, request_budget=budget)
        logger.info(f"使用 CDP 页面上下文模拟前端请求采集: {start_date} -> {end_date}, page_size={size}, max_pages={max_page}")
    else:
        client = CtripImHttpClient(cfg, log=logger.info, request_budget=budget)
        logger.info(f"使用 HTTP 模拟前端请求采集: {start_date} -> {end_date}, page_size={size}, max_pages={max_page}")
    try:
        sessions = client.collect_sessions(start_date, end_date, page_size=size, max_pages=max_page, include_roles=include_roles)
        StateStore(Path(cfg.state_file)).set_sessions(sessions, auto_select_all=True)
        logger.info(f"采集结束: collected={len(sessions)}")
    finally:
        if budget:
            logger.info(f"携程接口请求计数: used={budget.used}, limit={budget.limit}")
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


def cmd_run_export(
    cfg: AppConfig,
    logger,
    kind: str,
    formats: str | None = None,
    output: str | None = None,
    via: str = "cdp",
    request_budget: int | None = None,
    request_ledger: str | None = None,
) -> int:
    budget_limit = _normalize_request_budget(request_budget, "export")
    if request_ledger and budget_limit is None:
        raise RuntimeError("export request-ledger 必须配合 request-budget 使用")
    if (request_budget is not None or request_ledger) and not (kind == "structured" and via == "http"):
        raise RuntimeError("export request-budget/request-ledger 只支持 structured --via http；CDP/SingleFile/links 路径无法精确计数")
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

    budget = _make_request_budget(budget_limit, request_ledger, "export")
    if kind == "structured" and via == "http":
        selected_formats = _parse_csv(formats) or ["json"]
        invalid = [f for f in selected_formats if f not in {"json", "markdown"}]
        if invalid:
            raise RuntimeError(f"未知结构化导出格式: {', '.join(invalid)}")
        if budget and budget.remaining < len(sessions):
            raise RuntimeError(
                "export request-budget 剩余额度不足："
                f"remaining={budget.remaining}, selected_sessions={len(sessions)}；"
                "为避免中途耗尽预算，已在发出任何携程详情请求前停止"
            )
        client = CtripImDetailHttpClient(cfg, log=logger.info, request_budget=budget)
        try:
            success, failed = export_structured_via_http(client, cfg, sessions, selected_formats, logger.info)
        finally:
            if budget:
                logger.info(f"携程接口请求计数: used={budget.used}, limit={budget.limit}")
        summary.success = success
        summary.failed = failed
        summary.finished_at = _now_utc_iso()
        store.set_summary(summary)
        logger.info(f"导出结束: kind={kind} via=http success={success} failed={failed}")
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
    logger.info(f"导出结束: kind={kind} via={via} success={success} failed={failed}")
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


def cmd_discover_detail_xhr(
    cfg: AppConfig,
    logger,
    session_id: str,
    request_budget: int,
    wait_sec: float,
    output: str | None = None,
    via: str = "cdp",
    cdp_base_url: str | None = None,
    request_ledger: str | None = None,
) -> int:
    budget_limit = _normalize_request_budget(request_budget, "detail-xhr")
    assert budget_limit is not None
    budget = _make_request_budget(budget_limit, request_ledger, "detail-xhr")
    effective_limit = budget.remaining if budget else budget_limit
    if request_ledger and budget and budget.remaining <= 0:
        raise RuntimeError("detail-xhr request-ledger 剩余额度为 0，已停止以避免超过携程接口请求上限")
    if via == "proxy":
        proxy = CdpProxyClient(cfg.cdp_proxy_base_url)
        try:
            result = discover_detail_xhr_via_proxy(proxy, session_id, request_budget=effective_limit, wait_sec=wait_sec, log=logger.info)
        except RuntimeError as exc:
            logger.info(f"详情 XHR 发现失败: {exc}")
            return 1
    else:
        base_url = str(cdp_base_url or f"http://127.0.0.1:{cfg.cdp_port}").rstrip("/")
        try:
            result = discover_detail_xhr_via_cdp(
                base_url,
                session_id,
                request_budget=effective_limit,
                wait_sec=wait_sec,
                log=logger.info,
            )
        except RuntimeError as exc:
            logger.info(f"详情 XHR 发现失败: {exc}")
            return 1
    payload = result.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        logger.info(f"详情 XHR 发现报告已写入: {path}")
    else:
        print(text)
    if budget:
        budget.add_used(result.used)
        logger.info(f"携程接口请求计数: used={budget.used}, limit={budget.limit}")
    else:
        logger.info(f"携程接口请求计数: used={result.used}, limit={budget_limit}")
    return 0


def cmd_discover_apply_config(cfg: AppConfig, logger, config_path: str, report_path: str) -> int:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    recommended = report.get("recommendedConfig") if isinstance(report, dict) else None
    if not isinstance(recommended, dict) or not recommended.get("ctrip_im_detail_messages_url"):
        raise RuntimeError("发现报告缺少 recommendedConfig.ctrip_im_detail_messages_url，不能写入配置")
    _validate_detail_discovery_report(report, str(recommended["ctrip_im_detail_messages_url"]))
    cfg.ctrip_im_detail_messages_url = str(recommended["ctrip_im_detail_messages_url"]).strip()
    cfg.ctrip_im_detail_page_size = int(recommended.get("ctrip_im_detail_page_size") or cfg.ctrip_im_detail_page_size or 100)
    extra = recommended.get("ctrip_im_detail_extra_body")
    cfg.ctrip_im_detail_extra_body = extra if isinstance(extra, dict) else None
    cfg.ctrip_im_detail_verified_source = "browser_detail_xhr"
    cfg.ctrip_im_detail_verified_at = _now_utc_iso()
    save_config(Path(config_path), cfg)
    logger.info(f"已写入 HTTP 详情接口配置: {cfg.ctrip_im_detail_messages_url}")
    return 0


def _validate_detail_discovery_report(report: dict[str, object], recommended_url: str) -> None:
    candidates = report.get("candidateEndpoints")
    if not isinstance(candidates, list):
        raise RuntimeError("发现报告缺少 candidateEndpoints 证据，不能写入配置")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("url") or "") != recommended_url:
            continue
        statuses = candidate.get("statuses")
        if not bool(candidate.get("looksLikeMessages")):
            raise RuntimeError("发现报告候选接口不像消息列表，不能写入配置")
        if not isinstance(statuses, list) or 200 not in [int(x) for x in statuses if str(x).isdigit()]:
            raise RuntimeError("发现报告候选接口没有 HTTP 200 响应证据，不能写入配置")
        samples = candidate.get("samples")
        if not isinstance(samples, list) or not samples:
            raise RuntimeError("发现报告候选接口缺少响应样本，不能写入配置")
        return
    raise RuntimeError("发现报告 candidateEndpoints 中找不到 recommendedConfig 对应接口，不能写入配置")


def cmd_discover_cdp_status(cfg: AppConfig, cdp_base_url: str | None = None, via: str = "cdp") -> int:
    if via == "proxy":
        print(json.dumps(inspect_proxy_status(cfg.cdp_proxy_base_url), ensure_ascii=False, indent=2))
    else:
        base_url = str(cdp_base_url or f"http://127.0.0.1:{cfg.cdp_port}").rstrip("/")
        print(json.dumps(inspect_cdp_status(base_url), ensure_ascii=False, indent=2))
    return 0


def cmd_request_budget_status(request_budget: int, request_ledger: str) -> int:
    limit = _normalize_request_budget(request_budget, "request-budget")
    assert limit is not None
    budget = CtripRequestBudget(limit, ledger_path=Path(request_ledger))
    print(
        json.dumps(
            {
                "ledger": request_ledger,
                "limit": budget.limit,
                "used": budget.used,
                "remaining": budget.remaining,
                "exceeded": budget.exceeded,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_preflight(cfg: AppConfig, request_budget: int, request_ledger: str, via: str = "proxy", cdp_base_url: str | None = None) -> int:
    limit = _normalize_request_budget(request_budget, "preflight")
    assert limit is not None
    budget = CtripRequestBudget(limit, ledger_path=Path(request_ledger))
    issues: list[str] = []
    browser_status: dict[str, object]
    auth_status = inspect_auth_sources(cfg)
    try:
        if via == "proxy":
            browser_status = inspect_proxy_status(cfg.cdp_proxy_base_url)
            if int(browser_status.get("vbookingTargetCount") or 0) <= 0 and int(browser_status.get("detailTargetCount") or 0) <= 0:
                issues.append("当前浏览器没有 vbooking.ctrip.com 或 imvendor.ctrip.com target")
        else:
            base_url = str(cdp_base_url or f"http://127.0.0.1:{cfg.cdp_port}").rstrip("/")
            browser_status = inspect_cdp_status(base_url)
            if not bool(browser_status.get("readyForDetailDiscovery")):
                issues.append("原生 CDP 端点未满足 detail-xhr 基础条件")
    except RuntimeError as exc:
        browser_status = {"via": via, "error": str(exc)}
        issues.append(str(exc))
    if budget.exceeded:
        issues.append("携程接口请求账本已超过上限，必须停止目标实现")
    elif budget.remaining <= 0:
        issues.append("携程接口请求账本剩余额度为 0")
    if not auth_status.get("selected"):
        issues.append("未找到可用 ctrip-cli-sessions 请求头/登录态文件")
    payload = {
        "ready": not issues,
        "issues": issues,
        "requestBudget": {
            "ledger": request_ledger,
            "limit": budget.limit,
            "used": budget.used,
            "remaining": budget.remaining,
            "exceeded": budget.exceeded,
        },
        "auth": auth_status,
        "browser": browser_status,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ready"] else 1


def cmd_self_test_http_export(output_dir: str, request_budget: int) -> int:
    limit = _normalize_request_budget(request_budget, "self-test")
    assert limit is not None
    payload = run_http_export_selftest(Path(output_dir), request_budget=limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


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
    auth_sub.add_parser("status")

    run = sub.add_parser("run")
    run_sub = run.add_subparsers(dest="run_command")
    collect = run_sub.add_parser("collect")
    collect.add_argument("--page-size", type=int, default=None)
    collect.add_argument("--max-pages", type=int, default=None)
    collect.add_argument("--start-date", default=None, help="YYYY-MM-DD，默认昨天")
    collect.add_argument("--end-date", default=None, help="YYYY-MM-DD，默认同 start-date")
    collect.add_argument("--include", default=None, help="逗号分隔客服账号/昵称/显示名")
    collect.add_argument("--via", choices=["cdp", "http", "browser"], default="cdp", help="cdp=已登录页面内 fetch；http=纯 requests；browser=旧扩展点击采集")
    collect.add_argument("--request-budget", type=int, default=None, help="本次 collect 最多允许发出的携程接口请求数，最大 30")
    collect.add_argument("--request-ledger", default=None, help="跨命令累计携程接口请求数的 JSON 账本路径")
    export = run_sub.add_parser("export")
    export.add_argument("--kind", choices=["singlefile", "structured", "links"], required=True)
    export.add_argument("--formats", default=None, help="structured 导出格式，逗号分隔: json,markdown")
    export.add_argument("--output", default=None, help="links 导出路径")
    export.add_argument("--via", choices=["cdp", "http"], default="cdp", help="structured 导出方式；http=纯 requests")
    export.add_argument("--request-budget", type=int, default=None, help="本次 HTTP export 最多允许发出的携程接口请求数，最大 30")
    export.add_argument("--request-ledger", default=None, help="跨命令累计携程接口请求数的 JSON 账本路径")

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

    discover = sub.add_parser("discover")
    discover_sub = discover.add_subparsers(dest="discover_command")
    detail_xhr = discover_sub.add_parser("detail-xhr")
    detail_xhr.add_argument("--session-id", required=True)
    detail_xhr.add_argument("--request-budget", type=int, default=10, help="本次发现最多允许发出的携程接口请求数，最大 30")
    detail_xhr.add_argument("--request-ledger", default=None, help="跨命令累计携程接口请求数的 JSON 账本路径")
    detail_xhr.add_argument("--wait-sec", type=float, default=8.0)
    detail_xhr.add_argument("--output", default=None, help="发现报告 JSON 输出路径")
    detail_xhr.add_argument("--via", choices=["cdp", "proxy"], default="cdp", help="cdp=原生 CDP Network/Fetch 捕获；proxy=旧 eval 探针")
    detail_xhr.add_argument("--cdp-base-url", default=None, help="覆盖原生 CDP HTTP 地址，例如 http://127.0.0.1:9333")
    apply_config = discover_sub.add_parser("apply-config")
    apply_config.add_argument("--report", required=True, help="detail-xhr 输出的发现报告 JSON")
    cdp_status = discover_sub.add_parser("cdp-status")
    cdp_status.add_argument("--via", choices=["cdp", "proxy"], default="cdp", help="cdp=原生 DevTools HTTP；proxy=web-access CDP Proxy")
    cdp_status.add_argument("--cdp-base-url", default=None, help="覆盖原生 CDP HTTP 地址，例如 http://127.0.0.1:9333")

    request_budget_parser = sub.add_parser("request-budget")
    request_budget_sub = request_budget_parser.add_subparsers(dest="request_budget_command")
    request_budget_status = request_budget_sub.add_parser("status")
    request_budget_status.add_argument("--request-budget", type=int, required=True, help="本轮携程接口请求总预算，最大 30")
    request_budget_status.add_argument("--request-ledger", required=True, help="跨命令累计携程接口请求数的 JSON 账本路径")

    preflight = sub.add_parser("preflight")
    preflight.add_argument("--request-budget", type=int, required=True, help="本轮携程接口请求总预算，最大 30")
    preflight.add_argument("--request-ledger", required=True, help="跨命令累计携程接口请求数的 JSON 账本路径")
    preflight.add_argument("--via", choices=["cdp", "proxy"], default="proxy", help="cdp=原生 DevTools HTTP；proxy=web-access CDP Proxy")
    preflight.add_argument("--cdp-base-url", default=None, help="覆盖原生 CDP HTTP 地址，例如 http://127.0.0.1:9333")

    self_test = sub.add_parser("self-test")
    self_test_sub = self_test.add_subparsers(dest="self_test_command")
    http_export = self_test_sub.add_parser("http-export")
    http_export.add_argument("--output-dir", default=".im_archive/selftest", help="本地自测产物输出目录")
    http_export.add_argument("--request-budget", type=int, default=1, help="本地 mock 请求预算，最大 30")
    return parser


def _dispatch(args, cfg: AppConfig, logger, parser: argparse.ArgumentParser) -> int:
    if args.command == "chrome" and args.chrome_command == "start":
        return cmd_chrome_start(cfg, logger, headed=args.headed, debug=args.debug)
    if args.command == "auth" and args.auth_command == "login":
        return cmd_auth_login(cfg, logger)
    if args.command == "auth" and args.auth_command == "status":
        return cmd_auth_status(cfg)
    if args.command == "run" and args.run_command == "collect":
        if args.via == "browser":
            if args.request_budget is not None or args.request_ledger:
                raise RuntimeError("collect --via browser 旧插件路径无法精确执行 request-budget/request-ledger；请使用 --via cdp 或 --via http")
            return cmd_run_collect_browser(cfg, logger, args.page_size, args.max_pages)
        return cmd_run_collect_http(
            cfg,
            logger,
            args.page_size,
            args.max_pages,
            args.start_date,
            args.end_date,
            args.include,
            via=args.via,
            request_budget=args.request_budget,
            request_ledger=args.request_ledger,
        )
    if args.command == "run" and args.run_command == "export":
        return cmd_run_export(
            cfg,
            logger,
            args.kind,
            formats=args.formats,
            output=args.output,
            via=args.via,
            request_budget=args.request_budget,
            request_ledger=args.request_ledger,
        )
    if args.command == "roles" and args.roles_command == "list":
        return cmd_roles_list(cfg)
    if args.command == "roles" and args.roles_command == "select":
        return cmd_roles_select(cfg, args.all, args.include)
    if args.command == "import" and args.import_command == "links":
        return cmd_import_links(cfg, Path(args.file), preview_only=args.preview, confirm=args.confirm)
    if args.command == "state" and args.state_command == "watch":
        return cmd_state_watch(cfg, args.interval_sec, once=args.once)
    if args.command == "discover" and args.discover_command == "detail-xhr":
        return cmd_discover_detail_xhr(
            cfg,
            logger,
            args.session_id,
            request_budget=args.request_budget,
            wait_sec=args.wait_sec,
            output=args.output,
            via=args.via,
            cdp_base_url=args.cdp_base_url,
            request_ledger=args.request_ledger,
        )
    if args.command == "discover" and args.discover_command == "apply-config":
        return cmd_discover_apply_config(cfg, logger, args.config, args.report)
    if args.command == "discover" and args.discover_command == "cdp-status":
        return cmd_discover_cdp_status(cfg, args.cdp_base_url, via=args.via)
    if args.command == "request-budget" and args.request_budget_command == "status":
        return cmd_request_budget_status(args.request_budget, args.request_ledger)
    if args.command == "preflight":
        return cmd_preflight(cfg, args.request_budget, args.request_ledger, via=args.via, cdp_base_url=args.cdp_base_url)
    if args.command == "self-test" and args.self_test_command == "http-export":
        return cmd_self_test_http_export(args.output_dir, args.request_budget)

    parser.print_help()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = load_or_create_config(Path(args.config))
        logger = setup_logger(Path(cfg.log_dir))
        return _dispatch(args, cfg, logger, parser)
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
