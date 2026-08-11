# 香港身份证（HKID）预约名额自动监控

这是一个给软件小白使用的简化版小程序。

## 它会做什么

- 每 10 分钟自动检查一次香港入境处公开的预约配额查询接口。
- 默认检查 6 个办事处：
  - 湾仔 RHK
  - 长沙湾 RKO
  - 将军澳 RTK
  - 火炭 FTO
  - 屯门 TMO
  - 元朗 YLO
- 只看“今天起未来 30 天”。
- `quota-g`（有名额）和 `quota-y`（少量名额）都视为可预约。
- 有新的可预约格子时发送邮件。
- 同一个格子持续开放时不会每 10 分钟重复发邮件。
- 如果某个格子先满了、之后又重新开放，会再次提醒。
- **不会自动预约，不会填写个人资料，不会绕过验证码。**

---

# 推荐使用方式：GitHub Actions

优点：你的电脑关机也能继续监控。

## 第 1 步：准备 GitHub 账号

打开 GitHub 并注册/登录。

## 第 2 步：新建仓库

1. GitHub 右上角点 `+`
2. 选择 `New repository`
3. Repository name 可填：`hkid-monitor`
4. 建议选择 `Private`
5. 点击 `Create repository`

## 第 3 步：把这些文件上传到仓库

解压本压缩包后，把里面的文件和文件夹上传到仓库根目录。

最终仓库里应看到：

```text
.github/
  workflows/
    monitor.yml
.gitignore
monitor.py
state.json
README.md
```

最重要的是 `.github/workflows/monitor.yml` 必须保留这个路径。

## 第 4 步：设置邮件参数

进入你的 GitHub 仓库：

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

依次新建下面 6 个 Secret：

### 必填

`SMTP_HOST`
- 发件邮箱的 SMTP 服务器地址
- 例如 QQ 邮箱常见写法：`smtp.qq.com`

`SMTP_PORT`
- SSL 常用：`465`

`SMTP_MODE`
- SSL 填：`ssl`
- 如果你的邮箱要求 STARTTLS，则填：`starttls`

`SMTP_USER`
- 发件邮箱完整地址
- 例如：`yourname@qq.com`

`SMTP_PASS`
- **这里不要填普通登录密码**
- 填邮箱服务商提供的 SMTP 授权码 / App Password

`TO_EMAIL`
- 接收提醒的邮箱
- 可以和发件邮箱相同，也可以不同

> 不同邮箱服务商的 SMTP 地址、端口和授权方式可能不同，
> 请以你所使用邮箱的官方帮助文档为准。

## 第 5 步：先测试

进入仓库：

`Actions` → 左侧点 `HKID Appointment Monitor` → `Run workflow`

运行后，点进去看日志。

第一次运行如果未来 30 天已经有可预约格子，会直接给你发提醒邮件。

## 第 6 步：确认自动运行

`.github/workflows/monitor.yml` 已设置：

```yaml
- cron: "*/10 * * * *"
```

即大约每 10 分钟检查一次。

GitHub 的定时任务可能偶尔有排队延迟，所以它不是“精确到秒”的监控。

---

# 如何只监控指定办事处

打开：

`.github/workflows/monitor.yml`

找到：

```yaml
OFFICES: ""
```

留空表示全部 6 个办事处。

例如只看湾仔 + 长沙湾：

```yaml
OFFICES: "RHK,RKO"
```

可用代码：

- `RHK` 湾仔
- `RKO` 长沙湾
- `RTK` 将军澳
- `FTO` 火炭
- `TMO` 屯门
- `YLO` 元朗

---

# 如何把“一个月”改成别的天数

打开：

`.github/workflows/monitor.yml`

找到：

```yaml
WINDOW_DAYS: "30"
```

例如想监测未来 21 天：

```yaml
WINDOW_DAYS: "21"
```

---

# 如何改检查频率

打开：

`.github/workflows/monitor.yml`

当前是：

```yaml
- cron: "*/10 * * * *"
```

如果改成每 5 分钟：

```yaml
- cron: "*/5 * * * *"
```

不建议做秒级高频请求。

---

# 测试邮件功能

如果你在自己电脑上装有 Python，可以运行：

```bash
python monitor.py --test-email
```

但本地运行时，需要先把 SMTP 配置写成环境变量。

对软件小白来说，直接通过 GitHub Actions 测试更简单。

---

# 重要说明

1. 本程序只读取公开预约配额，不代抢、不代约。
2. 收到邮件后，仍需由你本人进入香港入境处官方预约系统确认并完成预约。
3. 香港入境处页面或接口未来可能调整；如果接口字段改变，需要同步修改程序。
4. 请不要把 SMTP 授权码直接写进 `monitor.py`、`README.md` 或公开仓库。
5. 建议把 GitHub 仓库设为 Private。

官方预约入口：

`https://www.gov.hk/icbooking`

配额预览页：

`https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=zh-CN&appId=579`
