#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
香港身份证 HKID 预约名额自动监控

功能：
1. 查询香港入境处预约配额数据
2. 默认检查 6 个人事登记办事处
3. 默认只检查未来 30 天
4. 发现“有名额”或“少量名额”时发送邮件
5. 同一个名额持续存在时不会反复提醒
6. 名额消失后再次出现，会再次提醒
7. 支持 --test-email 测试邮件
8. 不自动预约、不填写个人资料、不绕过验证码
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path


# ============================================================
# 基础配置
# ============================================================

API_URL = (
    "https://eservices.es2.immd.gov.hk/"
    "surgecontrolgate/ticket/getSituation"
)

QUOTA_PAGE = (
    "https://eservices.es2.immd.gov.hk/"
    "es/quota-enquiry-client/?l=zh-CN&appId=579"
)

BOOKING_URL = "https://www.gov.hk/icbooking"

# 香港时间 UTC+8
HKT = timezone(timedelta(hours=8))

# 用于记录上一轮已经存在的名额
STATE_FILE = Path("state.json")


# ============================================================
# 办事处
# ============================================================

OFFICE_NAMES = {
    "RHK": "湾仔",
    "RKO": "长沙湾",
    "RTK": "将军澳",
    "FTO": "火炭",
    "TMO": "屯门",
    "YLO": "元朗",
}


# ============================================================
# 时段
# ============================================================

SESSION_NAMES = {
    "R": "一般服务时段",
    "K": "延长服务时段",
}


# ============================================================
# 配额状态
# ============================================================

STATUS_NAMES = {
    "quota-g": "有名额",
    "quota-y": "少量名额",
}

AVAILABLE_STATUSES = set(STATUS_NAMES.keys())


# ============================================================
# 环境变量
# ============================================================

def env(name: str, default: str = "") -> str:
    """
    读取 GitHub Actions 环境变量。
    去掉首尾空格、回车和换行。
    """
    value = os.environ.get(name, default)

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# 查询香港入境处预约配额
# ============================================================

def fetch_quota() -> dict:
    """
    查询预约配额数据。
    """

    params = urllib.parse.urlencode(
        {
            "svcId": "579",
            "t": str(int(time.time() * 1000)),
        }
    )

    url = f"{API_URL}?{params}"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Referer": QUOTA_PAGE,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )

    context = ssl.create_default_context()

    with urllib.request.urlopen(
        req,
        timeout=25,
        context=context,
    ) as response:

        if response.status != 200:
            raise RuntimeError(
                f"香港入境处查询接口返回 HTTP {response.status}"
            )

        raw = response.read().decode("utf-8")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "查询接口返回的内容不是有效 JSON，"
            "香港入境处网页结构可能发生了变化。"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            "查询接口响应格式异常：不是 JSON 对象。"
        )

    if not isinstance(payload.get("data"), list):
        raise RuntimeError(
            "查询接口响应结构发生变化：找不到 data[]。"
        )

    return payload


# ============================================================
# 办事处筛选
# ============================================================

def parse_office_filter() -> set[str]:
    """
    OFFICES 留空：
        检查全部 6 个办事处

    例如：
        OFFICES=RHK,RKO

    表示只检查：
        湾仔 + 长沙湾
    """

    raw = env("OFFICES", "")

    if not raw:
        return set(OFFICE_NAMES.keys())

    requested = {
        item.strip().upper()
        for item in raw.split(",")
        if item.strip()
    }

    invalid = requested - set(OFFICE_NAMES.keys())

    if invalid:
        raise RuntimeError(
            "OFFICES 中存在未知办事处代码："
            + ", ".join(sorted(invalid))
            + "。可用代码："
            + ", ".join(OFFICE_NAMES.keys())
        )

    return requested


# ============================================================
# 从返回数据中提取可预约时段
# ============================================================

