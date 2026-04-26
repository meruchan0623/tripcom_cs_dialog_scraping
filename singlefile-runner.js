(() => {
  if (globalThis.__IM_ARCHIVE_SINGLEFILE_READY__) {
    return;
  }

  async function downloadWithSingleFile(options = {}) {
    if (!globalThis.singlefile || typeof globalThis.singlefile.getPageData !== "function") {
      throw new Error("SingleFile core 未注入");
    }

    const pageData = await globalThis.singlefile.getPageData({
      removeFrames: false,
      removeHiddenElements: true,
      removeUnusedStyles: true,
      removeUnusedFonts: true,
      compressContent: false,
      insertCanonicalLink: true,
      insertMetaNoIndex: false,
      insertMetaCSP: true,
      loadDeferredImages: true,
      loadDeferredImagesMaxIdleTime: 1500,
      loadDeferredImagesBeforeFrames: true,
      filenameTemplate: "{page-title}",
      ...options
    });

    const content = Array.isArray(pageData.content)
      ? new Uint8Array(pageData.content)
      : pageData.content;

    const blob = new Blob([content], {
      type: pageData.mimeType || "text/html"
    });

    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = options.filename || pageData.filename || "page.html";
    anchor.rel = "noopener";
    anchor.style.display = "none";
    document.documentElement.appendChild(anchor);
    anchor.click();

    setTimeout(() => {
      URL.revokeObjectURL(url);
      anchor.remove();
    }, 1500);

    return {
      filename: anchor.download,
      title: pageData.title || document.title || "",
      mimeType: pageData.mimeType || "text/html"
    };
  }

  async function getPageContent(options = {}) {
    if (!globalThis.singlefile || typeof globalThis.singlefile.getPageData !== "function") {
      throw new Error("SingleFile core 未注入");
    }

    const pageData = await globalThis.singlefile.getPageData({
      removeFrames: false,
      removeHiddenElements: true,
      removeUnusedStyles: true,
      removeUnusedFonts: true,
      compressContent: false,
      insertCanonicalLink: true,
      insertMetaNoIndex: false,
      insertMetaCSP: true,
      loadDeferredImages: true,
      loadDeferredImagesMaxIdleTime: 1500,
      loadDeferredImagesBeforeFrames: true,
      filenameTemplate: "{page-title}",
      ...options
    });

    const content = Array.isArray(pageData.content)
      ? new Uint8Array(pageData.content)
      : pageData.content;

    // 将内容转成字符串返回（不触发下载）
    if (content instanceof Uint8Array) {
      return new TextDecoder().decode(content);
    }
    return typeof content === "string" ? content : String(content);
  }

  globalThis.__IM_ARCHIVE_SINGLEFILE_READY__ = true;
  globalThis.__IM_ARCHIVE_SINGLEFILE_SAVE__ = downloadWithSingleFile;
  globalThis.__IM_ARCHIVE_SINGLEFILE_GET_CONTENT__ = getPageContent;
})();
