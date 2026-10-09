# 交行采购公告云端监控

该项目通过 GitHub Actions 每天按 **北京时间 10:30（UTC 02:30）** 定时触发，访问交通银行智采平台供应商门户，监测“张江园区”和“总行采购”公告。不依赖个人电脑开机；实际启动及企业微信送达允许延迟。

无论是否发现新公告，每次执行都会向企业微信群机器人发送结果；抓取异常时会尝试发送异常通知。若机器人或网络本身故障，通知也可能失败，需查看 Actions 日志。

## 运行方式

- 监测页面：<https://bocom-gys.bankcomm.com/espuser/register/noticePage>
- 张江园区：公告标题包含“张江”
- 总行采购：采购人为“总行”，公告类型为“采购公告”或“招标公告”
- 定时触发：`.github/workflows/boc-daily-monitor.yml` 中的 `30 2 * * *`，即每天北京时间 10:30
- 手动测试：**Actions → Daily BOC tender monitor → Run workflow**，选择 `main`
- 外部兼容入口：向 `main` 提交 `data/automation-trigger.json` 的变更会触发 `push` 工作流
- 已处理公告 ID：`data/seen.json`（工作流成功后写回仓库）

GitHub Actions schedule 已提供仓库内的独立定时入口。外部云调度器是否启用、其时区、凭证及运行状态尚未确认，不能从本仓库推断。

若外部调度器与 schedule 同时触发，可能执行两次并发送两条消息。现有 concurrency 只限制同时执行，不提供每天只推送一次的去重。确认外部调度器后，可选择停用其定时任务，或另行增加每日成功执行去重。

## 触发文件说明

`data/automation-trigger.json` 的 `timezone`、`scheduled_time` 和 `github_schedule_cron_utc` 是时间配置说明；GitHub Actions 实际定时由工作流 YAML 决定，这些字段不会自动修改外部调度器。

`last_triggered_at` 保留最后一次记录的外部触发时间，不代表最后一次 schedule 运行或监控成功时间。修改配置时不伪造该时间。外部调度器可能覆盖此文件，需确认是否保留新增字段。

## 必需的 GitHub Secret

在 **Settings → Secrets and variables → Actions** 中保存下列任一名称：

- `WECOM_WEBHOOK`（推荐）
- `WECOM_WEBHOOK_URL`

值应为企业微信群机器人的完整 Webhook 地址；不要提交到仓库或公开日志。

## 设置检查及验证

1. 确认默认分支为 `main`，工作流已启用，Actions 策略允许所用 actions，运行额度可用。
2. 确认工作流的 `contents: write` 权限可用，分支保护允许机器人写回 `data/seen.json`。
3. 手动运行一次，检查抓取、企业微信发送和状态保存步骤，并确认群内收到消息。手动成功不等于定时已验证。
4. 连续观察至少三天的 `schedule` 事件，记录运行时间、结果、群消息送达时间及是否重复。GitHub CLI 可执行：

```bash
gh run list \
  --repo tangshengxuan1203-art/boc-tender-monitor \
  --workflow boc-daily-monitor.yml \
  --event schedule \
  --limit 10 \
  --json databaseId,createdAt,startedAt,status,conclusion,url
```

5. 建议北京时间 11:00 前无成功运行或未收到消息时排查并手动补跑；这个时间只是检查阈值，不是送达保证。需要自动漏跑告警时，应使用独立检查链路。
6. 外部调度器需另行确认启用状态、Asia/Shanghai 10:30 或 UTC 02:30、目标 main 分支、凭证有效期和执行日志。使用 GitHub Actions 的 GITHUB_TOKEN 提交文件通常不会再次触发 push 工作流。

GitHub 定时事件可能延迟，负载高时可能丢弃；只运行默认分支的工作流。公共仓库连续 60 天无活动可能自动停用定时工作流。应持续检查每日运行记录，而非只检查 cron 配置。
