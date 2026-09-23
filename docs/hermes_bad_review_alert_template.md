# Hermes Agent 携程客服差评监控 Cron

## 已启用任务

| 任务 | Job ID | 上海时间 | 实际执行范围 |
|---|---|---|---|
| 携程客服差评每日预警与增量标记 | `3035fcf31060` | 每天 `07:00` | 昨天 |
| 携程客服自然月差评率监控与增量标记 | `3dffea2bbc63` | 每天 `07:20` 轻量触发 | 仅周一或当月倒数第二天；当月 1 日至当天；投递 `telegram:-5186417467` |

两项均为 `no_agent=True` 脚本任务，不启动 LLM。第二项用 `Asia/Shanghai` 动态计算月末倒数第二天；若与周一重合只执行一次，其他日期 stdout 为空且不投递。

## 业务指标与 HTTP 链路

正式执行只走 HTTP API：

1. `getAllCustomerServiceMetricCardData`：读取 `session_evaluate_count`（总客服评价数）与 `low_comment_rate`（差评率）；
2. `getEmployeeDimMetricDetailsV3`：读取客服维度 `low_comment_cnt`；
3. `getSessionDimMetricDetailsV3`：定位并去重差评 `sessionId`；
4. `getMessagesBySession`：导出差评对话。

差评数还会按 `差评数 / 总评价数` 重新计算并与接口差评率对账。认证、分页、接口字段缺失、详情失败或对账失败均返回非零；不能作为真实 0 条结果。

正式任务不启动或调用 ego-browser、Chrome、Edge、CDP、Selenium，也不读取或改写 `.im_archive/state.json`。

## 增量去重落盘合同

所有差评对话落盘必须为**增量去重落盘**：

- 已存在的原始 `IMChatlogExport_*.json` 绝不改名、覆盖或复制；
- 在原文件旁新增 `<原文件>.bad-review.flag`，扩展名不是 `.json`，不会被客服会话解析器误读；
- marker 不存在才创建，已存在则校验 `sessionId` 并复用；
- 原归档缺失时，本次 HTTP 详情写入报告目录 `bad-review-conversations/<sessionId>.bad-review.json`；同名文件存在时只校验并复用，冲突或损坏时拒绝覆盖并失败；
- 同日期重复运行不会生成重复会话文件，汇总中的 `landing.mode` 固定为 `incremental_deduplicated_sidecar`；
- `summary.json` 和 XLSX `差评会话` 工作表记录落盘状态、源路径和 marker 路径。

## 输出

```text
/Users/tsimclaw/Downloads/Ctrip-CS-dialog/bad-reviews/YYYYMMDD-YYYYMMDD/
  summary.json
  bad_reviews_YYYYMMDD-YYYYMMDD.xlsx
  bad-review-conversations/        # 仅原归档缺失时出现
```

XLSX 包含：`差评汇总`、`客服差评统计`、`差评会话`、`聊天内容`。所有 ID、订单号、日期和数字字段按文本写入。

## no-agent 脚本

```text
~/.hermes/scripts/trip_cs_bad_review_daily.sh
~/.hermes/scripts/trip_cs_bad_review_mtd_monitor.sh
/Users/tsimclaw/Develop/tripcom_cs_dialog_scraping/scripts/hermes_bad_review_watch.py
```

行为：

- 每日无差评：静默；
- 每日有差评：发送差评数、增量去重落盘状态与 XLSX；
- 周一/月倒数第二天且差评率 `<30%`：仅发送一句本月总评价数、差评数、差评率及“低于预警线”，不附文件；
- 周一/月倒数第二天且差评率 `>=30%`：向运营自动化执行组发送明确预警，包含本月总评价数、差评数、差评率、差评对话数、聊天导出成功/失败数、增量去重落盘状态，并附 XLSX；
- 失败：非零退出，仅输出简短中文 `携程客服差评预警执行失败了，原因：...`；
- 不输出 Cookie、Authorization、Token、SID 或完整请求头。

## 手动执行

```bash
cd /Users/tsimclaw/Develop/tripcom_cs_dialog_scraping
python3 -m im_archive_cli.imx_cli run bad-reviews   --start-date YYYY-MM-DD   --end-date YYYY-MM-DD   --output-dir /Users/tsimclaw/Downloads/Ctrip-CS-dialog   --page-size 100   --max-pages 50   --request-interval-sec 0   --mark-landed   --strict
```

验收应回读 `summary.json`、XLSX 四个工作表、marker 对应源文件，并确认 `.im_archive/state.json` 哈希未变化。
