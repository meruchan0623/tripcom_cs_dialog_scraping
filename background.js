// background.js — IM会话归档助手 Service Worker (Manifest V3)
// 三条独立输出链：SingleFile 归档 / 结构化对话导出 / XLSX 链接导出

importScripts("lib/jszip.min.js");

const DETAIL_BASE_URL = "https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=";
const LOAD_WAIT_MS = 2200;
const RATE_LIMIT_MS = 120;
const MAX_PAGES = 50;

const DEFAULT_CONFIG = {
  pageSize: 100,
  outputPrefix: "IM_Archive",
  outputPath: "",
  delayBetweenPages: 120,
  delayBetweenSaves: 30000,
  concurrency: 20
};

let archiveState = {
  running: false,
  paused: false,
  cancelled: false,
  phase: "idle",
  totalSessions: 0,
  completedSessions: 0,
  failedSessions: 0,
  currentSessionId: null,
  currentCsName: null,
  log: [],
  collectedSessions: [],
  availableCsRoles: [],
  availableCsRoleStats: [],
  selectedCsRoles: [],
  selectedStructuredFormats: ["json", "markdown"],
  lastOutputKind: null,
  lastOutputSummary: null,
  config: { ...DEFAULT_CONFIG }
};

function normalizeCsRoleKey(name) {
  return String(name || "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function buildCsRoleBuckets(sessions) {
  const buckets = new Map();
  (sessions || []).forEach(s => {
    const raw = String(s?.csName || "").trim();
    if (!raw) return;
    const key = normalizeCsRoleKey(raw);
    if (!key) return;
    if (!buckets.has(key)) {
      buckets.set(key, {
        key,
        name: raw,
        count: 0
      });
    }
    const bucket = buckets.get(key);
    bucket.count += 1;
    // 更偏好保留去空格后的更短展示名，减少“张三 ”这类脏名出现概率
    if (raw.length < bucket.name.length) {
      bucket.name = raw;
    }
  });
  return Array.from(buckets.values()).sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
}

function getUniqueCsRolesFromSessions(sessions) {
  return buildCsRoleBuckets(sessions).map(b => b.name);
}

function getCsRoleStatsFromSessions(sessions) {
  return buildCsRoleBuckets(sessions).map(b => ({
    name: b.name,
    count: b.count
  }));
}

function reconcileCsRoleSelections(preferSelectAll = false) {
  const available = getUniqueCsRolesFromSessions(archiveState.collectedSessions);
  const stats = getCsRoleStatsFromSessions(archiveState.collectedSessions);
  archiveState.availableCsRoles = available;
  archiveState.availableCsRoleStats = stats;

  const current = Array.isArray(archiveState.selectedCsRoles) ? archiveState.selectedCsRoles : [];
  const selected = current.filter(role => available.includes(role));

  if (preferSelectAll || !selected.length) {
    archiveState.selectedCsRoles = [...available];
  } else {
    archiveState.selectedCsRoles = selected;
  }
}

function getFilteredSessionsForSelectedRoles() {
  const selected = new Set((archiveState.selectedCsRoles || []).map(v => String(v || "").trim()).filter(Boolean));
  if (!selected.size) return [];
  const selectedKeys = new Set(Array.from(selected).map(name => normalizeCsRoleKey(name)));
  return archiveState.collectedSessions.filter(s => selectedKeys.has(normalizeCsRoleKey(s?.csName || "")));
}

// ─── 持久化 ───

async function saveProgress() {
  await chrome.storage.local.set({ archiveState });
}

async function loadProgress() {
  const data = await chrome.storage.local.get("archiveState");
  if (data.archiveState) {
    archiveState = {
      ...archiveState,
      ...data.archiveState,
      collectedSessions: Array.isArray(data.archiveState.collectedSessions)
        ? data.archiveState.collectedSessions
        : [],
      availableCsRoles: Array.isArray(data.archiveState.availableCsRoles)
        ? data.archiveState.availableCsRoles
        : [],
      availableCsRoleStats: Array.isArray(data.archiveState.availableCsRoleStats)
        ? data.archiveState.availableCsRoleStats
        : [],
      selectedCsRoles: Array.isArray(data.archiveState.selectedCsRoles)
        ? data.archiveState.selectedCsRoles
        : [],
      selectedStructuredFormats: Array.isArray(data.archiveState.selectedStructuredFormats)
        ? data.archiveState.selectedStructuredFormats
        : ["json", "markdown"],
      config: { ...DEFAULT_CONFIG, ...(data.archiveState.config || {}) }
    };
    archiveState.running = false;
    archiveState.paused = false;
    archiveState.cancelled = false;
    archiveState.currentSessionId = null;
    archiveState.currentCsName = null;
    if (archiveState.phase.startsWith("cancelling_")) {
      archiveState.phase = archiveState.collectedSessions.length ? "ready" : "idle";
    }
    reconcileCsRoleSelections(false);
  }
}

// ─── 工具函数 ───

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function makeDownloadFilename(basename) {
  const p = (archiveState.config.outputPath || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  return p ? `${p}/${basename}` : basename;
}

function makeFilenameTimestamp(date = new Date()) {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  const hh = String(date.getHours()).padStart(2, "0");
  const mi = String(date.getMinutes()).padStart(2, "0");
  const ss = String(date.getSeconds()).padStart(2, "0");
  const ms = String(date.getMilliseconds()).padStart(3, "0");
  return `${yyyy}${mm}${dd}_${hh}${mi}${ss}_${ms}`;
}

function normalizeFilenamePart(text, fallback = "Unknown") {
  const normalized = String(text || "")
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\s+/g, "_")
    .replace(/_+/g, "_")
    .trim();
  return (normalized || fallback).substring(0, 40);
}

function sanitizeSheetName(name, maxLen = 31) {
  let clean = String(name || "Unknown")
    .replace(/[\\/:*?\[\]]/g, "_")
    .replace(/\s+/g, " ")
    .trim();
  if (clean.length > maxLen) clean = clean.substring(0, maxLen - 1) + "…";
  return clean || "Unknown";
}

async function waitWhilePaused() {
  while (archiveState.paused && !archiveState.cancelled) {
    await sleep(500);
  }
}

async function updateAndPersist() {
  await saveProgress();
  chrome.runtime.sendMessage({ type: "progress", state: getStateSummary() }).catch(() => {});
}

async function log(message) {
  const ts = new Date().toLocaleTimeString();
  archiveState.log.push(`[${ts}] ${message}`);
  if (archiveState.log.length > 500) archiveState.log.shift();
  console.log("[IM-Archive BG]", message);
  await updateAndPersist();
}

function getStateSummary() {
  return {
    running: archiveState.running,
    paused: archiveState.paused,
    cancelled: archiveState.cancelled,
    phase: archiveState.phase,
    totalSessions: archiveState.totalSessions,
    completedSessions: archiveState.completedSessions,
    failedSessions: archiveState.failedSessions,
    currentSessionId: archiveState.currentSessionId,
    currentCsName: archiveState.currentCsName,
    collectedCount: archiveState.collectedSessions.length,
    availableCsRoles: archiveState.availableCsRoles,
    availableCsRoleStats: archiveState.availableCsRoleStats,
    selectedCsRoles: archiveState.selectedCsRoles,
    readyToArchive: archiveState.collectedSessions.length > 0,
    selectedStructuredFormats: archiveState.selectedStructuredFormats,
    lastOutputKind: archiveState.lastOutputKind,
    lastOutputSummary: archiveState.lastOutputSummary,
    log: archiveState.log.slice(-20)
  };
}

// ─── 内容脚本通信 ───

async function sendToContent(tabId, message) {
  try {
    const resp = await chrome.tabs.sendMessage(tabId, message);
    if (!resp || resp.status !== "ok") throw new Error(resp?.message || "content script error");
    return resp.data ?? null;
  } catch (error) {
    throw new Error(`sendToContent(${tabId}, ${message.action}): ${error.message}`);
  }
}

// ─── Tab 管理 ───
// 所有任务 tab 统一在一个专用工作窗口中打开，避免污染用户浏览窗口。

let workWindowId = null;
let workWindowPromise = null;

async function ensureWorkWindow() {
  if (workWindowPromise) return workWindowPromise;
  if (workWindowId != null) {
    try { await chrome.windows.get(workWindowId); return workWindowId; }
    catch (e) { workWindowId = null; }
  }
  workWindowPromise = (async () => {
    const win = await chrome.windows.create({ state: "minimized", focused: false, url: "about:blank" });
    workWindowId = win.id;
    return workWindowId;
  })();
  try { return await workWindowPromise; }
  finally { workWindowPromise = null; }
}

async function closeWorkWindow() {
  workWindowPromise = null;
  if (workWindowId != null) {
    try { await chrome.windows.remove(workWindowId); } catch (e) {}
    workWindowId = null;
  }
}

async function openDetailPage(sessionId) {
  const url = `${DETAIL_BASE_URL}${sessionId}`;
  try {
    const windowId = await ensureWorkWindow();
    const tab = await chrome.tabs.create({ url, active: false, windowId });
    await sleep(LOAD_WAIT_MS);
    const loadedTab = await chrome.tabs.get(tab.id);
    if (loadedTab.status !== "complete") {
      await new Promise((resolve, reject) => {
        const listener = (id, info) => {
          if (id === tab.id && info.status === "complete") {
            chrome.tabs.onUpdated.removeListener(listener);
            clearTimeout(timer);
            resolve();
          }
        };
        const timer = setTimeout(() => {
          chrome.tabs.onUpdated.removeListener(listener);
          reject(new Error("load timeout"));
        }, 30000);
        chrome.tabs.onUpdated.addListener(listener);
      });
    }
    await sleep(1800);
    return tab;
  } catch (error) {
    await log(`打开详情页失败 (${sessionId}): ${error.message}`);
    return null;
  }
}

async function safeCloseTab(tabId) {
  try { await chrome.tabs.remove(tabId); } catch (e) {}
}

// ─── SingleFile 归档 ───

async function ensureSingleFileInjected(tabId) {
  try {
    const r = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => Boolean(globalThis.__IM_ARCHIVE_SINGLEFILE_READY__)
    });
    if (r?.[0]?.result) return;
  } catch (e) {}
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["lib/singlefile/single-file.js", "singlefile-runner.js"]
  });
}

