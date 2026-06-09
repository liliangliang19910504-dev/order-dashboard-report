"""低毛利 SKU 日报 - 经理"任杰"

每天 10:00 推送昨日(T-1)数据,筛选:
  - 经理 = 任杰
  - 平台 ∈ {SMT 5 子类, ozon.ru}
  - 订单总毛利 < 1
  - 同一店长名下,同一 SKU 当日订单数 > 3

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
MIN_ORDERS_PER_DAY = 3  # >3 单才报警


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
          s.商品中文名,
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
        GROUP BY s.店长, s.SKU, s.商品中文名
        HAVING COUNT(DISTINCT s.订单号) > %s
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
            "name": r[2] or "",
            "orders": int(r[3]),
            "avg_margin": float(r[4] or 0),
            "min_margin": float(r[5] or 0),
        }
        for r in rows
    ]


def truncate_name(name: str, max_len: int = 22) -> str:
    """商品中文名太长时截断."""
    if len(name) <= max_len:
        return name
    return name[:max_len] + "…"


def build_markdown(items: list[dict], yesterday: str) -> str:
    title = f"## 💸 低毛利 SKU 日报 · {yesterday}"
    sub = f"**经理：任杰 · 平台：SMT + Ozon.ru · 订单毛利<1 且当日 SKU 出单>3**"

    if not items:
        return f"{title}\n\n{sub}\n\n昨日无低毛利预警 ✅"

    # 按店长分组
    by_manager = {}
    for it in items:
        by_manager.setdefault(it["manager"], []).append(it)

    lines = [title, "", sub, ""]
    lines.append(f"**累计预警 SKU：{len(items)} 条 / 涉及店长 {len(by_manager)} 人**")
    lines.append("")

    for mgr in sorted(by_manager, key=lambda m: -len(by_manager[m])):
        sku_list = by_manager[mgr]
        lines.append(f"### 👤 店长 {mgr}（{len(sku_list)} 条）")
        lines.append("")
        lines.append("| SKU | 商品 | 单数 | 均毛利 | 最低 |")
        lines.append("| --- | --- | ---: | ---: | ---: |")
        for it in sku_list:
            min_m = it["min_margin"]
            min_str = f"**{min_m:.2f}** ❗" if min_m < 0 else f"{min_m:.2f}"
            lines.append(
                f"| `{it['sku']}` | {truncate_name(it['name'])} "
                f"| {it['orders']} | {it['avg_margin']:.2f} | {min_str} |"
            )
        lines.append("")

    return "\n".join(lines)


def send_dingtalk(webhook: str, title: str, text: str) -> dict:
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
    r = requests.post(webhook, json=payload, timeout=30)
    print(f"[DingTalk] {r.status_code} {r.text}")
    return r.json()


def main():
    now_cst = datetime.utcnow() + timedelta(hours=8)
    yesterday = (now_cst - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"[Date] yesterday={yesterday}")

    items = fetch_low_margin_skus(yesterday)
    print(f"[Data] {len(items)} low-margin SKUs found")

    text = build_markdown(items, yesterday)
    print("=" * 60)
    print(text)
    print("=" * 60)

    webhook = os.environ["DINGTALK_LOW_MARGIN_WEBHOOK"]
    # 标题里也带上关键词"低毛利"
    result = send_dingtalk(webhook, f"低毛利 SKU 日报 {yesterday}", text)
    if result.get("errcode") != 0:
        print(f"[ERROR] DingTalk failed: {result}")
        sys.exit(1)
    print("[Done]")


if __name__ == "__main__":
    main()