def extract_available(
    payload: dict,
    window_days: int,
    offices: set[str],
) -> list[dict]:

    today = datetime.now(HKT).date()

    # 例如 WINDOW_DAYS=30
    # 就检查今天到未来 30 天
    end_date = today + timedelta(days=window_days)

    results: list[dict] = []

    for row in payload["data"]:

        try:
            office_id = str(
                row["officeId"]
            ).strip().upper()

            date_value = str(
                row["date"]
            ).strip()

            appointment_date = datetime.strptime(
                date_value,
                "%m/%d/%Y",
            ).date()

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

        # 不属于需要监控的办事处
        if office_id not in offices:
            continue

        # 不属于未来 30 天
        if not (
            today
            <= appointment_date
            <= end_date
        ):
            continue

        # R = 一般时段
        # K = 延长服务时段
        session_fields = (
            ("R", "quotaR"),
            ("K", "quotaK"),
        )

        for session_key, quota_key in session_fields:

            status = str(
                row.get(quota_key, "")
            ).strip()

            # 只处理：
            # quota-g = 有名额
            # quota-y = 少量名额
            if status not in AVAILABLE_STATUSES:
                continue

            result = {
                "key": (
                    f"{office_id}|"
                    f"{appointment_date.isoformat()}|"
                    f"{session_key}"
                ),
                "office_id": office_id,
                "office": OFFICE_NAMES.get(
                    office_id,
                    office_id,
                ),
                "date": appointment_date.isoformat(),
                "session": SESSION_NAMES.get(
                    session_key,
                    session_key,
                ),
                "status": STATUS_NAMES.get(
                    status,
                    status,
                ),
                "status_raw": status,
            }

            results.append(result)

    results.sort(
        key=lambda item: (
            item["date"],
            item["office_id"],
            item["session"],
        )
    )

    return results


# ============================================================
# 读取上一轮状态
# ============================================================

def load_previous_keys() -> set[str]:

    if not STATE_FILE.exists():
        return set()

    try:

        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        keys = data.get(
            "available_keys",
            [],
        )

        if not isinstance(keys, list):
            return set()

        return set(keys)

    except (
        ValueError,
        TypeError,
        OSError,
    ):
        return set()


# ============================================================
# 保存当前状态
# ============================================================

def save_state(
    current_keys: set[str],
    source_update_time: str,
) -> None:

    data = {
        "available_keys": sorted(
            current_keys
        ),
        "source_update_time": (
            source_update_time
        ),
        "checked_at_hkt": (
            datetime.now(HKT)
            .isoformat(timespec="seconds")
        ),
    }

    STATE_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# SMTP 邮箱设置
# ============================================================

def smtp_settings() -> dict:

    port_text = env(
        "SMTP_PORT",
        "465",
    )

    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError(
            "SMTP_PORT 必须是数字，例如 465。"
        ) from exc

    settings = {
        "host": env(
            "SMTP_HOST"
        ),
        "port": port,
        "mode": env(
            "SMTP_MODE",
            "ssl",
        ).lower(),
        "user": env(
            "SMTP_USER"
        ),
        "password": env(
            "SMTP_PASS"
        ),
        "to": env(
            "TO_EMAIL"
        ),
    }

    missing = []

    for key in (
        "host",
        "user",
        "password",
        "to",
    ):
        if not settings[key]:
            missing.append(key)

    if missing:
        raise RuntimeError(
            "缺少邮件配置："
            + ", ".join(missing)
            + "。请检查 GitHub Repository Secrets。"
        )

    if settings["mode"] not in {
        "ssl",
        "starttls",
    }:
        raise RuntimeError(
            "SMTP_MODE 只能填写 ssl 或 starttls。"
        )

    return settings


# ============================================================
# 清理邮件 Header
# ============================================================

def clean_header(value: str) -> str:
    """
    Email Header 中不能出现回车和换行。
    """

    value = str(value)

    value = value.replace(
        "\r",
        "",
    )

    value = value.replace(
        "\n",
        "",
    )

    return value.strip()


def clean_email_address(value: str) -> str:
    """
    清理邮箱地址。

    删除：
    - 空格
    - Tab
    - 回车
    - 换行
    """

    return "".join(
        str(value).split()
    )


# ============================================================
# 发送邮件
# ============================================================

