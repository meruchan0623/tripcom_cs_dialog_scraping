# Ctrip Pure HTTP Request Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本次在真实浏览器里捕获到的 Ctrip/Trip.com 客服记录请求模式沉淀到纯 HTTP CLI，使 `run collect --via http` 与 `run export --kind structured --via http` 按真实浏览器 header、cookie 传输规则执行，并能用测试和小预算 live probe 验证。

**Architecture:** 保留当前 `im_archive_cli.ctrip_http` 的客户端结构，但把“列表页来源”和“详情页来源”的请求头构造拆开。列表接口继续走 `vbooking.ctrip.com -> m.ctrip.com/restapi/soa2/13807/*`，详情消息接口走 `imvendor.ctrip.com -> m.ctrip.com/restapi/soa2/16037/getMessagesBySession`，登录态统一来自本地 cookie header 文件或 auth JSON，不在代码里写入任何真实 secret。

**Tech Stack:** Python 3, `requests`, pytest, existing `imx` CLI, existing `.im_archive` state/output flow.

---

## 第一性原理

1. 真实浏览器 Network 事件是事实来源：实现必须匹配 CDP 捕获到的实际请求，而不是猜测 API 文档或旧代码默认值。
2. 登录态是浏览器 cookie 语义，不是显式 token：纯 HTTP 只能复刻 `.ctrip.com` 域 cookie 被发送到 `m.ctrip.com` 的结果；不要把 `vbooking.ctrip.com`、`imvendor.ctrip.com` 专属 cookie 当作必须发送项。
3. 来源页决定 CORS 形态：`13807` 列表接口使用 `Origin/Referer=https://vbooking.ctrip.com`，`16037` 消息接口使用 `Origin/Referer=https://imvendor.ctrip.com`，并额外带 `cookieOrigin: https://imvendor.ctrip.com`。
4. 纯 HTTP 成功标准不是“代码能发请求”，而是：小预算 live probe 能拿到 `ResponseStatus.Ack=Success` 且解析出会话或消息。
5. 所有阻碍都要可分类：`403` 是鉴权/风控/请求形态失败；空消息是详情接口或 body 合同失败；预算耗尽是停止条件，不是异常重试理由。

## 文件结构

- Modify: `im_archive_cli/ctrip_http.py`
  - 拆分列表接口 header 与详情接口 header。
  - 更新员工列表 body 的排序/filter 默认值，贴合真实近 7 天请求。
  - 更新详情消息 body，加入真实 `head.extension` 合同。
  - 保持请求预算、错误包装、归一化解析逻辑不变。
- Modify: `tests/test_ctrip_http.py`
  - 增加 header 合同测试。
  - 增加详情 body 合同测试。
  - 更新员工列表 body 默认值测试。
  - 增加 fake session 断言，确保详情接口使用 `cookieOrigin`。
- Modify: `docs/HERMES_AGENT_RUNBOOK.md`
  - 补充“纯 HTTP 可尝试条件”和“403 停止规则”。
- Optional Modify: `skills/hermes-ctrip-im-archive/SKILL.md`
  - 如果 runbook 验证通过，再同步 Hermes 操作摘要；计划执行阶段不要先改 skill。

## Task 1: 固化浏览器请求合同测试

**Files:**
- Modify: `tests/test_ctrip_http.py`

- [ ] **Step 1: 增加列表页 header 合同测试**

在 import 列表里加入计划中将实现的函数名：

```python
from im_archive_cli.ctrip_http import build_vbooking_headers
```

新增测试：

```python
def test_build_vbooking_headers_match_browser_contract() -> None:
    headers = build_vbooking_headers("foo=bar")

    assert headers["accept"] == "application/json, text/plain, */*"
    assert headers["content-type"] == "application/json;charset=UTF-8"
    assert headers["origin"] == "https://vbooking.ctrip.com"
    assert headers["referer"] == "https://vbooking.ctrip.com/"
    assert headers["appname"] == "vbkbusiness"
    assert headers["cookie"] == "foo=bar"
    assert "Edg/" in headers["user-agent"]
```

- [ ] **Step 2: 增加详情页 header 合同测试**

在 import 列表里加入：

```python
from im_archive_cli.ctrip_http import build_imvendor_headers
```

新增测试：

```python
def test_build_imvendor_headers_match_browser_contract() -> None:
    headers = build_imvendor_headers("foo=bar")

    assert headers["accept"] == "application/json, text/plain, */*"
    assert headers["content-type"] == "application/json"
    assert headers["origin"] == "https://imvendor.ctrip.com"
    assert headers["referer"] == "https://imvendor.ctrip.com/"
    assert headers["cookieorigin"] == "https://imvendor.ctrip.com"
    assert headers["cookie"] == "foo=bar"
```

