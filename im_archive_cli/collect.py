from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from selenium.webdriver.remote.webdriver import WebDriver

from .browser import execute_js, execute_js_async
from .config import AppConfig
from .models import SessionRecord
from .state import dedupe_sessions

COLLECT_HELPER_JS = r"""
(() => {
  if (window.__IM_ARCHIVE_CLI_COLLECT_READY__) return;
  const API_EVENT_NAME = "__im_archive_api_response__";
  const apiState = { sequence: 0, latest: null };

  function wait(ms){ return new Promise(r => setTimeout(r, ms)); }
  function waitFor(selector, timeout=10000){
    return new Promise((resolve, reject) => {
      const first = document.querySelector(selector);
      if (first) return resolve(first);
      const obs = new MutationObserver(() => {
        const next = document.querySelector(selector);
        if (next) { obs.disconnect(); resolve(next); }
      });
      obs.observe(document.body, {childList:true, subtree:true});
      setTimeout(() => { obs.disconnect(); reject(new Error("waitFor timeout: " + selector)); }, timeout);
    });
  }
  function waitForCondition(checker, timeout=5000, interval=100){
    return new Promise((resolve, reject) => {
      const start = Date.now();
      const tick = () => {
        let result = null;
        try { result = checker(); } catch (_) {}
        if (result) return resolve(result);
        if (Date.now() - start >= timeout) return reject(new Error("waitForCondition timeout"));
        setTimeout(tick, interval);
      };
      tick();
    });
  }
  function getActionButton(rowEl){
    const actionCell = rowEl.querySelector("td:last-child");
    return actionCell?.querySelector(".linkStyle") || null;
  }
  function getExpandedContainer(){ return document.querySelector(".ant-table-expanded-row-level-1 .subTableContainer"); }
  function getExpandedPager(){ return getExpandedContainer()?.querySelector(".ant-table-pagination") || null; }
  function getExpandedPageSizeSelector(){ return getExpandedPager()?.querySelector(".ant-select-selector") || null; }
  function getExpandedActivePage(){ return getExpandedContainer()?.querySelector(".ant-pagination-item-active")?.innerText?.trim() || "1"; }
  function getExpandedRowsSignature(){
    const c = getExpandedContainer(); if (!c) return "";
    return Array.from(c.querySelectorAll("tbody.ant-table-tbody tr.ant-table-row")).slice(0,3).map(x => x.innerText?.trim() || "").join("|");
  }
  function getCurrentPageSize(){
    const txt = getExpandedPageSizeSelector()?.innerText?.trim() || "";
    const m = txt.match(/(\d+)/); return m ? parseInt(m[1], 10) : 10;
  }
  function getLatestApiData(){
    if (!apiState.latest) throw new Error("尚未捕获到会话接口响应");
    return apiState.latest;
  }
  function waitForApiAfter(sequence, timeout=7000){
    return waitForCondition(() => (apiState.latest && apiState.latest.sequence > sequence ? apiState.latest : null), timeout, 120);
  }
  function normalizeApiDetail(detail){
    if (!detail) return null;
    const pageSize = detail.pageSize || getCurrentPageSize();
    const totalNum = Number(detail.totalNum || 0);
    const sessions = Array.isArray(detail.sessions) ? detail.sessions.map((item, index) => ({
      sessionId: String(item.sessionId),
      createTime: item.createTime || "",
      index: item.index || index + 1
    })) : [];
    return {
      sequence: Number(detail.sequence || 0),
      totalNum,
      pageSize,
      totalPages: Math.max(1, Math.ceil((totalNum || sessions.length || 1) / Math.max(pageSize, 1))),
      sessions
    };
  }
  window.addEventListener(API_EVENT_NAME, event => {
    const detail = normalizeApiDetail(event.detail);
    if (!detail) return;
    apiState.sequence = detail.sequence;
    apiState.latest = detail;
  });

  window.__IM_ARCHIVE_CLI_COLLECT__ = {
    getCSList: () => {
      const rows = document.querySelectorAll(".ant-table-row-level-0");
      const result = [];
      rows.forEach(row => {
        const cells = row.querySelectorAll("td");
        if (cells.length < 5) return;
        const linkBtn = getActionButton(row);
        const actionText = linkBtn?.innerText?.trim() || "";
        if (!(actionText.includes("展开") || actionText.includes("收起"))) return;
        const name = cells[0]?.innerText?.trim() || "Unknown";
        const countText = cells[1]?.innerText?.trim() || "0";
        const count = parseInt(countText.replace(/[^\d]/g, ""), 10) || 0;
        result.push({ name, count });
      });
      return result;
    },
    expandCS: async (csName) => {
      const rows = Array.from(document.querySelectorAll(".ant-table-row-level-0"));
      const row = rows.find(r => (r.querySelector("td")?.innerText?.trim() || "") === csName);
      if (!row) throw new Error("未找到客服: " + csName);
      const btn = getActionButton(row);
      if (!btn) throw new Error("未找到展开按钮");
      const txt = btn.innerText?.trim() || "";
      const seq = apiState.sequence;
      if (!txt.includes("收起")) btn.click();
      await waitFor(".ant-table-expanded-row-level-1", 5000);
      if (apiState.latest && apiState.latest.sequence > seq) return true;
      await waitForApiAfter(seq, 8000);
      await wait(120);
      return true;
    },
    setPageSize: async (pageSize) => {
      let selector = getExpandedPageSizeSelector();
      if (!selector) selector = await waitForCondition(() => getExpandedPageSizeSelector(), 5000, 200);
      const before = selector.innerText?.trim() || "";
      const seq = apiState.sequence;
      if (!before.includes(String(pageSize))) {
        selector.click();
        await wait(120);
        const options = Array.from(document.querySelectorAll(".ant-select-item-option"));
        const target = options.find(o => (o.innerText || "").includes(`${pageSize} 条/页`));
        if (!target) throw new Error(`未找到 ${pageSize} 条/页选项`);
        target.click();
        await waitForCondition(() => (getExpandedPageSizeSelector()?.innerText || "").includes(String(pageSize)), 6000, 120);
        await waitForApiAfter(seq, 8000);
      }
      return true;
    },
    extractSessions: () => {
      const latest = getLatestApiData();
      return {
        sessions: latest.sessions || [],
        totalPages: latest.totalPages || 1
      };
    },
    goToNextPage: async () => {
      const pager = getExpandedPager();
      const nextBtn = pager?.querySelector(".ant-pagination-next");
      if (!nextBtn || nextBtn.classList.contains("ant-pagination-disabled")) return { moved: false };
      const prevPage = getExpandedActivePage();
      const prevSig = getExpandedRowsSignature();
      const seq = apiState.sequence;
      nextBtn.click();
      await waitForCondition(() => {
        const p = getExpandedActivePage();
        const s = getExpandedRowsSignature();
        return p !== prevPage || (s && s !== prevSig);
      }, 5000, 120);
      await waitForApiAfter(seq, 8000);
      await wait(120);
      return { moved: true };
    }
  };
  window.__IM_ARCHIVE_CLI_COLLECT_READY__ = true;
})();
"""


