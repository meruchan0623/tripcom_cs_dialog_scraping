// popup.js — IM会话归档助手侧栏控制

const els = {
  statusDot: document.getElementById("statusDot"),
  pageStatus: document.getElementById("pageStatus"),
  totalNum: document.getElementById("totalNum"),
  doneNum: document.getElementById("doneNum"),
  failNum: document.getElementById("failNum"),
  currentTask: document.getElementById("currentTask"),
  logArea: document.getElementById("logArea"),
  btnCollect: document.getElementById("btnCollect"),
  btnArchive: document.getElementById("btnArchive"),
  btnExportStructured: document.getElementById("btnExportStructured"),
  btnExportLinks: document.getElementById("btnExportLinks"),
  btnImportLinks: document.getElementById("btnImportLinks"),
  inputImportLinks: document.getElementById("inputImportLinks"),
  roleList: document.getElementById("roleList"),
  btnSelectAllRoles: document.getElementById("btnSelectAllRoles"),
  btnClearRoles: document.getElementById("btnClearRoles"),
  btnPause: document.getElementById("btnPause"),
  btnResume: document.getElementById("btnResume"),
  btnCancel: document.getElementById("btnCancel"),
  btnClearData: document.getElementById("btnClearData"),
  btnReset: document.getElementById("btnReset"),
  configPanel: document.getElementById("configPanel"),
  configToggle: document.getElementById("configToggle"),
  fmtJson: document.getElementById("fmtJson"),
  fmtMarkdown: document.getElementById("fmtMarkdown")
};

const CONTENT_SCRIPT_VERSION = "2026-04-20-api-bridge-v2";

const PHASE_LABELS = {
  idle: "空闲",
  collecting: "收集会话中",
  ready: "就绪",
  archiving_singlefile: "SingleFile 归档中",
  exporting_structured: "结构化导出中",
  exporting_links: "XLSX 链接导出中",
  archived: "归档完成",
  exported: "导出完成"
};

async function refreshState() {
  try {
    const resp = await chrome.runtime.sendMessage({ type: "getState" });
    if (resp?.status === "ok" && resp.data) {
      updateUI(resp.data);
    }
  } catch (error) {
    console.warn("[IM-Archive popup] refreshState failed:", error);
  }
}

async function ensureContentScript(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    const url = String(tab?.url || "");
    if (!url.includes("vbooking.ctrip.com")) {
      return false;
    }
  } catch (_) {
    return false;
  }

  try {
    const resp = await chrome.tabs.sendMessage(tabId, { action: "ping" });
    if (resp && resp.status === "ok" && resp.data?.version === CONTENT_SCRIPT_VERSION) {
      return true;
    }
  } catch (error) {
    // fall through to injection
  }

  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content-script.js"]
    });
    const retry = await chrome.tabs.sendMessage(tabId, { action: "ping" });
    if (retry && retry.status === "ok" && retry.data?.version === CONTENT_SCRIPT_VERSION) {
      return true;
    }
  } catch (injectError) {
    console.warn("[IM-Archive popup] ensureContentScript failed:", injectError);
  }

  return false;
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function showPageStatus(ok, text) {
  els.pageStatus.className = "page-status " + (ok ? "ok" : "err");
  els.pageStatus.textContent = text;
}