- [ ] **Step 3: 更新员工列表 body 默认值测试**

修改 `test_build_employee_body_uses_cli_defaults` 的断言：

```python
assert body["filterType"] == ""
assert body["orderType"] == "desc"
```

- [ ] **Step 4: 更新详情 body 合同测试**

将 `test_build_detail_body_uses_session_contract` 的期望改成真实消息接口合同：

```python
assert body == {
    "sessionId": "s1",
    "head": {
        "cver": "2",
        "extension": [
            {"name": "cpc", "value": "pc"},
            {"name": "protocal", "value": "https"},
            {"name": "amp-product-type", "value": "IM"},
            {"name": "amp-account-source", "value": "vbk"},
            {"name": "client-source", "value": ""},
            {"name": "locale", "value": "zh-CN"},
        ],
    },
}
```

- [ ] **Step 5: 运行测试确认失败**

Run:

```bash
python3 -m pytest tests/test_ctrip_http.py -q
```

Expected: FAIL，失败点包括 `build_vbooking_headers` / `build_imvendor_headers` 未定义、员工列表默认值不匹配、详情 body 不匹配。

## Task 2: 实现最小请求头与 body 修正

**Files:**
- Modify: `im_archive_cli/ctrip_http.py`

- [ ] **Step 1: 增加来源常量和 UA 常量**

在 URL 常量附近加入：

```python
VBOOKING_ORIGIN = "https://vbooking.ctrip.com"
VBOOKING_REFERER = "https://vbooking.ctrip.com/"
IMVENDOR_ORIGIN = "https://imvendor.ctrip.com"
IMVENDOR_REFERER = "https://imvendor.ctrip.com/"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
)
```

- [ ] **Step 2: 替换通用 `build_headers`**

保留兼容名，并新增两个明确来源函数：

```python
def build_vbooking_headers(cookie_header: str) -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=UTF-8",
        "origin": VBOOKING_ORIGIN,
        "referer": VBOOKING_REFERER,
        "appname": "vbkbusiness",
        "user-agent": BROWSER_USER_AGENT,
        "cookie": cookie_header,
    }


def build_imvendor_headers(cookie_header: str) -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": IMVENDOR_ORIGIN,
        "referer": IMVENDOR_REFERER,
        "cookieorigin": IMVENDOR_ORIGIN,
        "user-agent": BROWSER_USER_AGENT,
        "cookie": cookie_header,
    }


def build_headers(cookie_header: str) -> dict[str, str]:
    return build_vbooking_headers(cookie_header)
```

- [ ] **Step 3: 更新员工列表 body**

在 `build_employee_body` 中改两项：

```python
"filterType": "",
"orderType": "desc",
```

保持 `build_session_body` 的 `orderType: "asc"` 不变，因为会话列表真实请求按 `session_create_time` 正序。

- [ ] **Step 4: 更新详情 body**

替换 `build_detail_body` 的默认 body：

```python
body: dict[str, Any] = {
    "sessionId": session.session_id,
    "head": {
        "cver": "2",
        "extension": [
            {"name": "cpc", "value": "pc"},
            {"name": "protocal", "value": "https"},
            {"name": "amp-product-type", "value": "IM"},
            {"name": "amp-account-source", "value": "vbk"},
            {"name": "client-source", "value": ""},
            {"name": "locale", "value": "zh-CN"},
        ],
    },
}
```

保留 `ctrip_im_detail_extra_body` 的 `body.update(extra)`，让未来实测到的额外字段仍可配置注入。

- [ ] **Step 5: 让详情客户端使用 imvendor header**

在 `CtripImDetailHttpClient` 中覆盖请求头构造：

```python
def build_request_headers(self) -> dict[str, str]:
    return build_imvendor_headers(self.cookie_header)
```

同时在 `CtripImHttpClient.post_json` 中从：

```python
response = self.session.post(url, headers=build_headers(self.cookie_header), json=body, timeout=timeout)
```

改为：

```python
response = self.session.post(url, headers=self.build_request_headers(), json=body, timeout=timeout)
```

并在 `CtripImHttpClient` 增加：

```python
def build_request_headers(self) -> dict[str, str]:
    return build_vbooking_headers(self.cookie_header)
```

- [ ] **Step 6: 运行单测**

Run:

```bash
python3 -m pytest tests/test_ctrip_http.py -q
```

Expected: PASS。