def _inject_collect_scripts(driver: WebDriver, repo_root: Path) -> None:
    bridge_js = Path(repo_root / "page-bridge.js").read_text(encoding="utf-8")
    execute_js(driver, f"() => {{ {bridge_js} ; return true; }}")
    execute_js(driver, f"() => {{ {COLLECT_HELPER_JS} ; return true; }}")


def collect_sessions(
    driver: WebDriver,
    repo_root: Path,
    config: AppConfig,
    log: Callable[[str], None],
    page_size: int | None = None,
    max_pages: int | None = None,
) -> list[SessionRecord]:
    _inject_collect_scripts(driver, repo_root)
    size = int(page_size or config.page_size)
    max_page = int(max_pages or config.max_pages)

    cs_list = execute_js(driver, "() => window.__IM_ARCHIVE_CLI_COLLECT__.getCSList()")
    if not cs_list:
        raise RuntimeError("未找到客服汇总表，请确认当前在 IM 会话页面并且已登录")

    all_sessions: list[SessionRecord] = []
    log(f"找到 {len(cs_list)} 位客服")
    for cs in cs_list:
        cs_name = cs.get("name", "Unknown")
        log(f"处理客服 {cs_name}")
        execute_js_async(driver, "(name) => window.__IM_ARCHIVE_CLI_COLLECT__.expandCS(name)", cs_name)
        execute_js_async(driver, "(size) => window.__IM_ARCHIVE_CLI_COLLECT__.setPageSize(size)", size)

        for idx in range(max_page):
            extracted = execute_js(driver, "() => window.__IM_ARCHIVE_CLI_COLLECT__.extractSessions()")
            sessions = extracted.get("sessions", [])
            for item in sessions:
                all_sessions.append(
                    SessionRecord(
                        session_id=str(item.get("sessionId") or ""),
                        cs_name=cs_name,
                        create_time=str(item.get("createTime") or ""),
                    )
                )

            total_pages = int(extracted.get("totalPages") or 1)
            if idx + 1 >= total_pages:
                break
            moved = execute_js_async(driver, "() => window.__IM_ARCHIVE_CLI_COLLECT__.goToNextPage()")
            if not moved.get("moved"):
                break
            time.sleep(0.12)

    return dedupe_sessions(all_sessions)
