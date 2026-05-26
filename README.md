# 订单业绩看板 - 每日钉钉播报

每天 8:30 自动截图订单看板的"昨日"数据，发送到钉钉群。

## 触发链路

```
Vercel Cron (00:30 UTC = 08:30 北京)
  → /api/trigger-dashboard (ozon-cron-trigger 项目)
  → GitHub Actions workflow_dispatch (本仓库)
  → report.py
    ├─ Playwright 截图看板昨日数据
    ├─ Commit 截图到 main 分支
    └─ 发钉钉 (markdown + raw.githubusercontent.com 图片URL)
```

## Secrets

仓库 Settings → Secrets and variables → Actions:

- `DINGTALK_WEBHOOK` - 钉钉机器人 webhook URL
- `DINGTALK_SECRET`  - 钉钉加签 secret

## 本地测试

```bash
pip install -r requirements.txt
playwright install chromium
export DINGTALK_WEBHOOK="https://oapi.dingtalk.com/robot/send?access_token=..."
export DINGTALK_SECRET="SEC..."
python report.py
```

## 历史截图

`screenshots/YYYY-MM-DD.png`