## Task 3: 增加 header 使用路径回归测试

**Files:**
- Modify: `tests/test_ctrip_http.py`

- [ ] **Step 1: 增加列表客户端发送 vbooking header 的 fake session 测试**

新增测试：

```python
def test_collect_http_client_posts_with_vbooking_headers(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(ctrip_cookie_header_file=str(cookie_file), ctrip_auth_json=str(tmp_path / "missing.json"))

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"ResponseStatus":{"Ack":"Success"}}'

        def json(self) -> dict:
            return {"ResponseStatus": {"Ack": "Success"}}

    class FakeSession:
        def __init__(self) -> None:
            self.kwargs = None

        def post(self, *args, **kwargs):
            self.kwargs = kwargs
            return FakeResponse()

    fake = FakeSession()
    client = CtripImHttpClient(cfg, session=fake, request_interval_sec=0)

    client.post_json("https://m.ctrip.com/restapi/soa2/13807/example", {})

    headers = fake.kwargs["headers"]
    assert headers["origin"] == "https://vbooking.ctrip.com"
    assert headers["referer"] == "https://vbooking.ctrip.com/"
    assert headers["appname"] == "vbkbusiness"
    assert headers["cookie"] == "foo=bar"
```

- [ ] **Step 2: 增加详情客户端发送 imvendor header 的 fake session 测试**

新增测试：

```python
def test_detail_http_client_posts_with_imvendor_headers(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(
        ctrip_cookie_header_file=str(cookie_file),
        ctrip_auth_json=str(tmp_path / "missing.json"),
        ctrip_im_detail_messages_url="http://127.0.0.1/detail",
    )

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"ResponseStatus":{"Ack":"Success"},"messageList":[{"msgContent":"hello"}]}'

        def json(self) -> dict:
            return {"ResponseStatus": {"Ack": "Success"}, "messageList": [{"msgContent": "hello"}]}

    class FakeSession:
        def __init__(self) -> None:
            self.kwargs = None

        def post(self, *args, **kwargs):
            self.kwargs = kwargs
            return FakeResponse()

    fake = FakeSession()
    client = CtripImDetailHttpClient(cfg, session=fake, request_interval_sec=0)

    client.fetch_conversation(SessionRecord(session_id="s1", cs_name="Alice"))

    headers = fake.kwargs["headers"]
    assert headers["origin"] == "https://imvendor.ctrip.com"
    assert headers["referer"] == "https://imvendor.ctrip.com/"
    assert headers["cookieorigin"] == "https://imvendor.ctrip.com"
    assert headers["cookie"] == "foo=bar"
```

- [ ] **Step 3: 运行目标测试**

Run:

```bash
python3 -m pytest tests/test_ctrip_http.py tests/test_http_export.py -q
```

Expected: PASS。

## Task 4: 小预算 live probe 验证

**Files:**
- No code changes.
- Runtime artifacts: `.im_archive/logs/run_YYYYMMDD.log`, `.im_archive/state.json`, optional `.im_archive/ctrip-request-ledger-YYYYMMDD-http-probe.json`

- [ ] **Step 1: 检查脱敏登录态状态**

Run:

```bash
python3 -m im_archive_cli.imx_cli auth status
```

Expected: 输出 `selected` 指向 cookie header 或 auth JSON；只显示 cookie 名称和长度，不显示 cookie 值。

- [ ] **Step 2: 用 1 次请求预算验证员工列表第一跳**

Run:

```bash
python3 -m im_archive_cli.imx_cli run collect \
  --via http \
  --start-date 2026-06-22 \
  --end-date 2026-06-28 \
  --page-size 10 \
  --max-pages 1 \
  --request-budget 1 \
  --request-ledger .im_archive/ctrip-request-ledger-20260622-20260628-http-probe.json
```

Expected: 如果请求模式已修复，第一跳不再是空体 `HTTP 403`。预算为 1 时可能在准备请求单客服会话列表前耗尽；这属于预期停止条件。若仍返回 `HTTP 403`，停止执行并记录为 cookie 新鲜度或风控层问题，不进入导出阶段。

- [ ] **Step 3: 用小范围 include 验证完整 collect**

如果 Step 2 不是 `403`，选一个页面上真实存在的客服，例如 `vbk_2560483`，运行：

```bash
python3 -m im_archive_cli.imx_cli run collect \
  --via http \
  --start-date 2026-06-22 \
  --end-date 2026-06-28 \
  --include vbk_2560483 \
  --page-size 10 \
  --max-pages 1 \
  --request-budget 3 \
  --request-ledger .im_archive/ctrip-request-ledger-20260622-20260628-http-collect.json
```

