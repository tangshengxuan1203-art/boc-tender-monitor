# 交行张江招标公告云端监控

该项目通过 GitHub Actions 每天 **北京时间 09:00** 访问交通银行智采平台供应商门户的公告页面，筛选包含“张江”或“张江园区”的公告。

无论是否发现新公告，都会向企业微信群机器人发送一次结果；抓取异常也会发送异常通知，避免静默漏报。

## 已配置的运行方式

- 监测页面：<https://bocom-gys.bankcomm.com/espuser/register/noticePage>
- 关注词：`张江, 张江园区`
- 执行时间：每天 09:00（中国标准时间）
- 手动测试：GitHub 仓库的 **Actions → Monitor Zhangjiang tenders → Run workflow**
- 已处理公告 ID：`data/seen.json`（由工作流自动维护）

## 必需的 GitHub Secret

在仓库 **Settings → Secrets and variables → Actions** 中保存下列任一名称：

- `WECOM_WEBHOOK`（推荐）
- `WECOM_WEBHOOK_URL`

Secret 值应为企业微信群机器人的完整 Webhook 地址。

> GitHub 的定时任务可能相对设定时间有数分钟排队延迟；但工作流按北京时间 09:00 触发，且不依赖电脑开机。
