(() => {
  if (globalThis.__IM_ARCHIVE_DETAIL_PAGE_READY__) {
    return;
  }

  const CONTAINER_SELECTORS = [
    ".chat_list_content",
    ".chat_area-list",
    ".chat_area"
  ];

  const LIST_SELECTORS = [
    ".chat_area-list",
    ".chat_list",
    ".chat_list_content ul"
  ];

  function wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function queryFirst(selectors) {
    for (const selector of selectors) {
      const el = document.querySelector(selector);
      if (el) return el;
    }
    return null;
  }

  async function waitForConversationReady(timeout = 15000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      const container = queryFirst(CONTAINER_SELECTORS);
      const list = queryFirst(LIST_SELECTORS);
      if (container && list) {
        return { container, list };
      }
      await wait(200);
    }
    throw new Error("未找到对话容器");
  }

  function getMessageItems(list) {
    return Array.from(list.querySelectorAll("li")).filter(item => {
      // 匹配客户/客服消息和系统消息
      const cls = item.className || "";
      if (/customer|service|system/i.test(cls)) return true;
      return item.querySelector(".chat, .chat-text, .main_imgMsg, .nickname");
    });
  }

  function getLastItemSignature(list) {
    const items = getMessageItems(list);
    const last = items[items.length - 1];
    return last ? last.textContent.trim().slice(-120) : "";
  }

  async function loadAllMessages(options = {}) {
    const { container, list } = await waitForConversationReady();
    const maxRounds = Number(options.maxRounds || 40);
    const settleMs = Number(options.settleMs || 600);
    const stableRounds = Number(options.stableRounds || 4);

    let unchanged = 0;
    let previousCount = -1;
    let previousHeight = -1;
    let previousSignature = "";

    container.scrollTop = container.scrollHeight;
    await wait(300);

    for (let round = 0; round < maxRounds; round++) {
      container.scrollTop = 0;
      container.dispatchEvent(new Event("scroll", { bubbles: true }));
      await wait(settleMs);

      const items = getMessageItems(list);
      const count = items.length;
      const height = container.scrollHeight;
      const signature = getLastItemSignature(list);

      if (count === previousCount && height === previousHeight && signature === previousSignature) {
        unchanged++;
      } else {
        unchanged = 0;
      }

      previousCount = count;
      previousHeight = height;
      previousSignature = signature;

      if (unchanged >= stableRounds) {
        break;
      }
    }

    container.scrollTop = container.scrollHeight;
    await wait(300);

    return {
      count: getMessageItems(list).length,
      signature: getLastItemSignature(list)
    };
  }

  function inferTimestamp(li) {
    // 优先：<strong name=message_time>
    const strongTime = li.querySelector("strong[name=message_time]");
    if (strongTime?.textContent?.trim()) {
      return strongTime.textContent.trim();
    }

    const candidates = [
      ".date strong",
      ".date",
      ".time",
      ".msg-time",
      ".message-time",
      "[class*=date]",
      "[class*=time]"
    ];

    for (const selector of candidates) {
      const el = li.querySelector(selector);
      const text = el?.textContent?.trim();
      if (text) return text;
    }

    const text = li.textContent.replace(/\s+/g, " ").trim();
    const match = text.match(/(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)|(\d{1,2}:\d{2}(?::\d{2})?)/);
    return match ? match[0] : "";
  }

  function inferSenderRole(li, senderName) {
    const classText = li.className || "";
    if (/system|notice/i.test(classText)) return "system";
    if (/service/i.test(classText)) return "seller";
    if (senderName) {
      if (/csm\d|商家|顾问|客服/i.test(senderName)) return "seller";
      return "buyer";
    }
    return "unknown";
  }

  function inferMessageType(chatEl, images, text) {
    if (!chatEl) return "system";
    if (images.length) return "image";
    if (text) return "text";
    return "unknown";
  }

  function extractMessage(li, index, meta) {
    const chatEl = li.querySelector(".chat");
    const nicknameEl = li.querySelector(".nickname");
    const senderName = nicknameEl?.textContent?.trim() || "";
    const timestampText = inferTimestamp(li);

    // 只收集聊天区域内的图片，排除头像（.header img）
    const chatImages = chatEl
      ? Array.from(chatEl.querySelectorAll("img"))
      : [];
    const images = chatImages
      .filter(img => {
        const src = img.getAttribute("src") || "";
        // 排除空 src、SVG 占位符、头像
        if (!src || src.startsWith("data:image/svg+xml")) return false;
        if (img.closest(".header")) return false;
        return true;
      })
      .map(img => ({
        src: img.getAttribute("src") || "",
        alt: img.getAttribute("alt") || ""
      }));

    const textCandidates = [
      chatEl?.querySelector("p.chat-text span"),
      chatEl?.querySelector("p.chat-text"),
      chatEl?.querySelector("div.chat-text"),
      chatEl?.querySelector(".chat-text"),
      li.querySelector(".chat-text")
    ].filter(Boolean);

    const text = textCandidates
      .map(node => node.textContent.trim())
      .find(Boolean) || "";

    const senderRole = inferSenderRole(li, senderName);
    const messageType = inferMessageType(chatEl, images, text);

    return {
      sessionId: meta.sessionId,
      csName: meta.csName,
      detailUrl: location.href,
      sequence: index + 1,
      timestampText,
      senderRole,
      senderName,
      messageType,
      text,
      rawHtml: chatEl ? chatEl.innerHTML : li.innerHTML,
      attachments: images
    };
  }

  function createMarkdown(meta, messages) {
    const lines = [
      `# 会话 ${meta.sessionId}`,
      "",
      `- 客服: ${meta.csName || "Unknown"}`,
      `- 链接: ${meta.detailUrl}`,
      `- 消息数: ${messages.length}`,
      ""
    ];

    for (const message of messages) {
      lines.push(`## ${message.sequence}. ${message.senderRole}${message.senderName ? ` / ${message.senderName}` : ""}`);
      lines.push("");
      lines.push(`- 时间: ${message.timestampText || "-"}`);
      lines.push(`- 类型: ${message.messageType}`);
      lines.push(`- 文本: ${message.text || (message.messageType === "image" ? "[图片消息]" : "[空内容]")}`);
      if (message.attachments.length) {
        lines.push(`- 附件: ${message.attachments.map(item => item.src).join(", ")}`);
      }
      lines.push("");
    }

    return lines.join("\n");
  }

  function downloadText(filename, content, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.style.display = "none";
    document.documentElement.appendChild(anchor);
    anchor.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      anchor.remove();
    }, 1500);
  }

  async function extractConversationStructured(meta = {}) {
    const { list } = await waitForConversationReady();
    const items = getMessageItems(list);
    const messages = items.map((li, index) => extractMessage(li, index, {
      sessionId: meta.sessionId || "",
      csName: meta.csName || "",
      detailUrl: location.href
    }));

    return {
      sessionId: meta.sessionId || "",
      csName: meta.csName || "",
      detailUrl: location.href,
      title: document.title || "",
      createTime: meta.createTime || "",
      exportedAt: new Date().toISOString(),
      messages
    };
  }

  globalThis.__IM_ARCHIVE_DETAIL_PAGE_READY__ = true;
  globalThis.__IM_ARCHIVE_DETAIL_PAGE__ = {
    waitForConversationReady,
    loadAllMessages,
    extractConversationStructured,
    createMarkdown,
    downloadText
  };
})();
