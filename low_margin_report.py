"""低毛利 SKU 日报 - 经理"任杰"

每天 10:00 推送昨日(T-1)数据,筛选:
  - 经理 = 任杰
  - 平台 ∈ {SMT 5 子类, ozon.ru}
  - 订单总毛利 < 1
  - 同一店长名下,同一 SKU 当日订单数 >= 2

环境变量:
  DORIS_HOST, DORIS_PORT, DORIS_DB, DORIS_USER, DORIS_PASS
  DINGTALK_LOW_MARGIN_WEBHOOK  - 钉钉机器人 webhook URL (关键词"低毛利")
"""
import io
import os
import sys
from datetime import datetime, timedelta

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pymysql
import requests

MANAGER = "任杰"
PLATFORMS = ("aliexpress", "SMT半托仓发", "SMT半托JIT", "SMT全托仓发", "SMT全托JIT", "ozon.ru")
GROSS_MARGIN_LIMIT = 1.0
MIN_ORDERS_PER_DAY = 2  # >=2 单就报警

# 店长/经理 → 钉钉手机号 (用于 @)
PHONE_BOOK = {
    "任杰": "18291450432",      # 经理(顶部 @)
    "王珊": "15664916997",
    "何山": "13510297550",
    "黄子涵": "13233591738",
    "刘濛谦": "13116621785",
    "阎沐晗": "13643798540",
}


def fetch_low_margin_skus(yesterday: str) -> list[dict]:
    conn = pymysql.connect(
        host=os.environ["DORIS_HOST"],
        port=int(os.environ["DORIS_PORT"]),
        user=os.environ["DORIS_USER"],
        password=os.environ["DORIS_PASS"],
        database=os.environ["DORIS_DB"],
        charset="utf8mb4",
        connect_timeout=15,
    )
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT
          s.店长,
          s.SKU,
          COUNT(DISTINCT s.订单号) AS oos_count,
          ROUND(AVG(o.订单总毛利), 2) AS avg_margin,
          ROUND(MIN(o.订单总毛利), 2) AS min_margin
        FROM mv_sell_lll s
        JOIN mv_order_lll o ON s.订单号 = o.订单编号
        LEFT JOIN (SELECT DISTINCT 店铺, 平台 FROM mv_daily_sales_order_lll) p
          ON s.店铺 = p.店铺
        WHERE s.经理 = %s
          AND p.平台 IN {PLATFORMS}
          AND o.订单总毛利 < %s
          AND DATE(s.订单时间) = %s
          AND s.店长 IS NOT NULL AND s.店长 <> ''
        GROUP BY s.店长, s.SKU
        HAVING COUNT(DISTINCT s.订单号) >= %s
        ORDER BY s.店长, oos_count DESC
        """,
        (MANAGER, GROSS_MARGIN_LIMIT, yesterday, MIN_ORDERS_PER_DAY),
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "manager": r[0],
            "sku": r[1],
            "orders": int(r[2]),
            "avg_margin": float(r[3] or 0),
            "min_margin": float(r[4] or 0),
        }
        for r in rows
    ]


def build_markdown_and_mentions(items: list[dict], yesterday: str) -> tuple[str, list[str]]:
    """返回 (markdown 文本, 需要 @ 的手机号列表)."""
    # @任杰(经理)固定置顶
    mentions: list[str] = []
    manager_phone = PHONE_BOOK.get(MANAGER)

    title = f"## 💸 低毛利 日报 · {yesterday}"
    sub = f"**经理 {MANAGER} · SMT+Ozon.ru · 订单毛利<1 且 SKU 当日出单≥2**"

    lines = [title, "", sub, ""]
    if manager_phone:
        lines.append(f"@{manager_phone} {MANAGER}（经理）")
        lines.append("")
        mentions.append(manager_phone)

    if not items:
        lines.append("---")
        lines.append("")
        lines.append("昨日无低毛利预警 ✅")
        return "\n".join(lines), mentions

    # 按店长分组
    by_mgr = {}
    for it in items:
        by_mgr.setdefault(it["manager"], []).append(it)

    lines.append(f"**累计预警 SKU：{len(items)} 条 / 涉及店长 {len(by_mgr)} 人**")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 店长内按订单数降序,店长之间按命中条数降序
    for mgr in sorted(by_mgr, key=lambda m: -len(by_mgr[m])):
        sku_list = sorted(by_mgr[mgr], key=lambda x: -x["orders"])
        phone = PHONE_BOOK.get(mgr)
        if phone:
            lines.append(f"@{phone} **{mgr}**")
            mentions.append(phone)
        else:
            lines.append(f"**{mgr}**  ⚠️ 未配置手机号")
        lines.append("")
        lines.append("| SKU | 单数 | 均毛利 | 最低 |")
        lines.append("| --- | ---: | ---: | ---: |")
        for it in sku_list:
            min_m = it["min_margin"]
            min_str = f"**{min_m:.2f}** ❗" if min_m < 0 else f"{min_m:.2f}"
            lines.append(
                f"| `{it['sku']}` | {it['orders']} | {it['avg_margin']:.2f} | {min_str} |"
            )
        lines.append("")

    return "\n".join(lines), mentions


def send_dingtalk(webhook: str, title: str, text: str, at_mobiles: list[str]) -> dict:
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
        "at": {"atMobiles": at_mobiles, "isAtAll": False},
    }
    r = requests.post(webhook, json=payload, timeout=30)
    print(f"[DingTalk] {r.status_code} {r.text}")
    return r.json()


def main():
    now_cst = datetime.utcnow() + timedelta(hours=8)
    yesterday = (now_cst - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"[Date] yesterday={yesterday}")

    items = fetch_low_margin_skus(yesterday)
    print(f"[Data] {len(items)} low-margin SKUs found")

    text, at_mobiles = build_markdown_and_mentions(items, yesterday)
    print(f"[Mentions] @ {len(at_mobiles)} people: {at_mobiles}")
    print("=" * 60)
    print(text)
    print("=" * 60)

    # 去重(任杰既是经理也是店长时会重复)
    at_mobiles = list(dict.fromkeys(at_mobiles))

    webhook = os.environ["DINGTALK_LOW_MARGIN_WEBHOOK"]
    result = send_dingtalk(webhook, f"低毛利 日报 {yesterday}", text, at_mobiles)
    if result.get("errcode") != 0:
        print(f"[ERROR] DingTalk failed: {result}")
        sys.exit(1)
    print("[Done]")


if __name__ == "__main__":
    main()
