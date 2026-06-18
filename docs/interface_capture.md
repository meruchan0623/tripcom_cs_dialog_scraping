# Ctrip IMExperience Interface Capture

## Capture Summary

- Capture time: `20260618_133256`
- Entry page: `https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience`
- Browser target reused: Microsoft Edge / web-access proxy target `6ABA3613CFAF1C3937D6E1FB3B538748`
- Real page state: logged in as vendor account, 历史咨询 section visible.
- Actions performed:
  1. Selected 客人来源 `Trip`.
  2. Expanded first customer-service row `vbk_2538177/门票活动旅游管家Sara`.
  3. Opened first session detail, producing detail page `https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=300001148712239`.

Artifacts: `captures/20260618_133256`

## Main Requests

### History metric cards

`POST https://m.ctrip.com/restapi/soa2/13807/getAllCustomerServiceMetricCardData`

Triggered by 客人来源 filter change. Request body includes `butype`, `productChannel`, date range, `timeType`, and `consultationScene`.

### Customer-service summary list

`POST https://m.ctrip.com/restapi/soa2/13807/getEmployeeDimMetricDetailsV3`

Triggered by Trip filter / table reload. Response includes `vendor_account_id`, `vendor_account_name`, and metrics such as `sev_session_count`.

### Session list for one customer-service account

`POST https://m.ctrip.com/restapi/soa2/13807/getSessionDimMetricDetailsV3`

Triggered by first row `展开详情`. Response includes `totalNum` and `tableDataItemList[].dimMap.session_id/session_create_time`. This is the main interface for collecting客服会话记录入口.

### Detail message page

Opening `查看详情` created detail page:

`https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=300001148712239`

The detail page loaded existing chat text in DOM. Resource timing and bundle evidence identify:

`POST https://m.ctrip.com/restapi/soa2/16037/getMessagesBySession`

Bundle evidence from `pages/queryMessages` + `commons` shows request body is `{"sessionId": "<session_id>"}`.

## Notes

- Browser XHR wrapper cannot read implicit Cookie headers; CLI replay should use `ctrip-cli-sessions/ctrip_cookie_header.txt` and the existing `build_headers(...)` shape.
- Ignore polling/notification endpoints for the historical record export path unless live message polling is needed: `11679/getRecentMessages`, `16566/queryCountByEid`.