function replaceLogs(logs) {
  els.logArea.textContent = logs && logs.length ? logs.join("\n") : "等待启动...";
  els.logArea.scrollTop = els.logArea.scrollHeight;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setButtonState(state) {
  const running = !!state.running;
  const paused = !!state.paused;
  const readyToArchive = !!state.readyToArchive;

  els.btnCollect.disabled = running;
  els.btnArchive.disabled = running || !readyToArchive;
  els.btnExportStructured.disabled = running || !readyToArchive;
  els.btnExportLinks.disabled = running || !readyToArchive;
  els.btnImportLinks.disabled = running;
  els.btnSelectAllRoles.disabled = running || !readyToArchive;
  els.btnClearRoles.disabled = running || !readyToArchive;

  const roleInputs = els.roleList.querySelectorAll('input[type="checkbox"][name="csRole"]');
  roleInputs.forEach(input => {
    input.disabled = running || !readyToArchive;
  });

  els.btnPause.style.display = running && !paused ? "" : "none";
  els.btnPause.disabled = !(running && !paused);

  els.btnResume.style.display = running && paused ? "" : "none";
  els.btnResume.disabled = !(running && paused);

  els.btnCancel.disabled = !running;
}

function updateUI(state) {
  if (!state) return;

  els.statusDot.className = "status-dot";
  if (state.running && !state.paused) {
    els.statusDot.classList.add("running");
  } else if (state.paused) {
    els.statusDot.classList.add("paused");
  } else if (state.cancelled) {
    els.statusDot.classList.add("error");
  }

  els.totalNum.textContent = state.totalSessions ?? 0;
  els.doneNum.textContent = state.completedSessions ?? 0;
  els.failNum.textContent = state.failedSessions ?? 0;

  const phaseLabel = PHASE_LABELS[state.phase] || state.phase || "idle";

  if (state.phase === "ready") {
    els.currentTask.textContent = `已收集 ${state.collectedCount || 0} 条会话，请选择操作。`;
  } else if (state.phase === "archived" || state.phase === "exported") {
    els.currentTask.innerHTML =
      `<strong>${phaseLabel}</strong><br>` +
      (state.lastOutputSummary || `已收集 ${state.collectedCount || 0} 条会话`);
  } else if (state.currentCsName || state.currentSessionId) {
    els.currentTask.innerHTML =
      `<strong>阶段:</strong> ${phaseLabel}<br>` +
      `<strong>客服:</strong> ${state.currentCsName || "-"}<br>` +
      `<strong>会话:</strong> <code>${state.currentSessionId || "-"}</code>`;
  } else if (state.running) {
    els.currentTask.textContent = `当前阶段: ${phaseLabel}`;
  } else {
    els.currentTask.textContent = "先获取会话列表，再选择操作。";
  }

  if (Array.isArray(state.log)) {
    replaceLogs(state.log);
  }

  renderRoleOptions(state);

  // 同步格式选择
  if (Array.isArray(state.selectedStructuredFormats)) {
    els.fmtJson.checked = state.selectedStructuredFormats.includes("json");
    els.fmtMarkdown.checked = state.selectedStructuredFormats.includes("markdown");
  }

  setButtonState(state);
}

function renderRoleOptions(state) {
  const availableRoles = Array.isArray(state?.availableCsRoles) ? state.availableCsRoles : [];
  const roleStats = Array.isArray(state?.availableCsRoleStats) ? state.availableCsRoleStats : [];
  const statMap = new Map(roleStats.map(item => [String(item?.name || "").trim(), Number(item?.count || 0)]));
  const selectedRoles = new Set(Array.isArray(state?.selectedCsRoles) ? state.selectedCsRoles : []);

  if (!availableRoles.length) {
    els.roleList.innerHTML = '<span class="role-list-empty">请先点击“获取会话”加载客服角色</span>';
    return;
  }

  const html = availableRoles.map(role => {
    const checked = selectedRoles.has(role) ? "checked" : "";
    const escapedValue = escapeHtml(role);
    const count = statMap.get(String(role).trim());
    const label = count > 0 ? `${role} (${count})` : role;
    const escapedLabel = escapeHtml(label);
    return `<label><input type="checkbox" name="csRole" value="${escapedValue}" ${checked}> ${escapedLabel}</label>`;
  }).join("");
  els.roleList.innerHTML = html;

  const roleInputs = els.roleList.querySelectorAll('input[type="checkbox"][name="csRole"]');
  roleInputs.forEach(input => {
    input.addEventListener("change", syncSelectedRoles);
  });
}

async function init() {
  const tab = await getActiveTab();

  if (!tab || !tab.url) {
    showPageStatus(false, "无法获取当前标签页");
    return;
  }

  if (tab.url.includes("vbooking.ctrip.com")) {
    showPageStatus(true, "已检测到供应商平台");
    const ready = await ensureContentScript(tab.id);
    if (ready) {
      showPageStatus(true, "页面已就绪，可先获取会话");
    } else {
      showPageStatus(true, "当前页面仍是旧版脚本，请刷新供应商平台页面一次完成升级");
    }
  } else if (tab.url.includes("chrome://") || tab.url.startsWith("chrome-extension://")) {
    showPageStatus(false, "当前是浏览器内置页，请在供应商平台中使用");
    els.btnCollect.disabled = true;
  } else {
    showPageStatus(false, "当前不在供应商平台 (vbooking.ctrip.com)");
    els.btnCollect.disabled = true;
  }

  await refreshState();

  try {
    const cfg = await chrome.runtime.sendMessage({ type: "getConfig" });
    if (cfg?.config) {
      document.getElementById("cfgPageSize").value = cfg.config.pageSize || 100;
      document.getElementById("cfgConcurrency").value = cfg.config.concurrency || 20;
      document.getElementById("cfgDelay").value = Math.max(1, Math.round((cfg.config.delayBetweenSaves || 30000) / 1000));
      document.getElementById("cfgPrefix").value = cfg.config.outputPrefix || "IM_Archive";
      document.getElementById("cfgOutputPath").value = cfg.config.outputPath || "";
    }
  } catch (error) {
    console.warn("[IM-Archive popup] getConfig failed:", error);
  }
}

async function handleCollect() {
  try {
    const tab = await getActiveTab();
    if (!tab?.url || !tab.url.includes("vbooking.ctrip.com")) {
      throw new Error("请先切换到 vbooking.ctrip.com 的 IM 页面再执行获取会话");
    }
    const ready = await ensureContentScript(tab.id);
    if (!ready) {
      throw new Error("当前页面仍是旧版脚本，请刷新供应商平台页面一次后重试");
    }

    const resp = await chrome.runtime.sendMessage({
      type: "start",
      tabId: tab.id
    });
    if (resp.status === "error") {
      alert(resp.message);
      return;
    }

    await refreshState();
  } catch (error) {
    alert("启动失败: " + error.message);
  }
}

async function handleArchive() {
  const resp = await chrome.runtime.sendMessage({ type: "archiveSingleFile" });
  if (resp?.status === "error") {
    alert(resp.message);
    return;
  }
  await refreshState();
}

async function handleExportStructured() {
  // 先同步格式选择
  await syncFormats();

  const resp = await chrome.runtime.sendMessage({ type: "exportStructured" });
  if (resp?.status === "error") {
    alert(resp.message);
    return;
  }
  await refreshState();
}

async function handleExportLinks() {
  const resp = await chrome.runtime.sendMessage({ type: "exportLinksWorkbook" });
  if (resp?.status === "error") {
    alert(resp.message);
    return;
  }
  await refreshState();
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("读取文件失败"));
    reader.onload = () => {
      const result = String(reader.result || "");
      const commaIdx = result.indexOf(",");
      if (commaIdx < 0) {
        reject(new Error("文件编码失败"));
        return;
      }
      resolve(result.substring(commaIdx + 1));
    };
    reader.readAsDataURL(file);
  });
}

