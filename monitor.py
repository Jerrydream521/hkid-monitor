#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
香港身份证 HKID 预约名额自动监控

功能：
1. 查询香港入境处预约配额
2. 监测全部 6 个人事登记办事处
3. 从今天开始监测
4. 固定截止到 2026-09-30
5. 发现“有名额”或“少量名额”时发送 QQ 邮件
6. 同一个名额持续存在时不会重复提醒
7. 名额消失后重新出现，会再次提醒
8. 支持 --test-email 测试邮箱
9. 不自动预约、不填写个人资料、不绕过验证码
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
# 官方页面 / 查询接口
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


# ============================================================
# 时间设置
# ============================================================

# 香港时间 UTC+8
HKT = timezone(timedelta(hours=8))

# 默认截止日期
DEFAULT_END_DATE = "2026-09-30"

# 保存上一轮已有名额
STATE_FILE = Path("state.json")


# ============================================================
# 人事登记办事处
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
# 服务时段
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
# 读取 GitHub Actions 环境变量
# ============================================================

def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# 查询预约配额
# ============================================================

def fetch_quota() -> dict:

    params = urllib.parse.urlencode(
        {
            "svcId": "579",
            "t": str(int(time.time() * 1000)),
        }
    )

    url = f"{API_URL}?{params}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
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
        request,
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
            "香港入境处查询接口返回内容不是有效 JSON，"
            "网页或接口可能发生变化。"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            "香港入境处查询接口返回格式异常。"
        )

    if not isinstance(payload.get("data"), list):
        raise RuntimeError(
            "查询接口结构发生变化：找不到 data[]。"
        )

    return payload


# ============================================================
# 解析截止日期
# ============================================================

def parse_end_date():
    end_date_text = env(
        "END_DATE",
        DEFAULT_END_DATE,
    )

    try:
        return datetime.strptime(
            end_date_text,
            "%Y-%m-%d",
        ).date()

    except ValueError as exc:
        raise RuntimeError(
            "END_DATE 格式错误。"
            "正确格式例如：2026-09-30"
        ) from exc


# ============================================================
# 解析需要监控的办事处
# ============================================================

def parse_office_filter() -> set[str]:

    raw = env("OFFICES", "")

    # 留空 = 全部 6 个办事处
    if not raw:
        return set(OFFICE_NAMES.keys())

    requested = {
        item.strip().upper()
        for item in raw.split(",")
        if item.strip()
    }

    invalid = (
        requested
        - set(OFFICE_NAMES.keys())
    )

    if invalid:
        raise RuntimeError(
            "OFFICES 中存在未知办事处代码："
            + ", ".join(sorted(invalid))
        )

    return requested


# ============================================================
# 提取截止日期之前的可预约名额
# ============================================================

def extract_available(
    payload: dict,
    end_date,
    offices: set[str],
) -> list[dict]:

    today = datetime.now(HKT).date()

    results: list[dict] = []

    for row in payload["data"]:

        try:
            office_id = str(
                row["officeId"]
            ).strip().upper()

            appointment_date = datetime.strptime(
                str(row["date"]).strip(),
                "%m/%d/%Y",
            ).date()

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

        # 只检查指定办事处
        if office_id not in offices:
            continue

        # ====================================================
        # 最重要的日期条件
        #
        # 从今天开始
        # 到 2026-09-30 为止
        #
        # 10 月 1 日及以后完全忽略
        # ====================================================

        if not (
            today
            <= appointment_date
            <= end_date
        ):
            continue

        # R = 一般服务时段
        # K = 延长服务时段
        session_fields = (
            ("R", "quotaR"),
            ("K", "quotaK"),
        )

        for session_key, quota_key in session_fields:

            status = str(
                row.get(
                    quota_key,
                    "",
                )
            ).strip()

            # 只把这两种情况认定为有号：
            #
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
# 读取上一轮已经存在的名额
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
# 保存本轮名额
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
# QQ 邮箱 SMTP 配置
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

    return (
        str(value)
        .replace("\r", "")
        .replace("\n", "")
        .strip()
    )


def clean_email_address(value: str) -> str:

    return "".join(
        str(value).split()
    )


