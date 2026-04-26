(function () {
  "use strict";

  const API_KEYWORD = "getSessionDimMetricDetailsV3";
  const EVENT_NAME = "__im_archive_api_response__";
  const BRIDGE_FLAG = "__imArchiveBridgeInstalled";

  if (window[BRIDGE_FLAG]) {
    return;
  }
  window[BRIDGE_FLAG] = true;

  let sequence = 0;

  function findPayload(node, depth) {
    if (!node || depth > 6) return null;

    if (Array.isArray(node)) {
      for (const item of node) {
        const match = findPayload(item, depth + 1);
        if (match) return match;
      }
      return null;
    }

    if (typeof node !== "object") return null;
    if (Array.isArray(node.tableDataItemList)) return node;

    for (const value of Object.values(node)) {
      const match = findPayload(value, depth + 1);
      if (match) return match;
    }
    return null;
  }

  function emit(url, text) {
    if (!url || !String(url).includes(API_KEYWORD) || !text) return;

    try {
      const parsed = JSON.parse(text);
      const payload = findPayload(parsed, 0);
      if (!payload || !Array.isArray(payload.tableDataItemList)) return;

      const sessions = payload.tableDataItemList
        .map((item, index) => {
          const dimMap = item?.dimMap || item || {};
          const sessionId = dimMap.session_id || item?.session_id;
          if (!sessionId) return null;

          return {
            sessionId: String(sessionId),
            createTime: dimMap.session_create_time || item?.session_create_time || "",
            index: index + 1
          };
        })
        .filter(Boolean);

      window.dispatchEvent(new CustomEvent(EVENT_NAME, {
        detail: {
          sequence: ++sequence,
          url: String(url),
          totalNum: Number(payload.totalNum || sessions.length || 0),
          sessions
        }
      }));
    } catch (error) {
      console.warn("[IM-Archive bridge] parse failed:", error);
    }
  }

  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = async function (...args) {
      const response = await originalFetch.apply(this, args);
      try {
        const requestUrl = typeof args[0] === "string" ? args[0] : args[0]?.url;
        response.clone().text().then(text => emit(requestUrl || response.url, text)).catch(() => {});
      } catch (error) {
        // ignore
      }
      return response;
    };
  }

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url) {
    this.__imArchiveUrl = url;
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function () {
    this.addEventListener("loadend", function () {
      try {
        if (this.readyState === 4 && this.status >= 200 && this.status < 300) {
          emit(this.__imArchiveUrl || this.responseURL, this.responseText);
        }
      } catch (error) {
        // ignore
      }
    }, { once: true });
    return originalSend.apply(this, arguments);
  };
})();
