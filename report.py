"""每日订单业绩看板播报：截图 + 文字摘要 → 钉钉

环境变量：
  DINGTALK_WEBHOOK - 钉钉机器人 webhook URL
  DINGTALK_SECRET  - 钉钉加签 secret
  GITHUB_REPOSITORY - GitHub Actions 自动注入,格式 "owner/repo"
  GITHUB_REF_NAME   - 分支名(默认 main)
"""
import io
import os
import sys
import time

# Windows 本地控制台默认 GBK,强制 UTF-8 避免 emoji 报错
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import hmac
import hashlib
import base64
import urllib.parse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

DASHBOARD_URL = "https://order-dashboard-theta-rose.vercel.app/"
API_URL = "https://order-dashboard-theta-rose.vercel.app/api/metrics"


def screenshot_dashboard(out_path: Path) -> None:
    """打开看板,点击昨日,截全图."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=2,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = ctx.new_page()
        page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=60000)
        # 默认是"今日",点切到"昨日"
        page.click('button:has-text("昨日")')
        # 等待数据重新拉取并渲染
        page.wait_for_timeout(5000)
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out_path), full_page=True)
        browser.close()
    print(f"[OK] screenshot saved: {out_path}")


def fetch_metrics(yesterday: str, day_before: str) -> dict:
    """从看板自己的 API 拉昨日数据."""
    params = {
        "start": yesterday,
        "end": yesterday,
        "cmpStart": day_before,
        "cmpEnd": day_before,
    }
    r = requests.get(API_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def sign_dingtalk(secret: str) -> tuple[str, str]:
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    h = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(h))
    return timestamp, sign


def fmt_money(n: float) -> str:
    n = float(n or 0)
    if abs(n) >= 10000:
        return f"{n / 10000:.2f}万"
    return f"{n:.2f}"


def fmt_pct(curr: float, prev: float) -> str:
    if not prev:
        return ""
    p = (curr - prev) / prev * 100
    sign = "🔺" if p >= 0 else "🔻"
    return f" {sign}{abs(p):.1f}%"


def build_markdown(metrics: dict, screenshot_url: str, yesterday: str) -> str:
    cur = {r["platform"]: r for r in metrics.get("current", [])}
    cmp = {r["platform"]: r for r in metrics.get("comparison", [])}

    def g(d, p, k):
        return float(d.get(p, {}).get(k) or 0)

    keys = ["amazon", "ozon", "smt", "tiktok", "fruugo"]
    total_o = sum(int(g(cur, k, "orders")) for k in keys)
    total_g = sum(g(cur, k, "gmv") for k in keys)
    prev_o = sum(int(g(cmp, k, "orders")) for k in keys)
    prev_g = sum(g(cmp, k, "gmv") for k in keys)

    label = {
        "amazon": "Amazon",
        "ozon": "Ozon.ru",
        "smt": "SMT平台",
        "tiktok": "TikTok",
        "fruugo": "Fruugo",
    }

    lines = []
    lines.append(f"## 📊 订单业绩日报 · {yesterday}")
    lines.append("")
    lines.append(f"**全平台汇总**")
    lines.append(
        f"- 总订单 **{total_o:,}**{fmt_pct(total_o, prev_o)} · 总GMV **{fmt_money(total_g)}**{fmt_pct(total_g, prev_g)}"
    )
    lines.append("")
    lines.append("**各平台拆解**")
    for k in keys:
        o = int(g(cur, k, "orders"))
        v = g(cur, k, "gmv")
        po = int(g(cmp, k, "orders"))
        pv = g(cmp, k, "gmv")
        lines.append(
            f"- {label[k]}: {o:,} 单 / {fmt_money(v)}"
            f" (订单{fmt_pct(o, po)} GMV{fmt_pct(v, pv)})"
        )
    lines.append("")
    lines.append(f"![看板截图]({screenshot_url})")
    lines.append("")
    lines.append(f"[🔗 查看完整看板](https://order-dashboard-theta-rose.vercel.app/)")
    return "\n".join(lines)


def send_dingtalk(webhook: str, secret: str, title: str, text: str) -> None:
    ts, sign = sign_dingtalk(secret)
    url = f"{webhook}&timestamp={ts}&sign={sign}"
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
    r = requests.post(url, json=payload, timeout=30)
    print(f"[DingTalk] {r.status_code} {r.text}")
    r.raise_for_status()


def git_commit_push(file_path: Path, message: str) -> None:
    """提交截图到当前分支."""
    subprocess.run(["git", "config", "user.email", "action@github.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Daily Report Bot"], check=True)
    subprocess.run(["git", "add", str(file_path)], check=True)
    # 没有变化时不报错
    result = subprocess.run(["git", "diff", "--staged", "--quiet"])
    if result.returncode == 0:
        print("[Git] no changes to commit")
        return
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)
    print(f"[Git] pushed: {message}")


def main():
    now_cst = datetime.utcnow() + timedelta(hours=8)
    yesterday = (now_cst - timedelta(days=1)).strftime("%Y-%m-%d")
    day_before = (now_cst - timedelta(days=2)).strftime("%Y-%m-%d")
    print(f"[Date] yesterday={yesterday} day_before={day_before}")

    # 1. 截图
    fname = f"screenshots/{yesterday}.png"
    screenshot_path = Path(fname)
    screenshot_dashboard(screenshot_path)

    # 2. 提交截图到 main 分支(GitHub Actions 中)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        git_commit_push(screenshot_path, f"📸 {yesterday} 看板截图")
        # 等 raw.githubusercontent.com CDN 刷新
        time.sleep(15)

    # 3. 拼图片 URL
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if repo:
        screenshot_url = (
            f"https://raw.githubusercontent.com/{repo}/{branch}/{fname}"
        )
    else:
        screenshot_url = "(本地运行,无图片URL)"

    # 4. 拉指标
    metrics = fetch_metrics(yesterday, day_before)

    # 5. 拼 markdown
    text = build_markdown(metrics, screenshot_url, yesterday)
    print("=" * 60)
    print(text)
    print("=" * 60)

    # 6. 发钉钉
    webhook = os.environ.get("DINGTALK_WEBHOOK")
    secret = os.environ.get("DINGTALK_SECRET")
    if not webhook or not secret:
        print("[ERROR] DINGTALK_WEBHOOK / DINGTALK_SECRET 环境变量未设置")
        sys.exit(1)
    send_dingtalk(webhook, secret, f"订单业绩日报 {yesterday}", text)
    print("[Done]")


if __name__ == "__main__":
    main()