def send_email(
    subject: str,
    body: str,
) -> None:

    settings = smtp_settings()

    smtp_host = clean_header(
        settings["host"]
    )

    smtp_port = settings["port"]

    smtp_mode = clean_header(
        settings["mode"]
    ).lower()

    smtp_user = clean_email_address(
        settings["user"]
    )

    # 授权码只清理首尾空格和换行
    smtp_password = str(
        settings["password"]
    ).strip()

    to_email = clean_email_address(
        settings["to"]
    )

    subject = clean_header(
        subject
    )

    # --------------------------------------------------------
    # 基础检查
    # --------------------------------------------------------

    if not smtp_host:
        raise RuntimeError(
            "SMTP_HOST 为空。"
        )

    if not smtp_user:
        raise RuntimeError(
            "SMTP_USER 为空。"
        )

    if "@" not in smtp_user:
        raise RuntimeError(
            "SMTP_USER 格式不正确，"
            "应该填写完整邮箱，例如 123456@qq.com。"
        )

    if not to_email:
        raise RuntimeError(
            "TO_EMAIL 为空。"
        )

    if "@" not in to_email:
        raise RuntimeError(
            "TO_EMAIL 格式不正确，"
            "应该填写完整邮箱地址。"
        )

    if not smtp_password:
        raise RuntimeError(
            "SMTP_PASS 为空。"
        )

    # --------------------------------------------------------
    # 创建邮件
    # --------------------------------------------------------

    message = EmailMessage()

    message["Subject"] = subject

    # 为避免 Header 编码问题，
    # 发件人直接使用邮箱地址，不增加中文昵称
    message["From"] = smtp_user

    message["To"] = to_email

    message.set_content(
        body,
        charset="utf-8",
    )

    context = ssl.create_default_context()

    # --------------------------------------------------------
    # SSL SMTP
    # QQ 邮箱通常使用 smtp.qq.com + 465 + ssl
    # --------------------------------------------------------

    try:

        if smtp_mode == "ssl":

            with smtplib.SMTP_SSL(
                smtp_host,
                smtp_port,
                context=context,
                timeout=30,
            ) as smtp:

                smtp.login(
                    smtp_user,
                    smtp_password,
                )

                smtp.send_message(
                    message
                )

        # ----------------------------------------------------
        # STARTTLS
        # ----------------------------------------------------

        elif smtp_mode == "starttls":

            with smtplib.SMTP(
                smtp_host,
                smtp_port,
                timeout=30,
            ) as smtp:

                smtp.ehlo()

                smtp.starttls(
                    context=context
                )

                smtp.ehlo()

                smtp.login(
                    smtp_user,
                    smtp_password,
                )

                smtp.send_message(
                    message
                )

        else:

            raise RuntimeError(
                f"不支持的 SMTP_MODE："
                f"{smtp_mode}"
            )

    except smtplib.SMTPAuthenticationError as exc:

        raise RuntimeError(
            "QQ SMTP 登录失败。"
            "请检查 SMTP_USER 是否为完整 QQ 邮箱，"
            "以及 SMTP_PASS 是否填写的是 QQ 邮箱授权码，"
            "而不是 QQ 登录密码。"
            f" SMTP状态码：{exc.smtp_code}"
        ) from exc

    except smtplib.SMTPRecipientsRefused as exc:

        raise RuntimeError(
            "收件邮箱被服务器拒绝，"
            "请检查 TO_EMAIL。"
        ) from exc

    except smtplib.SMTPSenderRefused as exc:

        raise RuntimeError(
            "发件邮箱被服务器拒绝，"
            "请检查 SMTP_USER。"
        ) from exc

    except smtplib.SMTPException as exc:

        raise RuntimeError(
            f"SMTP 邮件发送失败："
            f"{type(exc).__name__}: {exc}"
        ) from exc


# ============================================================
# 生成发现新名额后的邮件正文
# ============================================================

def build_alert_body(
    new_slots: list[dict],
    all_slots: list[dict],
    window_days: int,
    source_update_time: str,
) -> str:

    lines = [
        (
            f"发现 {len(new_slots)} 个新的"
            f"香港身份证可预约时段。"
        ),
        (
            f"监测范围：从今天起未来 "
            f"{window_days} 天。"
        ),
        "",
        "【新出现的名额】",
        "",
    ]

    for slot in new_slots:

        lines.append(
            f'{slot["date"]}'
            f' ｜ {slot["office"]}'
            f' ｜ {slot["session"]}'
            f' ｜ {slot["status"]}'
        )

    lines.extend(
        [
            "",
            (
                f"当前监测窗口内共有 "
                f"{len(all_slots)} 个可预约时段。"
            ),
            "",
            (
                "官方数据更新时间："
                + (
                    source_update_time
                    if source_update_time
                    else "未提供"
                )
            ),
            "",
            "请尽快进入香港入境处官方预约系统确认：",
            BOOKING_URL,
            "",
            "预约配额预览：",
            QUOTA_PAGE,
            "",
            (
                "说明：本程序仅提供预约配额监测和邮件提醒，"
                "不会自动提交预约。"
            ),
        ]
    )

    return "\n".join(lines)


# ============================================================
# 测试邮件
# ============================================================

