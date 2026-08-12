# HKID Appointment Monitor

香港身份证（HKID）预约名额自动监控工具。

本项目用于定时查询香港入境处公开的身份证预约配额信息，在指定日期范围内发现新的可预约时段时，通过 QQ 邮箱发送提醒。

> 本项目仅用于公开预约配额查询和提醒。
> 不自动预约、不提交个人身份资料、不绕过验证码，也不会为用户锁定预约名额。

---

## 1. 当前运行架构

```text
Cloudflare Cron Trigger（每 1 分钟）
        ↓
Cloudflare Worker
        ↓
GitHub REST API
        ↓
workflow_dispatch
        ↓
GitHub Actions
        ↓
monitor.py
        ↓
香港入境处预约配额查询接口
        ↓
筛选指定日期范围和办事处
        ↓
发现新名额
        ↓
QQ 邮箱提醒
```

当前不再使用 GitHub Actions 自带的 `schedule` 定时器。Cloudflare 负责定时触发，GitHub 只负责执行监控程序。

---

## 2. 当前监控配置

- 检查频率：每 1 分钟一次
- 开始日期：当天
- 截止日期：2026-09-30
- 监控办事处：全部 6 个
- 提醒方式：QQ 邮箱
- 只有新名额才发送提醒
- 已经提醒过且持续存在的名额不会重复发送
- 名额消失后重新出现，会再次发送提醒

---

## 3. 监控的办事处

| 代码 | 办事处 |
|---|---|
| RHK | 湾仔 |
| RKO | 长沙湾 |
| RTK | 将军澳 |
| FTO | 火炭 |
| TMO | 屯门 |
| YLO | 元朗 |

如果 `OFFICES` 留空，则全部检查。

---

## 4. 名额状态识别规则

程序当前把以下两种状态视为可预约：

```text
quota-g = 有名额
quota-y = 少量名额
```

其他状态不会触发邮件。

---

## 5. 目录结构

```text
hkid-monitor/
│
├─ .github/
│  └─ workflows/
│     └─ monitor.yml
│
├─ monitor.py
├─ state.json
└─ README.md
```

---

## 6. monitor.py 的作用

`monitor.py` 是核心监控程序，主要负责：

1. 请求香港入境处预约配额数据
2. 获取官方数据更新时间
3. 筛选当天至 2026-09-30 的预约日期
4. 筛选 6 个办事处
5. 判断当前实时可预约时段
6. 与上一轮状态比较
7. 找出新出现的名额
8. 发现新名额时发送 QQ 邮件
9. 在 GitHub Actions 日志中打印实时名额
10. 只有预约状态发生变化时才更新 `state.json`

---

## 7. state.json 的作用

`state.json` 用于记录上一轮已经存在的预约名额，防止重复提醒。

示例：

```text
10:00
火炭 8月20日 有名额
→ 发送邮件

10:01
火炭 8月20日 仍然有名额
→ 不重复发送

10:05
该名额消失
→ 更新状态

10:20
该名额重新出现
→ 再次发送邮件
```

只有预约状态发生变化时才更新 `state.json`，因此不会因为每分钟运行一次而产生大量无意义的 Git commit。

---

## 8. GitHub Actions 配置

工作流文件位置：

```text
.github/workflows/monitor.yml
```

当前只保留：

```yaml
on:
  workflow_dispatch:
```

不使用 `schedule:`，因为定时任务已经交给 Cloudflare。

---

## 9. GitHub Repository Secrets

路径：

```text
GitHub
→ hkid-monitor
→ Settings
→ Secrets and variables
→ Actions
```

需要配置：

```text
SMTP_HOST
SMTP_PORT
SMTP_MODE
SMTP_USER
SMTP_PASS
TO_EMAIL
```

QQ 邮箱推荐配置：

```text
SMTP_HOST = smtp.qq.com
SMTP_PORT = 465
SMTP_MODE = ssl
SMTP_USER = 你的完整QQ邮箱
SMTP_PASS = QQ邮箱SMTP授权码
TO_EMAIL = 接收提醒的邮箱
```

推荐 `SMTP_USER = TO_EMAIL`，即 QQ 邮箱给自己发送提醒。

