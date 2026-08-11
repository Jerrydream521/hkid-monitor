#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
HKID appointment quota monitor
- Reads the public quota preview endpoint used by the Hong Kong Immigration Department page.
- Checks all 6 ROP offices for availability within the next N calendar days.
- Sends email only when an available cell appears that was not available in the previous run.
- Does NOT book, reserve, submit personal information, or bypass CAPTCHA.
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

API_URL = "https://eservices.es2.immd.gov.hk/surgecontrolgate/ticket/getSituation"
QUOTA_PAGE = "https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=zh-CN&appId=579"
BOOKING_URL = "https://www.gov.hk/icbooking"

HKT = timezone(timedelta(hours=8))
STATE_FILE = Path("state.json")

OFFICE_NAMES = {
    "RHK": "湾仔",
    "RKO": "长沙湾",
    "RTK": "将军澳",
    "FTO": "火炭",
    "TMO": "屯门",
    "YLO": "元朗",
}

SESSION_NAMES = {
    "R": "一般服务时段",
    "K": "延长服务时段",
}

STATUS_NAMES = {
    "quota-g": "有名额",
    "quota-y": "少量名额",
}

AVAILABLE_STATUSES = set(STATUS_NAMES)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def fetch_quota() -> dict:
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
                "AppleWebKit/537.36 Chrome/124 Safari/537.36 "
                "HKID-Quota-Monitor/1.0"
            ),
            "Referer": QUOTA_PAGE,
            "Accept": "application/json,text/plain,*/*",
        },
    )

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
        if resp.status != 200:
            raise RuntimeError(f"查询接口返回 HTTP {resp.status}")
        payload = json.loads(resp.read().decode("utf-8"))

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise RuntimeError("查询接口响应结构发生变化：找不到 data[]")

    return payload


def parse_office_filter() -> set[str]:
    raw = env("OFFICES", "")
    if not raw:
        return set(OFFICE_NAMES)

    requested = {x.strip().upper() for x in raw.split(",") if x.strip()}
    invalid = requested - set(OFFICE_NAMES)
    if invalid:
        raise RuntimeError(
            "OFFICES 中存在未知办事处代码："
            + ", ".join(sorted(invalid))
            + "。可用："
            + ", ".join(OFFICE_NAMES)
        )
    return requested


def extract_available(payload: dict, window_days: int, offices: set[str]) -> list[dict]:
    today = datetime.now(HKT).date()
    end_date = today + timedelta(days=window_days)

    results: list[dict] = []
    for row in payload["data"]:
        try:
            office_id = str(row["officeId"]).strip().upper()
            d = datetime.strptime(str(row["date"]), "%m/%d/%Y").date()
        except (KeyError, TypeError, ValueError):
            continue

        if office_id not in offices:
            continue
        if not (today <= d <= end_date):
            continue

        for session_key, quota_key in (("R", "quotaR"), ("K", "quotaK")):
            status = str(row.get(quota_key, "")).strip()
            if status in AVAILABLE_STATUSES:
                results.append(
                    {
                        "key": f"{office_id}|{d.isoformat()}|{session_key}",
                        "office_id": office_id,
                        "office": OFFICE_NAMES.get(office_id, office_id),
                        "date": d.isoformat(),
                        "session": SESSION_NAMES[session_key],
                        "status": STATUS_NAMES[status],
                        "status_raw": status,
                    }
                )

    results.sort(key=lambda x: (x["date"], x["office_id"], x["session"]))
    return results


def load_previous_keys() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("available_keys", []))
    except (ValueError, TypeError, OSError):
        return set()


def save_state(current_keys: set[str], source_update_time: str) -> None:
    data = {
        "available_keys": sorted(current_keys),
        "source_update_time": source_update_time,
        "checked_at_hkt": datetime.now(HKT).isoformat(timespec="seconds"),
    }
    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def smtp_settings() -> dict:
    settings = {
        "host": env("SMTP_HOST"),
        "port": int(env("SMTP_PORT", "465")),
        "mode": env("SMTP_MODE", "ssl").lower(),
        "user": env("SMTP_USER"),
        "password": env("SMTP_PASS"),
        "to": env("TO_EMAIL"),
        "from_name": env("FROM_NAME", "HKID预约监控"),
    }

    missing = [
        k for k in ("host", "user", "password", "to")
        if not settings[k]
    ]
    if missing:
        raise RuntimeError(
            "缺少邮件配置：" + ", ".join(missing)
            + "。请在 GitHub Repository Secrets 中填写对应值。"
        )

    if settings["mode"] not in {"ssl", "starttls"}:
        raise RuntimeError("SMTP_MODE 只能填写 ssl 或 starttls")

    return settings


