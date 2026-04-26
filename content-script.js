// content-script.js — IM会话归档助手
// 注入到 vbooking.ctrip.com 页面，负责 DOM 交互和接口响应提取

(function () {
  "use strict";

  const API_EVENT_NAME = "__im_archive_api_response__";
  const BRIDGE_FLAG = "__imArchiveBridgeInstalled";
  const API_KEYWORD = "getSessionDimMetricDetailsV3";
  const CONTENT_SCRIPT_VERSION = "2026-04-20-api-bridge-v2";

  const apiState = {
    sequence: 0,
    latest: null
  };

  function waitFor(selector, timeout = 10000) {
    return new Promise((resolve, reject) => {
      const el = document.querySelector(selector);
      if (el) {
        resolve(el);
        return;
      }

      const observer = new MutationObserver((_, obs) => {
        const next = document.querySelector(selector);
        if (next) {
          obs.disconnect();
          resolve(next);
        }
      });

      observer.observe(document.body, { childList: true, subtree: true });
      setTimeout(() => {
        observer.disconnect();
        reject(new Error("waitFor timeout: " + selector));
      }, timeout);
    });
  }

  function waitForCondition(checker, timeout = 5000, interval = 100) {
    return new Promise((resolve, reject) => {
      const start = Date.now();

      const tick = () => {
        try {
          const result = checker();
          if (result) {
            resolve(result);
            return;
          }
        } catch (error) {
          // ignore transient render errors
        }

        if (Date.now() - start >= timeout) {
          reject(new Error("waitForCondition timeout"));
          return;
        }
        setTimeout(tick, interval);
      };

      tick();
    });
  }

  function randomDelay(min = 120, max = 260) {
    return new Promise(resolve => setTimeout(resolve, min + Math.random() * (max - min)));
  }

  function waitForBridgeInstalled(timeout = 3000) {
    return waitForCondition(
      () => document.documentElement.dataset.imArchiveBridgeInstalled === "true",
      timeout,
      80
    );
  }

  function dispatchClickSequence(el) {
    el.dispatchEvent(new PointerEvent("pointerdown", {
      bubbles: true,
      cancelable: true,
      view: window,
      button: 0,
      buttons: 1
    }));
    el.dispatchEvent(new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
      view: window,
      button: 0,
      buttons: 1
    }));
    el.dispatchEvent(new MouseEvent("mouseup", {
      bubbles: true,
      cancelable: true,
      view: window,
      button: 0
    }));
    el.dispatchEvent(new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      view: window,
      button: 0
    }));
  }

  function getActionButton(rowEl) {
    const actionCell = rowEl.querySelector("td:last-child");
    return actionCell?.querySelector(".linkStyle") || null;
  }

  function getExpandedContainer() {
    return document.querySelector(".ant-table-expanded-row-level-1 .subTableContainer");
  }

  function getExpandedPager() {
    return getExpandedContainer()?.querySelector(".ant-table-pagination") || null;
  }

  function getExpandedPageSizeSelector() {
    return getExpandedPager()?.querySelector(".ant-select-selector") || null;
  }

  function getExpandedActivePage() {
    return getExpandedContainer()?.querySelector(".ant-pagination-item-active")?.innerText?.trim() || "1";
  }

  function getExpandedRowsSignature() {
    const container = getExpandedContainer();
    if (!container) return "";
    return Array.from(container.querySelectorAll("tbody.ant-table-tbody tr.ant-table-row"))
      .slice(0, 3)
      .map(row => row.innerText?.trim() || "")
      .join(" | ");
  }

  function getCurrentPageSize() {
    const selectorText = getExpandedPageSizeSelector()?.innerText?.trim() || "";
    const match = selectorText.match(/(\d+)/);
    return match ? parseInt(match[1], 10) : 10;
  }

  function normalizeApiDetail(detail) {
    if (!detail) return null;

    const pageSize = detail.pageSize || getCurrentPageSize();
    const totalNum = Number(detail.totalNum || 0);
    const sessions = Array.isArray(detail.sessions)
      ? detail.sessions.map((item, index) => ({
          sessionId: String(item.sessionId),
          createTime: item.createTime || "",
          index: item.index || index + 1
        }))
      : [];

    return {
      sequence: Number(detail.sequence || 0),
      totalNum,
      pageSize,
      totalPages: Math.max(1, Math.ceil((totalNum || sessions.length || 1) / Math.max(pageSize, 1))),
      sessions
    };
  }

  function ensureApiBridge() {
    if (document.documentElement.dataset.imArchiveBridgeInstalled === "true") return;

    const script = document.createElement("script");
    script.src = chrome.runtime.getURL("page-bridge.js");
    script.async = false;
    script.onload = () => {
      document.documentElement.dataset.imArchiveBridgeInstalled = "true";
      script.remove();
    };
    script.onerror = () => {
      console.warn("[IM-Archive] page bridge load failed");
      script.remove();
    };

    (document.head || document.documentElement || document.body).appendChild(script);
  }

  function waitForApiAfter(sequence, timeout = 7000) {
    return waitForCondition(() => {
      if (apiState.latest && apiState.latest.sequence > sequence) {
        return apiState.latest;
      }
      return null;
    }, timeout, 120);
  }

  function getLatestApiData() {
    if (!apiState.latest) {
      throw new Error("尚未捕获到会话接口响应");
    }
    return apiState.latest;
  }

  window.addEventListener(API_EVENT_NAME, event => {
    const detail = normalizeApiDetail(event.detail);
    if (detail) {
      apiState.sequence = detail.sequence;
      apiState.latest = detail;
    }
  });

  ensureApiBridge();

  const IMArchiveContentScript = {
    isTargetPage() {
      return window.location.hostname.includes("vbooking.ctrip.com");
    },

    getCustomerServiceRows() {
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
        const btnState = actionText.includes("展开") ? "collapsed" : "expanded";

        result.push({ name, count, row, btnState });
      });

      return result;
    },

    async expandCustomerService(csRow) {
      try {
        const linkBtn = getActionButton(csRow);
        if (!linkBtn) {
          return { ok: false, message: "未找到展开按钮" };
        }

        const text = linkBtn.innerText.trim();
        const currentSequence = apiState.sequence;
        await waitForBridgeInstalled();

        if (text.includes("收起")) {
          const latest = getLatestApiData();
          return { ok: true, message: "当前已展开", api: latest };
        }
        if (!text.includes("展开")) {
          return { ok: false, message: "无法识别当前按钮状态" };
        }

        linkBtn.click();
        await waitFor(".ant-table-expanded-row-level-1", 5000);
        const latest = await waitForApiAfter(currentSequence, 8000);
        await randomDelay();
        return { ok: true, message: "展开成功", api: latest };
      } catch (error) {
        console.error("[IM-Archive] expand error:", error);
        return { ok: false, message: error.message || "展开失败" };
      }
    },

    async setPageSize(pageSize = 100) {
      try {
        let selectWrap = getExpandedPageSizeSelector();
        if (!selectWrap) {
          // 分页器可能还未渲染，等待最多 5 秒
          try {
            await waitForCondition(() => getExpandedPageSizeSelector(), 5000, 200);
            selectWrap = getExpandedPageSizeSelector();
          } catch (_) {}
        }
        if (!selectWrap) {
          return { ok: false, message: "未找到分页器选择器" };
        }

        const beforeText = selectWrap.innerText?.trim() || "";
        const currentSequence = apiState.sequence;
        await waitForBridgeInstalled();
        if (beforeText.includes(String(pageSize))) {
          const latest = getLatestApiData();
          return { ok: true, message: "分页已是目标条数", api: latest };
        }

        dispatchClickSequence(selectWrap);
        await randomDelay();

        const targetOption = Array.from(document.querySelectorAll(".ant-select-item-option"))
          .find(opt => {
            const text = opt.querySelector(".ant-select-item-option-content")?.innerText?.trim() || opt.innerText?.trim() || "";
            return text === `${pageSize} 条/页` || text.includes(`${pageSize} 条/页`);
          });

        if (!targetOption) {
          return { ok: false, message: `未找到 ${pageSize} 条/页选项` };
        }

        dispatchClickSequence(targetOption);
        await waitForCondition(() => {
          const currentText = getExpandedPageSizeSelector()?.innerText?.trim() || "";
          return currentText.includes(String(pageSize)) ? currentText : null;
        }, 5000);
        const latest = await waitForApiAfter(currentSequence, 8000);
        await randomDelay();
        return { ok: true, message: "分页设置成功", api: latest };
      } catch (error) {
        console.error("[IM-Archive] setPageSize error:", error);
        return { ok: false, message: error.message || "设置分页失败" };
      }
    },

    getLatestSessions() {
      return getLatestApiData();
    },

    getTotalPages() {
      const latest = getLatestApiData();
      return latest.totalPages;
    },

    async goToNextPage() {
      try {
        const pagination = getExpandedPager();
        const nextBtn = pagination?.querySelector(".ant-pagination-next");
        if (!nextBtn || nextBtn.classList.contains("ant-pagination-disabled")) {
          return { ok: false, message: "已经是最后一页" };
        }

        const previousPage = getExpandedActivePage();
        const previousSignature = getExpandedRowsSignature();
        const currentSequence = apiState.sequence;
        await waitForBridgeInstalled();

        nextBtn.click();
        await waitForCondition(() => {
          const currentPage = getExpandedActivePage();
          const currentSignature = getExpandedRowsSignature();
          if (currentPage !== previousPage) return true;
          if (currentSignature && currentSignature !== previousSignature) return true;
          return false;
        }, 5000);
        const latest = await waitForApiAfter(currentSequence, 8000);
        await randomDelay();
        return { ok: true, message: "已翻到下一页", api: latest };
      } catch (error) {
        return { ok: false, message: error.message || "翻页失败" };
      }
    }
  };

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    async function handleMessage() {
      try {
        switch (msg.action) {
          case "ping":
            return {
              status: "ok",
              data: {
                page: IMArchiveContentScript.isTargetPage(),
                loaded: true,
                version: CONTENT_SCRIPT_VERSION,
                bridgeInstalled: document.documentElement.dataset.imArchiveBridgeInstalled === "true"
              }
            };

          case "getCSList": {
            const list = IMArchiveContentScript.getCustomerServiceRows();
            if (!list.length) {
              return { status: "error", message: "未找到客服汇总表，请确认当前页面已加载完成" };
            }
            return {
              status: "ok",
              data: list.map(item => ({
                name: item.name,
                count: item.count,
                state: item.btnState
              }))
            };
          }

          case "expandCS": {
            const csRows = IMArchiveContentScript.getCustomerServiceRows();
            const target = csRows.find(item => item.name === msg.csName);
            if (!target) {
              return { status: "error", message: "未找到客服: " + msg.csName };
            }
            const result = await IMArchiveContentScript.expandCustomerService(target.row);
            return result.ok
              ? { status: "ok", data: { expanded: true, message: result.message, api: result.api } }
              : { status: "error", message: result.message };
          }

          case "setPageSize": {
            const result = await IMArchiveContentScript.setPageSize(msg.pageSize || 100);
            return result.ok
              ? { status: "ok", data: { pageSize: msg.pageSize || 100, message: result.message, api: result.api } }
              : { status: "error", message: result.message };
          }

          case "extractSessions": {
            const latest = IMArchiveContentScript.getLatestSessions();
            return {
              status: "ok",
              data: {
                sessions: latest.sessions,
                count: latest.sessions.length,
                totalNum: latest.totalNum,
                totalPages: latest.totalPages,
                pageSize: latest.pageSize
              }
            };
          }

          case "getTotalPages":
            return {
              status: "ok",
              data: {
                totalPages: IMArchiveContentScript.getTotalPages(),
                totalNum: getLatestApiData().totalNum
              }
            };

          case "goToNextPage": {
            const result = await IMArchiveContentScript.goToNextPage();
            return result.ok
              ? { status: "ok", data: { moved: true, message: result.message, api: result.api } }
              : { status: "error", message: result.message };
          }

          default:
            return { status: "error", message: "未知 action: " + msg.action };
        }
      } catch (error) {
        return { status: "error", message: error.message || "content script 执行失败" };
      }
    }

    handleMessage().then(sendResponse);
    return true;
  });

  window.__imArchiveLoaded = true;
  console.log("[IM-Archive] Content script loaded on", window.location.href);
})();