async function handleImportLinksClick() {
  els.inputImportLinks.value = "";
  els.inputImportLinks.click();
}

async function handleImportLinksFileChange(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const base64 = await readFileAsBase64(file);
    const previewResp = await chrome.runtime.sendMessage({
      type: "importLinksWorkbookPreview",
      filename: file.name,
      base64
    });
    if (previewResp?.status === "error") {
      alert(previewResp.message || "预览失败");
      return;
    }

    const preview = previewResp?.preview || {};
    const roles = Array.isArray(preview.roles) ? preview.roles : [];
    const topRoles = roles.slice(0, 15).map(r => `- ${r.csName}: ${r.count} 条`).join("\n");
    const moreHint = roles.length > 15 ? `\n- ...其余 ${roles.length - 15} 位客服` : "";
    const confirmText =
      `即将导入链接表：${file.name}\n\n` +
      `会话总数：${preview.totalSessions || 0}\n` +
      `客服数量：${preview.totalRoles || 0}\n\n` +
      `客服明细：\n${topRoles || "- 无"}${moreHint}\n\n` +
      "确认导入吗？";
    if (!confirm(confirmText)) {
      return;
    }

    const resp = await chrome.runtime.sendMessage({
      type: "importLinksWorkbook",
      filename: file.name,
      base64
    });
    if (resp?.status === "error") {
      alert(resp.message || "导入失败");
      return;
    }
    alert(resp?.message || "导入成功");
    await refreshState();
  } catch (error) {
    alert("导入失败: " + error.message);
  } finally {
    els.inputImportLinks.value = "";
  }
}

