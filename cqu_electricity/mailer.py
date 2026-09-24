from __future__ import annotations

import base64
import html
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from .config import Settings
from .models import MeterReading


class EmailError(RuntimeError):
    """SMTP 配置或邮件发送失败。"""


def validate_email_settings(settings: Settings) -> None:
    missing: list[str] = []
    if not settings.smtp_host:
        missing.append("SMTP_HOST")
    if not settings.smtp_from:
        missing.append("SMTP_FROM")
    if not settings.smtp_to:
        missing.append("SMTP_TO")
    if missing:
        raise EmailError(f"缺少邮件配置：{', '.join(missing)}")
    if settings.smtp_use_ssl and settings.smtp_starttls:
        raise EmailError("SMTP_USE_SSL 和 SMTP_STARTTLS 不能同时为 true")
    if bool(settings.smtp_username) != bool(settings.smtp_password):
        raise EmailError("SMTP_USERNAME 和 SMTP_PASSWORD 必须同时填写或同时留空")


def _message(settings: Settings, reading: MeterReading, chart_path: Path) -> EmailMessage:
    if not chart_path.exists():
        raise EmailError(f"找不到待发送图表：{chart_path}")
    image_base64 = base64.b64encode(chart_path.read_bytes()).decode("ascii")

    def safe(value: object | None) -> str:
        return html.escape("—" if value in (None, "") else str(value))

    total_balance = f"{reading.total_balance_yuan(settings.electricity_price):.2f}"
    subject = f"[{settings.email_subject_prefix}] {reading.room} 余额 {total_balance} 元"
    captured_at = reading.captured_at.strftime("%Y-%m-%d %H:%M:%S %Z")
    cash_label = "现金余额" if reading.subsidy_balance_yuan is not None else "真实余额"
    chart_title = (
        "最近14天用电情况（余额估算）"
        if reading.subsidy_balance_yuan is not None and reading.unit_price_yuan_per_kwh is not None
        else "最近14天用电情况" if reading.meter_reading_kwh is not None else "最近14天电费余额"
    )
    detail_rows = [(cash_label, f"{safe(reading.balance_yuan)} 元")]
    if reading.meter_reading_kwh is not None:
        detail_rows.append(("电表累计读数", f"{safe(reading.meter_reading_kwh)} 度"))
    if reading.subsidy_kwh is not None:
        detail_rows.append(("剩余电补助", f"{safe(reading.subsidy_kwh)} 度"))
    if reading.subsidy_balance_yuan is not None:
        detail_rows.append(("补贴余额", f"{safe(reading.subsidy_balance_yuan)} 元"))
    if reading.meter_address:
        detail_rows.append(("电表地址", safe(reading.meter_address)))
    detail_row_html = []
    for index, (label, value) in enumerate(detail_rows):
        border = "border-bottom:1px solid #e5e7eb;" if index < len(detail_rows) - 1 else ""
        detail_row_html.append(
            '<tr>'
            f'<td style="padding:13px 16px;background:#f8fafc;color:#64748b;{border}">{label}</td>'
            f'<td style="padding:13px 16px;font-weight:600;{border}">{value}</td>'
            '</tr>'
        )
    detail_rows_html = "\n".join(detail_row_html)
    html_body = f"""\
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
  </head>
  <body style="margin:0;padding:0;background:#f3f6f9;color:#1f2937;font-family:Arial,'Microsoft YaHei',sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f6f9;">
      <tr>
        <td align="center" style="padding:28px 12px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="max-width:760px;background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;box-shadow:0 5px 20px rgba(15,23,42,.08);">
            <tr>
              <td style="padding:24px 30px;background:#1f77b4;color:#ffffff;">
                <div style="font-size:13px;letter-spacing:1px;opacity:.86;">CQU ELECTRICITY MONITOR</div>
                <div style="margin-top:7px;font-size:25px;font-weight:700;">重庆大学宿舍电费监控</div>
                <div style="margin-top:8px;font-size:14px;opacity:.9;">{safe(reading.building)} · {safe(reading.room)}</div>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 30px 12px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                       style="background:#f0f7fc;border:1px solid #cfe5f5;border-radius:10px;">
                  <tr>
                    <td style="padding:18px 18px 16px;">
                      <div style="font-size:13px;color:#64748b;">当前电费余额</div>
                      <div style="margin-top:5px;font-size:30px;line-height:1.2;font-weight:700;color:#1f77b4;white-space:nowrap;">
                        {safe(total_balance)} <span style="font-size:16px;font-weight:500;">元</span>
                      </div>
                      <div style="margin-top:10px;font-size:12px;line-height:1.5;color:#64748b;">
                        更新于 <span style="color:#334155;">{safe(captured_at)}</span>
                      </div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:12px 30px 8px;">
                <div style="margin-bottom:12px;font-size:17px;font-weight:700;color:#111827;">当前电费情况</div>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                       style="border-collapse:separate;border-spacing:0;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;font-size:14px;">
                  {detail_rows_html}
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:22px 30px 30px;">
                <div style="margin-bottom:12px;font-size:17px;font-weight:700;color:#111827;">{chart_title}</div>
                <div style="padding:10px;background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;">
                  <img src="data:image/png;base64,{image_base64}" alt="{chart_title}"
                       style="display:block;width:100%;max-width:100%;height:auto;border:0;" />
                </div>
              </td>
            </tr>
            <tr>
              <td align="center" style="padding:16px 24px;background:#f8fafc;border-top:1px solid #e5e7eb;font-size:12px;color:#94a3b8;">
                此邮件由重庆大学宿舍电费监控系统自动生成，请勿直接回复。
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
    plain_body = (
        "当前电费情况\n"
        f"抓取时间：{captured_at}\n"
        f"房间：{reading.room}\n"
        f"楼栋：{reading.building}\n"
        f"余额：{total_balance} 元\n"
        f"{cash_label}：{reading.balance_yuan} 元\n"
        + (f"电表累计读数：{reading.meter_reading_kwh} 度\n" if reading.meter_reading_kwh is not None else "")
        + (f"剩余电补助：{reading.subsidy_kwh} 度\n" if reading.subsidy_kwh is not None else "")
        + (f"补贴余额：{reading.subsidy_balance_yuan} 元\n" if reading.subsidy_balance_yuan is not None else "")
        + (f"电表地址：{reading.meter_address}\n" if reading.meter_address else "")
        + "最近14天图表已使用 Base64 内嵌在 HTML 邮件中。"
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(settings.smtp_to)
    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")
    return message


def send_electricity_email(
    settings: Settings, reading: MeterReading, chart_path: Path
) -> None:
    validate_email_settings(settings)
    message = _message(settings, reading, chart_path)
    context = ssl.create_default_context()
    try:
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout,
                context=context,
            ) as smtp:
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
            ) as smtp:
                if settings.smtp_starttls:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailError(f"SMTP 发送失败：{exc}") from exc