# ============================================================
# 发送 QQ 邮件
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

    smtp_password = "".join(
        str(settings["password"]).split()
    )

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

    if (
        not smtp_user
        or "@" not in smtp_user
    ):
        raise RuntimeError(
            "SMTP_USER 格式不正确。"
        )

    if (
        not to_email
        or "@" not in to_email
    ):
        raise RuntimeError(
            "TO_EMAIL 格式不正确。"
        )

    if not smtp_password:
        raise RuntimeError(
            "SMTP_PASS 为空。"
        )

    # --------------------------------------------------------
    # 构造邮件
    # --------------------------------------------------------

    message = EmailMessage()

    message["Subject"] = subject

    message["From"] = smtp_user

    message["To"] = to_email

    message.set_content(
        body,
        charset="utf-8",
    )

    context = ssl.create_default_context()

    # --------------------------------------------------------
    # QQ 邮箱 SSL
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
                f"不支持的 SMTP_MODE：{smtp_mode}"
            )

    except smtplib.SMTPAuthenticationError as exc:

        raise RuntimeError(
            "QQ SMTP 登录失败。"
            "请检查 QQ 邮箱地址和 SMTP 授权码。"
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
# 构造正式提醒邮件
# ============================================================

def build_alert_body(
    new_slots: list[dict],
    all_slots: list[dict],
    start_date,
    end_date,
    source_update_time: str,
) -> str:

    lines = [
        (
            f"发现 {len(new_slots)} 个新的"
            "香港身份证可预约时段。"
        ),

        "",

        (
            f"监测日期："
            f"{start_date.isoformat()} "
            f"至 {end_date.isoformat()}"
        ),

        "",

        "【新出现的预约名额】",

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
                f"当前监测范围内共有 "
                f"{len(all_slots)} 个"
                "可预约时段。"
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

            "请尽快进入香港入境处官方预约系统：",

            BOOKING_URL,

            "",

            "预约配额预览：",

            QUOTA_PAGE,

            "",

            (
                "说明：本程序只进行预约配额监测"
                "和邮件提醒，不会自动提交预约。"
            ),
        ]
    )

    return "\n".join(lines)


# ============================================================
# 测试 QQ 邮箱
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

            "HKID 预约监控程序"
            "已经可以正常发送邮件提醒。\n\n"

            "正式版本会每 5 分钟左右检查一次。\n\n"

            "监测日期截止到："
            "2026-09-30。\n\n"

            "只有发现新的可预约时段"
            "才会发送提醒。"
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

    today = datetime.now(HKT).date()

    end_date = parse_end_date()

    # --------------------------------------------------------
    # 截止日期已经过去
    # --------------------------------------------------------

    if today > end_date:

        print(
            "监控截止日期已经过去。"
        )

        print(
            f"截止日期：{end_date.isoformat()}"
        )

        print(
            "本轮不再查询预约名额。"
        )

        return

    # --------------------------------------------------------
    # 办事处
    # --------------------------------------------------------

    offices = parse_office_filter()

    print(
        "======================================"
    )

    print(
        "HKID 预约名额自动监控"
    )

    print(
        "======================================"
    )

    print(
        f"香港当前日期：{today.isoformat()}"
    )

    print(
        f"监测截止日期：{end_date.isoformat()}"
    )

    print(
        "监测办事处："
        + "、".join(
            OFFICE_NAMES[office]
            for office in sorted(offices)
        )
    )

    print(
        "正在查询香港入境处预约配额..."
    )

    # --------------------------------------------------------
    # 查询官网
    # --------------------------------------------------------

    payload = fetch_quota()

    # --------------------------------------------------------
    # 提取今天至 9 月 30 日之间的可预约名额
    # --------------------------------------------------------

    slots = extract_available(
        payload,
        end_date,
        offices,
    )

    # --------------------------------------------------------
    # 读取上一轮状态
    # --------------------------------------------------------

    previous_keys = (
        load_previous_keys()
    )

    current_keys = {
        slot["key"]
        for slot in slots
    }

    # --------------------------------------------------------
    # 新出现的名额
    #
    # 当前存在
    # 但上一轮不存在
    # --------------------------------------------------------

    new_keys = (
        current_keys
        - previous_keys
    )

    new_slots = [
        slot
        for slot in slots
        if slot["key"] in new_keys
    ]

    # --------------------------------------------------------
    # 官方数据更新时间
    # --------------------------------------------------------

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
        "--------------------------------------"
    )

    print(
        f"检查完成："
        f"{today.isoformat()} "
        f"至 {end_date.isoformat()}"
    )

    print(
        f"当前可预约：{len(slots)} 格"
    )

    print(
        f"新出现名额：{len(new_slots)} 格"
    )

    if source_update_time:

        print(
            f"官方数据更新时间："
            f"{source_update_time}"
        )

    print(
        "--------------------------------------"
    )

    # --------------------------------------------------------
    # 如果有新的名额
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

        # ----------------------------------------------------
        # DRY RUN
        # ----------------------------------------------------

        if dry_run:

            print(
                "DRY RUN："
                "不发送邮件，"
                "不修改 state.json。"
            )

            return

        # ----------------------------------------------------
        # 生成邮件正文
        # ----------------------------------------------------

        body = build_alert_body(
            new_slots=new_slots,
            all_slots=slots,
            start_date=today,
            end_date=end_date,
            source_update_time=source_update_time,
        )

        # ----------------------------------------------------
        # 找到最早有号日期
        # ----------------------------------------------------

        earliest = min(
            slot["date"]
            for slot in new_slots
        )

        # ----------------------------------------------------
        # 邮件标题
        # ----------------------------------------------------

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
            "本轮没有发现新的预约名额。"
        )

        print(
            "不发送邮件。"
        )

    # --------------------------------------------------------
    # 保存当前状态
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
# 主程序入口
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
            "只测试 QQ 邮箱，"
            "不查询预约名额"
        ),
    )

    parser.add_argument(
        "--dry-run",

        action="store_true",

        help=(
            "只查询预约名额，"
            "不发送邮件，"
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