`SMTP_PASS` 必须填写 QQ 邮箱生成的 SMTP 授权码，不是 QQ 登录密码。

---

## 10. Cloudflare Worker

Cloudflare Worker 当前名称：

```text
hkid-github-trigger
```

它只负责：

```text
Cloudflare Cron
→ 调用 GitHub API
→ 触发 monitor.yml
```

Cloudflare Worker 本身不查询 HKID、不发送 QQ 邮件、不保存预约状态。

---

## 11. Cloudflare Secret

Cloudflare Worker 中需要保存：

```text
GITHUB_TOKEN
```

位置：

```text
Cloudflare
→ Compute
→ Workers & Pages
→ hkid-github-trigger
→ Settings
→ Variables and Secrets
```

类型必须选择 `Secret`。

GitHub Fine-grained Token 建议仅授权：

```text
Repository: hkid-monitor
Actions: Read and write
Metadata: Read-only
```

---

## 12. Cloudflare Cron Trigger

当前 Cron：

```text
* * * * *
```

表示每 1 分钟执行一次。

---

## 13. Cloudflare 日志怎么看

进入：

```text
Cloudflare
→ Observability
```

筛选 Service：

```text
hkid-github-trigger
```

正常情况下，每分钟应看到类似：

```text
Cron triggered: 2026-08-12T02:30:02...
GitHub workflow dispatch: 200 ...
```

其中：

- `Cron triggered`：Cloudflare 定时器正常
- `GitHub workflow dispatch: 200`：GitHub API 调用成功

---

## 14. GitHub Actions 日志怎么看

进入：

```text
GitHub
→ hkid-monitor
→ Actions
→ HKID Appointment Monitor
→ 最新一次运行
→ check
→ Check quota
```

正常日志示例：

```text
======================================
HKID 预约名额自动监控
======================================

香港日期：2026-08-12
截止日期：2026-09-30

正在读取香港入境处预约配额...

======================================
本轮检查结果
======================================

当前可预约时段：0 个
新出现：0 个
已消失：0 个
官方数据更新时间：08/12/2026 10:30:24

======================================
当前实时可预约名额 LIVE
======================================

LIVE | 当前监测范围内没有可预约名额
```

---

## 15. LIVE / NEW / 已消失

### LIVE

表示这一轮查询时，官网当前真实存在的可预约时段。

```text
LIVE | 2026-08-20 | 火炭 | 一般服务时段 | 少量名额
```

### NEW

表示上一轮不存在、本轮首次出现。

```text
NEW | 2026-08-20 | 火炭 | 一般服务时段 | 少量名额
```

出现 `NEW` 后，程序会发送 QQ 邮件。

### 已消失

表示上一轮存在、本轮已经不存在。

```text
已消失：4 个
```

---

## 16. QQ 邮件提醒

检测到新名额后，邮件标题类似：

```text
【HKID有名额】最早 2026-08-20，新增 1 个时段
```

正文会包含：

- 日期
- 办事处
- 服务时段
- 名额状态
- 当前监控范围
- 官方数据更新时间
- 香港入境处预约入口

邮件只在发现 `NEW` 名额时发送。

---

## 17. 如何确认系统真的有效

完整链路需要同时满足以下条件：

### 1）Cloudflare 正常

```text
Cron triggered
GitHub workflow dispatch: 200
```

### 2）GitHub Action 正常

GitHub Actions 显示 `Success`。

### 3）入境处数据正常

GitHub 日志出现：

```text
官方数据更新时间：...
```

说明程序成功获得了入境处返回的数据。

### 4）实时名额识别

如果官网存在名额，GitHub 日志应出现：

```text
LIVE | 日期 | 办事处 | 时段 | 状态
```

### 5）新名额邮件提醒

首次检测到新名额时：

```text
NEW | ...
预约提醒邮件已发送。
```

同时 QQ 邮箱应收到正式提醒。

---

## 18. 常见问题排查

### GITHUB_TOKEN secret is missing

检查：

```text
Settings
→ Variables and Secrets
→ GITHUB_TOKEN
```

确认：

```text
Type = Secret
Environment = Production
```

### GitHub workflow dispatch: 401

一般表示 Token 无效、过期或已撤销。

### GitHub workflow dispatch: 403