async function saveTabWithSingleFile(tabId, filename) {
  try {
    await ensureSingleFileInjected(tabId);
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: async file => await globalThis.__IM_ARCHIVE_SINGLEFILE_SAVE__({ filename: file }),
      args: [filename]
    });
    if (!results?.[0]?.result?.filename) throw new Error("SingleFile 未返回下载结果");
    return true;
  } catch (error) {
    await log("SingleFile 保存错误: " + error.message);
    return false;
  }
}

// ─── 详情页脚本注入 ───

async function ensureDetailPageInjected(tabId) {
  try {
    const r = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => Boolean(globalThis.__IM_ARCHIVE_DETAIL_PAGE_READY__)
    });
    if (r?.[0]?.result) return;
  } catch (e) {}
  await chrome.scripting.executeScript({ target: { tabId }, files: ["detail-page.js"] });
}

// ─── XLSX 后台生成 ───

function escapeXml(v) {
  return String(v ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&apos;");
}
function colName(i) { let n=i+1, s=""; while(n>0){s=String.fromCharCode(65+(n-1)%26)+s; n=Math.floor((n-1)/26);} return s; }

function createSheetXml(rows) {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${
    rows.map((row,ri)=>`<row r="${ri+1}">${row.map((c,ci)=>`<c r="${colName(ci)}${ri+1}" t="inlineStr"><is><t>${escapeXml(c)}</t></is></c>`).join("")}</row>`).join("")
  }</sheetData></worksheet>`;
}

async function buildWorkbookBuffer(workbook) {
  const zip = new JSZip();
  zip.file("[Content_Types].xml",`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${workbook.sheets.map((_,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}</Types>`);
  zip.folder("_rels").file(".rels",`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`);
  zip.folder("xl").file("workbook.xml",`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${workbook.sheets.map((s,i)=>`<sheet name="${escapeXml(s.name)}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join("")}</sheets></workbook>`);
  zip.folder("xl").folder("_rels").file("workbook.xml.rels",`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${workbook.sheets.map((_,i)=>`<Relationship Id="rId${i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join("")}<Relationship Id="rId${workbook.sheets.length+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`);
  zip.folder("xl").file("styles.xml",`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>`);
  const ws = zip.folder("xl").folder("worksheets");
  workbook.sheets.forEach((s,i) => ws.file(`sheet${i+1}.xml`, createSheetXml(s.rows)));
  return zip.generateAsync({ type: "uint8array" });
}

function uint8ArrayToBase64(bytes) {
  let b=""; const cs=0x8000;
  for(let i=0;i<bytes.length;i+=cs) b+=String.fromCharCode(...bytes.subarray(i,i+cs));
  return btoa(b);
}

function base64ToUint8Array(base64) {
  const binary = atob(base64 || "");
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function unescapeXml(text) {
  return String(text || "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}

function columnLettersToIndex(ref) {
  const letters = String(ref || "").match(/[A-Z]+/i)?.[0] || "";
  let idx = 0;
  for (let i = 0; i < letters.length; i++) {
    idx = idx * 26 + (letters.toUpperCase().charCodeAt(i) - 64);
  }
  return Math.max(0, idx - 1);
}

function parseWorksheetRows(xml) {
  const rows = [];
  const rowMatches = xml.match(/<row\b[\s\S]*?<\/row>/g) || [];
  for (const rowXml of rowMatches) {
    const row = [];
    const cellMatches = rowXml.match(/<c\b[\s\S]*?<\/c>/g) || [];
    for (const cellXml of cellMatches) {
      const refMatch = cellXml.match(/\br="([A-Z]+\d+)"/i);
      const colIdx = columnLettersToIndex(refMatch?.[1] || "");

      let value = "";
      const inlineMatch = cellXml.match(/<is>\s*<t[^>]*>([\s\S]*?)<\/t>\s*<\/is>/i);
      const valueMatch = cellXml.match(/<v>([\s\S]*?)<\/v>/i);
      if (inlineMatch) value = unescapeXml(inlineMatch[1]);
      else if (valueMatch) value = unescapeXml(valueMatch[1]);

      row[colIdx] = value;
    }
    rows.push(row);
  }
  return rows;
}

function normalizeImportedSession(raw, fallbackIndex = 0) {
  const sessionIdFromLink = String(raw.detailUrl || "").match(/[?&]sessionId=([^&#]+)/i)?.[1] || "";
  const sessionId = String(raw.sessionId || sessionIdFromLink).trim();
  if (!sessionId) return null;
  return {
    sessionId,
    csName: String(raw.csName || "Unknown").trim() || "Unknown",
    createTime: String(raw.createTime || "").trim(),
    detailUrl: raw.detailUrl || `${DETAIL_BASE_URL}${sessionId}`,
    imported: true,
    seq: fallbackIndex + 1
  };
}

async function parseImportedLinksWorkbook(base64) {
  const bytes = base64ToUint8Array(base64);
  const zip = await JSZip.loadAsync(bytes);
  const worksheetFiles = Object.keys(zip.files)
    .filter(name => /^xl\/worksheets\/sheet\d+\.xml$/i.test(name))
    .sort((a, b) => a.localeCompare(b, "en"));
  if (!worksheetFiles.length) throw new Error("xlsx 中未找到工作表");

  const sessions = [];
  for (const sheetPath of worksheetFiles) {
    const xml = await zip.file(sheetPath).async("string");
    const rows = parseWorksheetRows(xml);
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i] || [];
      const rowValues = row.map(v => String(v || "").trim());
      if (!rowValues.some(Boolean)) continue;
      const sessionIdCell = String(row[1] || "").trim();
      const csNameCell = String(row[2] || "").trim();
      const createTimeCell = String(row[3] || "").trim();
      const detailUrlCell = String(row[4] || "").trim();

      // 跳过标题行
      if (/会话ID/i.test(sessionIdCell) || /详情页链接/i.test(detailUrlCell)) continue;

      const parsed = normalizeImportedSession({
        sessionId: sessionIdCell,
        csName: csNameCell,
        createTime: createTimeCell,
        detailUrl: detailUrlCell
      }, sessions.length);
      if (parsed) sessions.push(parsed);
    }
  }

  return dedupeSessions(sessions);
}

function buildImportPreview(sessions) {
  const grouped = {};
  sessions.forEach(s => {
    const csName = String(s?.csName || "Unknown").trim() || "Unknown";
    grouped[csName] = (grouped[csName] || 0) + 1;
  });
  const roleEntries = Object.entries(grouped)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-CN"))
    .map(([csName, count]) => ({ csName, count }));
  return {
    totalSessions: sessions.length,
    totalRoles: roleEntries.length,
    roles: roleEntries
  };
}

// ─── Markdown 生成 ───

function createMarkdownFromStructured(meta, messages) {
  const lines = [
    `# 会话 ${meta.sessionId}`, "",
    `- 客服: ${meta.csName || "Unknown"}`,
    `- 链接: ${meta.detailUrl || ""}`,
    `- 消息数: ${messages.length}`, ""
  ];
  for (const msg of messages) {
    lines.push(`## ${msg.sequence}. ${msg.senderRole}${msg.senderName ? ` / ${msg.senderName}` : ""}`, "");
    lines.push(`- 时间: ${msg.timestampText || "-"}`);
    lines.push(`- 类型: ${msg.messageType}`);
    lines.push(`- 文本: ${msg.text || (msg.messageType === "image" ? "[图片消息]" : "[空内容]")}`);
    if (msg.attachments?.length) lines.push(`- 附件: ${msg.attachments.map(a => a.src).join(", ")}`);
    lines.push("");
  }
  return lines.join("\n");
}

// ─── 会话收集 ───

function dedupeSessions(sessions) {
  const seen = new Set();
  return sessions.filter(s => { if (!s.sessionId || seen.has(s.sessionId)) return false; seen.add(s.sessionId); return true; });
}

async function extractSessionsForCustomer(tabId, csName) {
  const all = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    if (archiveState.cancelled) break;
    await waitWhilePaused();
    const pd = await sendToContent(tabId, { action: "extractSessions" });
    all.push(...(pd?.sessions || []).map(s => ({ ...s, csName })));
    if (page >= (pd?.totalPages || 1)) break;
    const nr = await sendToContent(tabId, { action: "goToNextPage" });
    await log(`  已翻到下一页 (${page + 1}/${pd?.totalPages || 1})`);
    if (!nr?.moved) break;
    await sleep(archiveState.config.delayBetweenPages);
  }
  const deduped = dedupeSessions(all);
  await log(`  ${csName} 共提取 ${deduped.length} 个唯一 session_id`);
  return deduped;
}

// ─── 匀速放行队列 ───

async function runRateLimitedQueue(items, worker, getQuotaCount, getQuotaWindowMs) {
  let cursor = 0;
  const total = items.length;
  const running = new Set();
  let lastStartAt = 0;

  while (cursor < total && !archiveState.cancelled) {
    await waitWhilePaused();
    const quotaCount = Math.max(1, getQuotaCount());
    const quotaWindowMs = Math.max(1, getQuotaWindowMs());
    const openIntervalMs = Math.max(1, Math.floor(quotaWindowMs / quotaCount));
    const now = Date.now();
    const waitMs = Math.max(0, lastStartAt + openIntervalMs - now);

    if (lastStartAt > 0 && waitMs > 0) {
      await sleep(waitMs);
    }

    const index = cursor++;
    lastStartAt = Date.now();
    const p = worker(items[index], index, total).catch(() => {}).finally(() => running.delete(p));
    running.add(p);
  }

  if (running.size > 0) {
    await Promise.allSettled(running);
  }
}

// ─── 状态机辅助 ───

function resetRunState(nextPhase) {
  archiveState.running = true;
  archiveState.paused = false;
  archiveState.cancelled = false;
  archiveState.phase = nextPhase;
  archiveState.completedSessions = 0;
  archiveState.failedSessions = 0;
  archiveState.currentSessionId = null;
  archiveState.currentCsName = null;
  archiveState.log = [];
}

async function finishRun(nextPhase) {
  archiveState.running = false;
  archiveState.paused = false;
  archiveState.cancelled = false;
  archiveState.phase = nextPhase;
  archiveState.currentSessionId = null;
  archiveState.currentCsName = null;
  await updateAndPersist();
}

// ─── 链路 0：会话收集 ───

async function collectSessionFlow({ tabId }) {
  resetRunState("collecting");
  archiveState.totalSessions = 0;
  archiveState.collectedSessions = [];
  archiveState.availableCsRoles = [];
  archiveState.availableCsRoleStats = [];
  archiveState.selectedCsRoles = [];
  await saveProgress();
  try {
    await log("=== 开始获取 session_id 列表 ===");
    const csList = await sendToContent(tabId, { action: "getCSList" });
    if (!csList?.length) throw new Error("未找到客服列表，请确认当前在 IM 会话详情页面");
    await log(`找到 ${csList.length} 位客服`);

    for (const cs of csList) {
      if (archiveState.cancelled) break;
      await waitWhilePaused();
      archiveState.currentCsName = cs.name;
      archiveState.currentSessionId = null;
      await updateAndPersist();
      await log(`--- 处理客服: ${cs.name} (${cs.count} 会话) ---`);

      // 展开客服行（带重试）
      let expanded = false;
      for (let attempt = 1; attempt <= 3; attempt++) {
        try {
          await sendToContent(tabId, { action: "expandCS", csName: cs.name });
          expanded = true;
          break;
        } catch (e) {
          await log(`  展开重试 ${attempt}/3: ${e.message}`);
          await sleep(1000 * attempt);
        }
      }
      if (!expanded) {
        await log(`  ✗ 跳过客服 ${cs.name}: 展开失败`);
        continue;
      }
      await sleep(RATE_LIMIT_MS);

      // 设置分页大小（失败不终止，用默认分页继续）
      const pageSize = archiveState.config.pageSize || 100;
      try {
        await sendToContent(tabId, { action: "setPageSize", pageSize });
      } catch (e) {
        await log(`  ⚠ 设置分页失败，使用默认分页继续: ${e.message}`);
      }
      await sleep(RATE_LIMIT_MS);

      // 提取会话
      try {
        const sessions = await extractSessionsForCustomer(tabId, cs.name);
        archiveState.collectedSessions.push(...sessions);
        archiveState.totalSessions = archiveState.collectedSessions.length;
      } catch (e) {
        await log(`  ✗ 提取会话失败: ${e.message}`);
      }
      await updateAndPersist();
    }

    archiveState.collectedSessions = dedupeSessions(archiveState.collectedSessions);
    archiveState.totalSessions = archiveState.collectedSessions.length;
    reconcileCsRoleSelections(true);
    await log(`=== 收集完成: ${archiveState.collectedSessions.length} 条 ===`);
    await finishRun(archiveState.cancelled ? (archiveState.collectedSessions.length ? "ready" : "idle") : "ready");
  } catch (error) {
    await log("会话收集异常: " + error.message);
    await finishRun("idle");
  } finally {
    if (archiveState.running) await finishRun(archiveState.collectedSessions.length ? "ready" : "idle");
  }
}

// ─── 链路 1：SingleFile 归档 ───

async function archiveCollectedSessions() {
  if (!archiveState.collectedSessions.length) throw new Error("无可归档会话");
  const selectedSessions = getFilteredSessionsForSelectedRoles();
  if (!selectedSessions.length) throw new Error("未选中任何客服角色或所选角色下无会话");
  resetRunState("archiving_singlefile");
  archiveState.totalSessions = selectedSessions.length;
  archiveState.lastOutputKind = "singlefile";
  await saveProgress();
  try {
    const quotaCount = archiveState.config.concurrency || 1;
    const quotaWindowMs = archiveState.config.delayBetweenSaves || 1000;
    const openIntervalMs = Math.max(1, Math.floor(quotaWindowMs / quotaCount));
    await log(`=== SingleFile 归档: ${archiveState.totalSessions} 条, 平均每 ${Math.round(quotaWindowMs / 1000)} 秒打开 ${quotaCount} 页, 匀速间隔约 ${openIntervalMs} ms ===`);
    await runRateLimitedQueue(selectedSessions, async (sess, i, total) => {
      await log(`[${i+1}/${total}] ${sess.sessionId}...`);
      const tab = await openDetailPage(sess.sessionId);
      if (!tab) { archiveState.failedSessions++; await log(`  ✗ 打开失败`); return; }
      try {
        const fn = `${archiveState.config.outputPrefix}_${normalizeFilenamePart(sess.csName)}_${sess.sessionId}_${String(i+1).padStart(3,"0")}.html`;
        if (await saveTabWithSingleFile(tab.id, fn)) { archiveState.completedSessions++; await log(`  OK: ${fn}`); }
        else { archiveState.failedSessions++; await log(`  ✗ 保存失败`); }
      } finally { await safeCloseTab(tab.id); }
      await updateAndPersist();
    }, () => archiveState.config.concurrency || 1, () => archiveState.config.delayBetweenSaves || 1000);
    archiveState.lastOutputSummary = `成功 ${archiveState.completedSessions} / 失败 ${archiveState.failedSessions}`;
    await log(`=== 归档完成: ${archiveState.lastOutputSummary} ===`);
    await finishRun(archiveState.cancelled ? "ready" : "archived");
  } catch (error) {
    await log("归档异常: " + error.message);
    await finishRun("ready");
  } finally {
    await closeWorkWindow();
    if (archiveState.running) await finishRun("ready");
  }
}

// ─── 链路 2：结构化对话导出 ───

async function exportStructuredConversations() {
  if (!archiveState.collectedSessions.length) throw new Error("无可导出会话");
  const selectedSessions = getFilteredSessionsForSelectedRoles();
  if (!selectedSessions.length) throw new Error("未选中任何客服角色或所选角色下无会话");
  const formats = archiveState.selectedStructuredFormats;
  if (!formats.length) throw new Error("未选择导出格式");
  const markdownExportStamp = makeFilenameTimestamp();
  resetRunState("exporting_structured");
  archiveState.totalSessions = selectedSessions.length;
  archiveState.lastOutputKind = "structured";
  await saveProgress();
  try {
    const quotaCount = archiveState.config.concurrency || 1;
    const quotaWindowMs = archiveState.config.delayBetweenSaves || 1000;
    const openIntervalMs = Math.max(1, Math.floor(quotaWindowMs / quotaCount));
    await log(`=== 结构化导出: ${archiveState.totalSessions} 条, 平均每 ${Math.round(quotaWindowMs / 1000)} 秒打开 ${quotaCount} 页, 匀速间隔约 ${openIntervalMs} ms, 格式 ${formats.join("+")} ===`);
    await runRateLimitedQueue(selectedSessions, async (sess, i, total) => {
      await log(`[${i+1}/${total}] ${sess.sessionId}...`);
      const tab = await openDetailPage(sess.sessionId);
      if (!tab) { archiveState.failedSessions++; await log(`  ✗ 打开失败`); return; }
      try {
        await ensureDetailPageInjected(tab.id);
        const r = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: async (meta) => {
            const dp = globalThis.__IM_ARCHIVE_DETAIL_PAGE__;
            await dp.loadAllMessages({ settleMs: 400, stableRounds: 3 });
            return await dp.extractConversationStructured(meta);
          },
          args: [{ sessionId: sess.sessionId, csName: sess.csName }]
        });
        const data = r?.[0]?.result;
        if (!data?.messages) throw new Error("提取失败");
        await log(`  ${data.messages.length} 条消息`);
        const safe = normalizeFilenamePart(sess.csName);
        const seq = String(i+1).padStart(3,"0");
        const pfx = archiveState.config.outputPrefix;
        if (formats.includes("json")) {
          const url = "data:application/json;charset=utf-8," + encodeURIComponent(JSON.stringify(data, null, 2));
          await chrome.downloads.download({ url, filename: makeDownloadFilename(`${safe}/${pfx}_${sess.sessionId}_${seq}.json`), saveAs: false });
        }
        if (formats.includes("markdown")) {
          const md = createMarkdownFromStructured({ sessionId: sess.sessionId, csName: sess.csName, detailUrl: DETAIL_BASE_URL + sess.sessionId }, data.messages);
          const url = "data:text/markdown;charset=utf-8," + encodeURIComponent(md);
          await chrome.downloads.download({ url, filename: makeDownloadFilename(`${safe}/${pfx}_${sess.sessionId}_${seq}_${markdownExportStamp}.md`), saveAs: false });
        }
        archiveState.completedSessions++;
      } catch (error) {
        archiveState.failedSessions++;
        await log(`  ✗ ${error.message}`);
      } finally { await safeCloseTab(tab.id); }
      await updateAndPersist();
    }, () => archiveState.config.concurrency || 1, () => archiveState.config.delayBetweenSaves || 1000);
    archiveState.lastOutputSummary = `成功 ${archiveState.completedSessions} / 失败 ${archiveState.failedSessions}`;
    await log(`=== 导出完成: ${archiveState.lastOutputSummary} ===`);
    await finishRun(archiveState.cancelled ? "ready" : "exported");
  } catch (error) {
    await log("导出异常: " + error.message);
    await finishRun("ready");
  } finally {
    await closeWorkWindow();
    if (archiveState.running) await finishRun("ready");
  }
}

// ─── 链路 3：XLSX 链接导出 ───

async function exportLinksWorkbook() {
  if (!archiveState.collectedSessions.length) throw new Error("无可导出会话");
  resetRunState("exporting_links");
  archiveState.totalSessions = archiveState.collectedSessions.length;
  archiveState.lastOutputKind = "links";
  await saveProgress();
  try {
    await log("=== XLSX 链接导出 ===");
    const grouped = {};
    archiveState.collectedSessions.forEach((s, i) => {
      const k = s.csName || "Unknown";
      if (!grouped[k]) grouped[k] = [];
      grouped[k].push({ ...s, seq: i + 1 });
    });
    const usedNames = new Set();
    const sheets = [];
    for (const [csName, sessions] of Object.entries(grouped)) {
      if (!sessions.length) continue;
      let name = sanitizeSheetName(csName);
      let suf = 1; const base = name;
      while (usedNames.has(name)) name = `${base.substring(0,28)}_${suf++}`;
      usedNames.add(name);
      const rows = [["序号","会话ID","客服","创建时间","详情页链接"]];
      sessions.forEach(s => rows.push([String(s.seq), s.sessionId, s.csName, s.createTime||"", DETAIL_BASE_URL+s.sessionId]));
      sheets.push({ name, rows });
    }
    const bytes = await buildWorkbookBuffer({ sheets });
    const fn = `${archiveState.config.outputPrefix}_links.xlsx`;
    await chrome.downloads.download({
      url: `data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,${uint8ArrayToBase64(bytes)}`,
      filename: makeDownloadFilename(fn), saveAs: false
    });
    archiveState.completedSessions = archiveState.totalSessions;
    archiveState.lastOutputSummary = `${archiveState.totalSessions} 条会话`;
    await log(`=== XLSX 已保存: ${fn} (${bytes.length} bytes) ===`);
    await finishRun("exported");
  } catch (error) {
    await log("XLSX 导出异常: " + error.message);
    archiveState.failedSessions = archiveState.totalSessions;
    await finishRun("ready");
  } finally {
    if (archiveState.running) await finishRun("ready");
  }
}

// ─── 清空与重置 ───

async function clearCollectedData() {
  archiveState.collectedSessions = [];
  archiveState.availableCsRoles = [];
  archiveState.availableCsRoleStats = [];
  archiveState.selectedCsRoles = [];
  archiveState.totalSessions = 0;
  archiveState.completedSessions = 0;
  archiveState.failedSessions = 0;
  archiveState.lastOutputKind = null;
  archiveState.lastOutputSummary = null;
  archiveState.phase = "idle";
  archiveState.log = [];
  await updateAndPersist();
}

async function resetAll() {
  archiveState = {
    running: false, paused: false, cancelled: false,
    phase: "idle", totalSessions: 0, completedSessions: 0, failedSessions: 0,
    currentSessionId: null, currentCsName: null,
    log: [], collectedSessions: [],
    availableCsRoles: [], availableCsRoleStats: [], selectedCsRoles: [],
    selectedStructuredFormats: ["json", "markdown"],
    lastOutputKind: null, lastOutputSummary: null,
    config: { ...DEFAULT_CONFIG }
  };
  await chrome.storage.local.remove("archiveState");
  await updateAndPersist();
}

// ─── 消息处理 ───

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  async function handleMsg() {
    switch (msg.type) {
      case "start":
        if (archiveState.running) return { status: "error", message: "已有任务运行中" };
        collectSessionFlow({ tabId: sender.tab?.id || msg.tabId }).catch(e => log("收集异常: " + e.message));
        return { status: "ok" };

      case "archive":
      case "archiveSingleFile":
        if (archiveState.running) return { status: "error", message: "已有任务运行中" };
        if (!archiveState.collectedSessions.length) return { status: "error", message: "请先获取会话列表" };
        archiveCollectedSessions().catch(e => log("归档异常: " + e.message));
        return { status: "ok" };

      case "exportStructured":
        if (archiveState.running) return { status: "error", message: "已有任务运行中" };
        if (!archiveState.collectedSessions.length) return { status: "error", message: "请先获取会话列表" };
        exportStructuredConversations().catch(e => log("导出异常: " + e.message));
        return { status: "ok" };

      case "exportLinksWorkbook":
        if (archiveState.running) return { status: "error", message: "已有任务运行中" };
        if (!archiveState.collectedSessions.length) return { status: "error", message: "请先获取会话列表" };
        exportLinksWorkbook().catch(e => log("导出异常: " + e.message));
        return { status: "ok" };

      case "importLinksWorkbook":
        if (archiveState.running) return { status: "error", message: "任务运行中，无法导入" };
        if (!msg.base64 || typeof msg.base64 !== "string") return { status: "error", message: "缺少 xlsx 文件内容" };
        try {
          const importedSessions = await parseImportedLinksWorkbook(msg.base64);
          if (!importedSessions.length) {
            return { status: "error", message: "未从 xlsx 中解析到有效会话" };
          }
          archiveState.collectedSessions = importedSessions;
          archiveState.totalSessions = importedSessions.length;
          archiveState.completedSessions = 0;
          archiveState.failedSessions = 0;
          archiveState.phase = "ready";
          archiveState.lastOutputKind = "imported_links";
          archiveState.lastOutputSummary = `已导入 ${importedSessions.length} 条会话`;
          reconcileCsRoleSelections(true);
          await log(`=== 已导入链接表: ${msg.filename || "links.xlsx"}，共 ${importedSessions.length} 条会话 ===`);
          return { status: "ok", message: `导入成功：${importedSessions.length} 条会话` };
        } catch (error) {
          return { status: "error", message: `导入失败: ${error.message}` };
        }

      case "importLinksWorkbookPreview":
        if (!msg.base64 || typeof msg.base64 !== "string") return { status: "error", message: "缺少 xlsx 文件内容" };
        try {
          const importedSessions = await parseImportedLinksWorkbook(msg.base64);
          if (!importedSessions.length) {
            return { status: "error", message: "未从 xlsx 中解析到有效会话" };
          }
          return { status: "ok", preview: buildImportPreview(importedSessions) };
        } catch (error) {
          return { status: "error", message: `预览失败: ${error.message}` };
        }

      case "pause":
        archiveState.paused = true; await log("已暂停");
        return { status: "ok" };
      case "resume":
        archiveState.paused = false; await log("已恢复");
        return { status: "ok" };
      case "cancel":
        archiveState.cancelled = true; archiveState.paused = false;
        archiveState.phase = `cancelling_${archiveState.phase || "run"}`;
        await log("正在取消...");
        return { status: "ok" };

      case "getState":
        return { status: "ok", data: getStateSummary() };
      case "getConfig":
        return { status: "ok", config: archiveState.config };
      case "setConfig":
        archiveState.config = { ...archiveState.config, ...(msg.config || {}) };
        await saveProgress();
        return { status: "ok", config: archiveState.config };
      case "setStructuredFormats":
        if (Array.isArray(msg.formats))
          archiveState.selectedStructuredFormats = msg.formats.filter(f => f === "json" || f === "markdown");
        await saveProgress();
        return { status: "ok", formats: archiveState.selectedStructuredFormats };

      case "setSelectedCsRoles": {
        const available = getUniqueCsRolesFromSessions(archiveState.collectedSessions);
        const selected = Array.isArray(msg.roles)
          ? msg.roles.map(v => String(v || "").trim()).filter(Boolean)
          : [];
        archiveState.availableCsRoles = available;
        archiveState.availableCsRoleStats = getCsRoleStatsFromSessions(archiveState.collectedSessions);
        archiveState.selectedCsRoles = selected.filter(role => available.includes(role));
        await saveProgress();
        return {
          status: "ok",
          selectedCsRoles: archiveState.selectedCsRoles,
          availableCsRoles: archiveState.availableCsRoles,
          availableCsRoleStats: archiveState.availableCsRoleStats
        };
      }

      case "clearData":
        if (archiveState.running) return { status: "error", message: "任务运行中，无法清空" };
        await clearCollectedData();
        return { status: "ok" };
      case "resetAll":
        if (archiveState.running) return { status: "error", message: "任务运行中，无法重置" };
        await resetAll();
        return { status: "ok" };

      default:
        return { status: "error", message: "未知消息: " + msg.type };
    }
  }
  handleMsg().then(sendResponse);
  return true;
});

// ─── 生命周期 ───

chrome.runtime.onInstalled.addListener(async details => {
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  if (details.reason === "install") await saveProgress();
});
chrome.runtime.onStartup.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});
chrome.action.onClicked.addListener(async tab => {
  try { await chrome.sidePanel.open({ windowId: tab.windowId }); } catch (e) {}
});

loadProgress().then(async () => {
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  console.log("[IM-Archive] Background ready");
}).catch(e => console.warn("[IM-Archive] loadProgress failed:", e));