def test_email() -> None:

    print(
        "正在测试 QQ 邮箱 SMTP..."
    )

    send_email(
        subject=(
            "【测试成功】"
            "HKID预约监控邮件"
        ),
        body=(
            "如果你收到这封邮件，"
            "说明 QQ 邮箱 SMTP 配置正确。\n\n"
            "HKID 预约监控程序已经可以正常发送邮件提醒。\n\n"
            "正式运行后，程序会定期检查未来 30 天的"
            "香港身份证预约名额。\n\n"
            "只有发现新的可预约时段时才会发送提醒。"
        ),
    )

    print(
        "测试邮件已发送。"
    )


# ============================================================
# 正式监控
# ============================================================

def run(
    dry_run: bool = False,
) -> None:

    window_days_text = env(
        "WINDOW_DAYS",
        "30",
    )

    try:
        window_days = int(
            window_days_text
        )
    except ValueError as exc:
        raise RuntimeError(
            "WINDOW_DAYS 必须是数字。"
        ) from exc

    if (
        window_days < 1
        or window_days > 120
    ):
        raise RuntimeError(
            "WINDOW_DAYS 应在 1～120 之间。"
        )

    offices = parse_office_filter()

    print(
        "正在查询香港入境处预约配额..."
    )

    print(
        f"监测范围：未来 {window_days} 天"
    )

    print(
        "监测办事处："
        + "、".join(
            OFFICE_NAMES[office]
            for office in sorted(offices)
        )
    )

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------

    payload = fetch_quota()

    slots = extract_available(
        payload,
        window_days,
        offices,
    )

    # --------------------------------------------------------
    # 读取之前已有名额
    # --------------------------------------------------------

    previous_keys = (
        load_previous_keys()
    )

    current_keys = {
        slot["key"]
        for slot in slots
    }

    # 当前存在，但上一次不存在
    # = 新出现的名额
    new_keys = (
        current_keys
        - previous_keys
    )

    new_slots = [
        slot
        for slot in slots
        if slot["key"] in new_keys
    ]

    source_update_time = str(
        payload.get(
            "lastUpdateTime",
            "",
        )
    ).strip()

    # --------------------------------------------------------
    # 日志
    # --------------------------------------------------------

    print(
        f"检查完成：未来 {window_days} 天，"
        f"当前可预约 {len(slots)} 格，"
        f"新出现 {len(new_slots)} 格。"
    )

    # --------------------------------------------------------
    # 有新名额
    # --------------------------------------------------------

    if new_slots:

        print(
            "发现新的预约名额："
        )

        for slot in new_slots:

            print(
                "NEW | "
                f'{slot["date"]} | '
                f'{slot["office"]} | '
                f'{slot["session"]} | '
                f'{slot["status"]}'
            )

        # dry-run 模式：
        # 只查询，不发邮件、不写状态
        if dry_run:

            print(
                "DRY RUN："
                "不发送邮件，"
                "不修改 state.json。"
            )

            return

        # ----------------------------------------------------
        # 生成提醒邮件
        # ----------------------------------------------------

        body = build_alert_body(
            new_slots=new_slots,
            all_slots=slots,
            window_days=window_days,
            source_update_time=source_update_time,
        )

        earliest = min(
            slot["date"]
            for slot in new_slots
        )

        subject = (
            f"【HKID有名额】"
            f"最早 {earliest}，"
            f"新增 {len(new_slots)} 个时段"
        )

        # ----------------------------------------------------
        # 发邮件
        # ----------------------------------------------------

        send_email(
            subject=subject,
            body=body,
        )

        print(
            "预约提醒邮件已发送。"
        )

    else:

        print(
            "本轮没有发现新的预约名额，"
            "不发送邮件。"
        )

    # --------------------------------------------------------
    # 保存当前状态
    #
    # 即使当前 0 个名额也要保存。
    #
    # 例如：
    #
    # 第一次：
    # 8月20日 湾仔 有名额
    #
    # 下一次：
    # 名额消失
    #
    # 再下一次：
    # 8月20日 湾仔重新有名额
    #
    # 程序就能再次提醒。
    # --------------------------------------------------------

    if not dry_run:

        save_state(
            current_keys,
            source_update_time,
        )

        print(
            "state.json 已更新。"
        )


# ============================================================
# 主程序
# ============================================================

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "香港身份证 HKID "
            "预约名额自动监控"
        )
    )

    parser.add_argument(
        "--test-email",
        action="store_true",
        help=(
            "只发送测试邮件，"
            "不查询预约名额"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "只查询预约名额，"
            "不发邮件，"
            "不修改 state.json"
        ),
    )

    args = parser.parse_args()

    try:

        if args.test_email:

            test_email()

        else:

            run(
                dry_run=args.dry_run
            )

        return 0

    except Exception as exc:

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
