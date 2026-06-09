"""SMT 半托JIT + 全托JIT 缺货订单日报

每天 9:30 推送昨日(T-1)缺货订单数,按店长降序排列。

环境变量:
  DORIS_HOST, DORIS_PORT, DORIS_DB, DORIS_USER, DORIS_PASS - Doris 连接
  DINGTALK_OOS_WEBHOOK - 钉钉机器人 webhook URL (关键词"缺货")
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


def fetch_oos_by_manager(yesterday: str) -> list[tuple[str, int]]:
    """查 SMT半托JIT + SMT全托JIT 缺货订单,按店长汇总."""
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
        """
        SELECT
          s.店长 AS manager,
          COUNT(DISTINCT s.订单号) AS oos_orders
        FROM mv_sell_lll s
        LEFT JOIN (SELECT DISTINCT 店铺, 平台 FROM mv_daily_sales_order_lll) p
          ON s.店铺 = p.店铺
        WHERE DATE(s.订单时间) = %s
          AND s.缺货标识 = '1'
          AND p.平台 IN ('SMT半托JIT','SMT全托JIT')
          AND s.店长 IS NOT NULL AND s.店长 <> ''
        GROUP BY s.店长
        ORDER BY oos_orders DESC
        """,
        (yesterday,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [(r[0], int(r[1])) for r in rows]


def build_markdown(rows: list[tuple[str, int]], yesterday: str) -> str:
    # 钉钉机器人关键词必须是 "JIT缺货" 才能通过
    if not rows:
        return f"## 📦 JIT缺货 日报 · {yesterday}\n\n昨日无缺货订单 🎉"

    total = sum(n for _, n in rows)
    lines = [
        f"## 📦 JIT缺货 日报 · {yesterday}",
        "",
        f"**SMT 半托JIT + 全托JIT 缺货合计：{total} 单**",
        "",
        "| 排名 | 店长 | 缺货订单数 |",
        "| :--: | :--- | ---: |",
    ]
    for i, (mgr, n) in enumerate(rows, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"
        lines.append(f"| {medal} | {mgr} | **{n}** |")
    return "\n".join(lines)


def send_dingtalk(webhook: str, title: str, text: str) -> dict:
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
    r = requests.post(webhook, json=payload, timeout=30)
    print(f"[DingTalk] {r.status_code} {r.text}")
    return r.json()


def main():
    # 取昨日(中国时间)
    now_cst = datetime.utcnow() + timedelta(hours=8)
    yesterday = (now_cst - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"[Date] yesterday={yesterday}")

    rows = fetch_oos_by_manager(yesterday)
    print(f"[Data] {len(rows)} managers, total {sum(n for _, n in rows)} oos orders")

    text = build_markdown(rows, yesterday)
    print("=" * 60)
    print(text)
    print("=" * 60)

    webhook = os.environ["DINGTALK_OOS_WEBHOOK"]
    # 标题里也带上关键词,双保险
    result = send_dingtalk(webhook, f"JIT缺货 日报 {yesterday}", text)
    if result.get("errcode") != 0:
        print(f"[ERROR] DingTalk failed: {result}")
        sys.exit(1)
    print("[Done]")


if __name__ == "__main__":
    main()