async function handlePause() {
  const resp = await chrome.runtime.sendMessage({ type: "pause" });
  if (resp?.status === "ok") {
    await refreshState();
  }
}

async function handleResume() {
  const resp = await chrome.runtime.sendMessage({ type: "resume" });
  if (resp?.status === "ok") {
    await refreshState();
  }
}

async function handleCancel() {
  if (!confirm("确定要取消当前任务吗？")) return;
  const resp = await chrome.runtime.sendMessage({ type: "cancel" });
  if (resp?.status === "ok") {
    await refreshState();
  }
}

async function handleClearData() {
  if (!confirm("确定要清空已收集的会话数据吗？")) return;
  const resp = await chrome.runtime.sendMessage({ type: "clearData" });
  if (resp?.status === "ok") {
    await refreshState();
  }
}

async function handleReset() {
  if (!confirm("确定要重置插件吗？所有状态和数据将被清除。")) return;
  const resp = await chrome.runtime.sendMessage({ type: "resetAll" });
  if (resp?.status === "ok") {
    await refreshState();
  }
}

function toggleConfig() {
  els.configPanel.style.display = els.configPanel.style.display === "none" ? "" : "none";
}

async function saveConfig() {
  const config = {
    pageSize: parseInt(document.getElementById("cfgPageSize").value, 10) || 100,
    concurrency: Math.min(200, Math.max(1, parseInt(document.getElementById("cfgConcurrency").value, 10) || 20)),
    delayBetweenSaves: Math.min(60000, Math.max(1000, (parseInt(document.getElementById("cfgDelay").value, 10) || 30) * 1000)),
    outputPrefix: document.getElementById("cfgPrefix").value.trim() || "IM_Archive",
    outputPath: document.getElementById("cfgOutputPath").value.trim()
  };

  await chrome.runtime.sendMessage({ type: "setConfig", config });
}

async function syncFormats() {
  const formats = [];
  if (els.fmtJson.checked) formats.push("json");
  if (els.fmtMarkdown.checked) formats.push("markdown");
  await chrome.runtime.sendMessage({ type: "setStructuredFormats", formats });
}

async function syncSelectedRoles() {
  const checked = Array.from(els.roleList.querySelectorAll('input[type="checkbox"][name="csRole"]:checked'))
    .map(input => input.value);
  await chrome.runtime.sendMessage({ type: "setSelectedCsRoles", roles: checked });
  await refreshState();
}

async function selectAllRoles() {
  const all = Array.from(els.roleList.querySelectorAll('input[type="checkbox"][name="csRole"]')).map(input => input.value);
  await chrome.runtime.sendMessage({ type: "setSelectedCsRoles", roles: all });
  await refreshState();
}

async function clearAllRoles() {
  await chrome.runtime.sendMessage({ type: "setSelectedCsRoles", roles: [] });
  await refreshState();
}

chrome.runtime.onMessage.addListener(msg => {
  if (msg.type === "progress" && msg.state) {
    updateUI(msg.state);
  }
});

els.btnCollect.addEventListener("click", handleCollect);
els.btnArchive.addEventListener("click", handleArchive);
els.btnExportStructured.addEventListener("click", handleExportStructured);
els.btnExportLinks.addEventListener("click", handleExportLinks);
els.btnImportLinks.addEventListener("click", handleImportLinksClick);
els.inputImportLinks.addEventListener("change", handleImportLinksFileChange);
els.btnPause.addEventListener("click", handlePause);
els.btnResume.addEventListener("click", handleResume);
els.btnCancel.addEventListener("click", handleCancel);
els.btnSelectAllRoles.addEventListener("click", selectAllRoles);
els.btnClearRoles.addEventListener("click", clearAllRoles);
els.btnClearData.addEventListener("click", handleClearData);
els.btnReset.addEventListener("click", handleReset);
els.configToggle.addEventListener("click", toggleConfig);
els.fmtJson.addEventListener("change", syncFormats);
els.fmtMarkdown.addEventListener("change", syncFormats);

// 所有配置项变更时实时保存
for (const id of ["cfgPageSize", "cfgConcurrency", "cfgDelay", "cfgPrefix", "cfgOutputPath"]) {
  document.getElementById(id).addEventListener("input", saveConfig);
}

init();
