const assert = require('assert');
const fs = require('fs');
const { JSDOM } = require('jsdom');

function loadDetailPage(html) {
  const dom = new JSDOM(html, {
    url: 'https://imvendor.ctrip.com/queryMessages?sessionId=300001130759696',
    runScripts: 'outside-only'
  });
  const code = fs.readFileSync(require('path').join(__dirname, '..', 'detail-page.js'), 'utf8');
  dom.window.eval(code);
  return dom.window;
}

async function extractMessages(html) {
  const win = loadDetailPage(html);
  return win.__IM_ARCHIVE_DETAIL_PAGE__.extractConversationStructured({ sessionId: '300001130759696', csName: 'Fiona' });
}

(async () => {
  const html = `<!doctype html><html><body>
    <div class="chat_area-list"><ul>
      <li class="customer">
        <strong name="message_time">2026-06-02 17:35:22</strong>
        <span class="nickname">buyer</span>
        <div class="chat">
          <p class="chat-text">
            <span style="display:none">内容中包含敏感信息，请修改后再发</span>
            <span>как совершать звонки с помощью этой esim?</span>
            <span class="tran-group"><span class="tran-btn">翻译</span><span class="tran-status fail">来自google翻译</span></span>
            <span class="content-more">展开</span>
            <span class="cite-container"><span class="cite-text-content">quoted old message</span></span>
          </p>
        </div>
      </li>
      <li class="customer">
        <strong name="message_time">2026-06-02 17:35:24</strong>
        <span class="nickname">buyer</span>
        <div class="chat">
          <div class="order-list"><p class="order-title">订单咨询通知</p>
            <div class="order-detail"><dl>
              <dd><span class="label-title">产品 ID：</span><span class="label-body">81467905</span></dd>
              <dd><span class="label-title">产品名称：</span><span class="label-body">欧洲45地 5G eSIM｜通话+短信｜30天｜QR Code</span></dd>
              <dd><span class="label-title">使用日期：</span><span class="label-body">2026/06/02</span></dd>
            </dl></div>
          </div>
        </div>
      </li>
    </ul></div>
  </body></html>`;

  const result = await extractMessages(html);
  assert.strictEqual(result.messages[0].text, 'как совершать звонки с помощью этой esim?');
  assert.ok(!result.messages[0].text.includes('内容中包含敏感信息'));
  assert.ok(!result.messages[0].text.includes('翻译'));
  assert.ok(!result.messages[0].text.includes('quoted old message'));
  assert.strictEqual(result.messages[1].text, '');
  assert.strictEqual(result.messages[1].messageType, 'card');
  assert.deepStrictEqual(result.messages[1].orderCards[0].productId, '81467905');
  assert.deepStrictEqual(result.messages[1].orderCards[0].productName, '欧洲45地 5G eSIM｜通话+短信｜30天｜QR Code');
  console.log('detail_page_dom_filter_test passed');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