一般表示 Token 权限不足。确认：

```text
Actions = Read and write
```

### GitHub workflow dispatch: 404

检查：

```text
GITHUB_OWNER
GITHUB_REPO
WORKFLOW_FILE
GITHUB_BRANCH
```

当前应对应：

```text
Owner: Jerrydream521
Repo: hkid-monitor
Workflow: monitor.yml
Branch: main
```

### QQ SMTP 登录失败

检查：

```text
SMTP_HOST = smtp.qq.com
SMTP_PORT = 465
SMTP_MODE = ssl
SMTP_USER = 完整QQ邮箱
SMTP_PASS = QQ邮箱SMTP授权码
```

### 收不到邮件

先检查 GitHub 日志有没有 `NEW`。如果只有 `LIVE` 没有 `NEW`，说明这个名额已经提醒过，不会重复发送。

---

## 19. 官方数据更新时间

日志中的：

```text
官方数据更新时间：08/12/2026 09:53:24
```

是香港入境处数据源返回的更新时间，不是 Cloudflare 运行时间、GitHub Action 运行时间或邮件发送时间。

例如：

```text
程序查询时间：10:00
官方数据更新时间：09:53:24
```

表示程序在 10:00 查询时，入境处最新提供的是 09:53:24 这一版配额数据。

---

## 20. 关于预约网站排队

监控程序发现名额并不等于已经锁定名额。

实际流程：

```text
发现名额
→ 邮件提醒
→ 用户进入香港入境处预约系统
→ 排队
→ 进入预约页面
→ 自行完成预约
```

排队期间名额仍可能被其他人预约。

---

## 21. 安全和隐私

以下信息绝对不要写进公开 GitHub 文件：

```text
QQ邮箱SMTP授权码
GitHub Personal Access Token
密码
香港身份证号码
护照号码
手机号
其他个人敏感信息
```

GitHub SMTP 信息应放在 `GitHub Repository Secrets`。

GitHub PAT 应放在 `Cloudflare Worker Secret`。

不要直接写在：

```text
monitor.py
monitor.yml
README.md
Cloudflare Worker源码
```

---

## 22. 停止日期

当前监控截止日期：

```text
2026-09-30
```

`monitor.py` 会忽略 2026-10-01 及以后的预约日期。

Cloudflare Worker 也应在香港时间 2026-09-30 结束后停止继续触发 GitHub。

---

## 23. 修改监控截止日期

GitHub：

```text
.github/workflows/monitor.yml
```

修改：

```yaml
END_DATE: "2026-09-30"
```

如果修改截止日期，也建议同步修改 Cloudflare Worker 中的停止时间。

---

## 24. 手动测试

GitHub 仍保留 `Run workflow`，可以手动测试：

```text
GitHub
→ Actions
→ HKID Appointment Monitor
→ Run workflow
```

手动运行不会影响 Cloudflare 的自动触发。

---

## 25. 项目原则

本项目只负责：

```text
查询
→ 判断
→ 提醒
```

不会执行：

```text
自动抢号
自动提交预约
绕过排队
绕过验证码
批量占号
提交个人身份信息
```

---

## 26. 当前最终配置摘要

```text
Cloudflare Worker:
hkid-github-trigger

Cloudflare Cron:
* * * * *
= 每 1 分钟

GitHub Repository:
Jerrydream521/hkid-monitor

GitHub Workflow:
.github/workflows/monitor.yml

Workflow Trigger:
workflow_dispatch

Python:
monitor.py

监控日期:
当天 → 2026-09-30

办事处:
湾仔
长沙湾
将军澳
火炭
屯门
元朗

提醒:
QQ SMTP

状态日志:
LIVE
NEW
已消失

预约状态变化:
才更新 state.json

新名额:
才发送邮件
```

---

## 27. 使用提醒

即使程序每分钟检查一次，也无法保证获得预约名额。

原因包括：

- 官方数据本身可能不是实时逐秒更新
- 临时取消号可能很快被其他用户预约
- 正式预约网站可能存在排队
- 邮件推送存在少量延迟
- GitHub Action 启动也可能存在短暂排队

本项目的作用是尽可能减少人工反复查询，并在公开预约配额出现变化时尽快提醒。
