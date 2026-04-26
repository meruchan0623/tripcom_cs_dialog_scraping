// single-file-core.js — 简化版页面保存器
// 完整版可从 https://github.com/gildas-lormeau/SingleFile 获取
// 此版本提供基础功能，后续可替换为完整版

/**
 * 将当前页面序列化为完整 HTML（含内联样式和资源引用）
 * @param {Object} options
 * @returns {Promise<{html:string, resources:Array}>}
 */
async function getPageData(options = {}) {
  const doc = options.doc || document;
  
  // 收集所有样式
  const styles = collectStyles(doc);
  
  // 复制 HTML
  let html = doc.documentElement.outerHTML;

  // 注入样式
  const styleBlock = `<style id="im-archive-styles">\n/* ===== Inlined Styles (IM Archive) ===== */\n${styles}\n</style>`;
  html = html.replace('</head>', styleBlock + '\n</head>');

  // 注入 base href
  if (!doc.querySelector('base')) {
    html = html.replace('<head>', `<head>\n<base href="${doc.location.href}">`);
  }

  // 添加归档元信息注释
  const archiveComment = `<!-- Saved by IM会话归档助手 on ${new Date().toISOString()} -->`;
  html = archiveComment + '\n' + html;

  return {
    html,
    title: doc.title,
    url: doc.location.href,
    savedAt: new Date().toISOString()
  };
}

/** 收集所有 CSS 规则 */
function collectStyles(doc) {
  const allRules = [];
  
  try {
    for (const ss of Array.from(doc.styleSheets)) {
      try {
        for (const rule of Array.from(ss.cssRules || [])) {
          if (rule.type === rule.STYLE_RULE || 
              rule.type === rule.MEDIA_RULE ||
              rule.type === rule.SUPPORTS_RULE) {
            allRules.push(rule.cssText);
          }
        }
      } catch(e) {
        // 跨域样式表无法访问，跳过
        allRules.push(`/* Blocked stylesheet: ${(e.message||'').substring(0,100)} */`);
      }
    }
  } catch(e) {}

  // 内联 style 标签
  for (const el of doc.querySelectorAll('style')) {
    if (!el.id || !el.id.startsWith('im-archive-')) {
      allRules.push(el.textContent);
    }
  }

  return allRules.join('\n');
}

// 导出（兼容不同模块系统）
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { getPageData };
} else if (typeof self !== 'undefined') {
  self.SingleFileCore = { getPageData };
}