Expected: `.im_archive/state.json` 写入至少 1 条 `collected_sessions`，日志显示 `采集结束: collected=<N>`。

- [ ] **Step 4: 配置详情消息接口验证来源**

确保 config 中已有浏览器验证来源；如果没有，先写入临时本地配置，不直接改默认仓库配置：

```yaml
ctrip_im_detail_messages_url: https://m.ctrip.com/restapi/soa2/16037/getMessagesBySession
ctrip_im_detail_verified_source: browser_detail_xhr
```

Expected: `CtripImDetailHttpClient` 不再因未验证真实 Ctrip endpoint 而拒绝发请求。

- [ ] **Step 5: 小预算导出一个会话**

先只选一个客服或导入一个 session，再运行：

```bash
python3 -m im_archive_cli.imx_cli run export \
  --kind structured \
  --via http \
  --formats json \
  --request-budget 1 \
  --request-ledger .im_archive/ctrip-request-ledger-20260622-20260628-http-export.json
```

Expected: `.im_archive/output` 或当前 config 的 `output_dir` 下出现一个 `IMChatlogExport_*.json`，JSON 中 `messages` 非空。若 `messages` 为空，停止并对照 `getMessagesBySession` body/header 合同。

## Task 5: 文档与操作边界更新

**Files:**
- Modify: `docs/HERMES_AGENT_RUNBOOK.md`
- Optional Modify after verification: `skills/hermes-ctrip-im-archive/SKILL.md`

- [ ] **Step 1: 更新 runbook 的纯 HTTP 条件**

在 Ctrip/Trip.com 执行链路章节加入：

```markdown
### 纯 HTTP 模式的使用条件

纯 HTTP 只在以下条件同时满足时使用：

- `imx auth status` 能找到可用 cookie header 或 auth JSON。
- `run collect --via http` 的 1 请求预算 probe 不再返回空体 `HTTP 403`。
- 详情接口配置为 `https://m.ctrip.com/restapi/soa2/16037/getMessagesBySession`，且 `ctrip_im_detail_verified_source: browser_detail_xhr`。

请求头合同：

- `13807/*` 列表接口使用 `Origin/Referer=https://vbooking.ctrip.com`，显式 `appname: vbkbusiness`。
- `16037/getMessagesBySession` 详情接口使用 `Origin/Referer=https://imvendor.ctrip.com`，显式 `cookieOrigin: https://imvendor.ctrip.com`。
- 登录态来自 `.ctrip.com` 域 cookie；不要在日志、文档或提交中写入真实 cookie 值。

停止规则：

- 任一 live probe 返回 `HTTP 401/403`，停止纯 HTTP 流程，改用 CDP 页面上下文或刷新浏览器登录态。
- `messages` 为空时不视为成功导出，必须记录失败并保留失败样本。
```

- [ ] **Step 2: 只在 live probe 成功后更新 workspace skill**

如果 Task 4 全部通过，再同步 `skills/hermes-ctrip-im-archive/SKILL.md` 的“已验证链路”：

```markdown
- 纯 HTTP 可作为小预算 probe 通过后的加速路径；正式无人值守仍需保留 CDP fallback。
```

如果 Task 4 未通过，不更新 skill 的默认路径，避免把未验证的 HTTP 路径宣传成可用主链路。

- [ ] **Step 3: 运行文档相关轻量检查**

Run:

```bash
rg -n "cookie=|vbkticket=|bticket=|GUID=" docs skills im_archive_cli tests
```

Expected: 不出现真实 cookie 值。只允许出现 cookie 名称、脱敏说明或测试假值 `foo=bar`。

## 验收标准

- `python3 -m pytest tests/test_ctrip_http.py tests/test_http_export.py -q` 通过。
- `run collect --via http` 的小预算 probe 不再在第一跳返回空体 `HTTP 403`，或如果仍返回，日志能明确分类为登录态/风控阻碍。
- `getSessionDimMetricDetailsV3` 请求使用 `vbooking` 来源 header。
- `getMessagesBySession` 请求使用 `imvendor` 来源 header 与 `cookieOrigin`。
- 所有日志、测试、文档均不包含真实 cookie/token 值。

## 执行方式

执行方式有两个：
Subagent-Driven：按计划分派实现和验证，适合多文件请求合同修复。子代理模型：gpt-5.3-codex-spark。
Inline Execution：我在当前线程直接按计划实现、跑静态测试、构建并做页面验证。
你选一个方式，我继续执行。