def send_email(subject: str, body: str) -> None:
    s = smtp_settings()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f'{s["from_name"]} <{s["user"]}>'
    msg["To"] = s["to"]
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if s["mode"] == "ssl":
        with smtplib.SMTP_SSL(s["host"], s["port"], context=ctx, timeout=25) as smtp:
            smtp.login(s["user"], s["password"])
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(s["host"], s["port"], timeout=25) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
            smtp.login(s["user"], s["password"])
            smtp.send_message(msg)


def build_alert_body(
    new_slots: list[dict],
    all_slots: list[dict],
    window_days: int,
    source_update_time: str,
) -> str:
    lines = [
        f"发现 {len(new_slots)} 个新的香港身份证可预约时段。",
        f"监测范围：从今天起未来 {window_days} 天；所有已选择的人事登记办事处。",
        "",
        "【新出现的名额】",
    ]

    for s in new_slots:
        lines.append(
            f'- {s["date"]}｜{s["office"]}｜{s["session"]}｜{s["status"]}'
        )

    lines += [
        "",
        f"当前监测窗口内总共有 {len(all_slots)} 个可预约格。",
        f"官方数据更新时间：{source_update_time or '未提供'}",
        "",
        "请尽快打开香港入境处官方预约系统确认并自行预约：",
        BOOKING_URL,
        "",
        "配额预览页：",
        QUOTA_PAGE,
        "",
        "说明：本程序只做公开配额查询和邮件提醒，不会代你提交预约。",
    ]
    return "\n".join(lines)


def test_email() -> None:
    send_email(
        "【测试成功】HKID预约监控邮件",
        (
            "如果你收到这封邮件，说明 SMTP 邮件提醒配置正确。\n\n"
            "接下来 GitHub Actions 会按设定周期自动检查未来 30 天的预约名额。"
        ),
    )
    print("测试邮件已发送。")


def run(dry_run: bool = False) -> None:
    window_days = int(env("WINDOW_DAYS", "30"))
    if window_days < 1 or window_days > 120:
        raise RuntimeError("WINDOW_DAYS 应在 1~120 之间")

    offices = parse_office_filter()
    payload = fetch_quota()
    slots = extract_available(payload, window_days, offices)

    previous = load_previous_keys()
    current = {s["key"] for s in slots}
    new_keys = current - previous
    new_slots = [s for s in slots if s["key"] in new_keys]

    source_update_time = str(payload.get("lastUpdateTime", "")).strip()

    print(
        f"检查完成：未来 {window_days} 天，"
        f"当前可预约 {len(slots)} 格，新出现 {len(new_slots)} 格。"
    )

    if new_slots:
        for s in new_slots:
            print(
                f'NEW {s["date"]} {s["office"]} '
                f'{s["session"]} {s["status"]}'
            )

        if dry_run:
            print("DRY RUN：未发送邮件，也未写入 state.json。")
            return

        body = build_alert_body(
            new_slots=new_slots,
            all_slots=slots,
            window_days=window_days,
            source_update_time=source_update_time,
        )
        earliest = min(s["date"] for s in new_slots)
        send_email(
            f"【HKID有名额】最早 {earliest}，新增 {len(new_slots)} 个时段",
            body,
        )
        print("提醒邮件已发送。")

    if not dry_run:
        # 即使没有新增名额，也要保存“当前仍开放的格子”。
        # 这样某个名额消失后再重新出现时，能够再次提醒。
        save_state(current, source_update_time)


def main() -> int:
    parser = argparse.ArgumentParser(description="香港身份证预约名额监控")
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="只发送一封测试邮件，不查询预约名额",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="查询并打印结果，但不发邮件、不更新状态",
    )
    args = parser.parse_args()

    try:
        if args.test_email:
            test_email()
        else:
            run(dry_run=args.dry_run)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
