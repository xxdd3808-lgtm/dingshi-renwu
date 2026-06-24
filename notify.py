#!/usr/bin/env python3
"""新股/新可转债上市日期提醒 — 查到日期即通知"""

import json
import os
from datetime import datetime, timedelta

import akshare as ak
import requests

# ---------- 配置 ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "state.json")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")

TODAY = datetime.now().strftime("%Y-%m-%d")


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_valid_listing_date(val):
    """判断是否为有效的上市日期（非空、非 NaT）"""
    s = str(val).strip()
    return s not in ("NaT", "nan", "None", "", "nat")


def is_recent(date_str, days=60):
    """上市日期是否在最近 N 天内（防止首次运行误报老数据）"""
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return d >= datetime.now() - timedelta(days=days)
    except ValueError:
        return False


def fetch_bond_listing_date(code):
    """查询可转债上市日期（通过申购代码匹配）"""
    df = ak.bond_zh_cov()
    match = df[df["申购代码"].astype(str).str.strip() == code]
    if match.empty:
        return None
    val = match.iloc[0]["上市时间"]
    if is_valid_listing_date(val):
        return str(val)[:10]
    return None


def fetch_stock_listing_date(code):
    """查询新股上市日期（通过申购代码或股票代码匹配）"""
    df = ak.stock_xgsglb_em(symbol="全部股票")
    # 先按申购代码查，再按股票代码查
    for col in ["申购代码", "股票代码"]:
        if col not in df.columns:
            continue
        match = df[df[col].astype(str).str.strip() == code]
        if not match.empty:
            val = match.iloc[0].get("上市日期")
            if is_valid_listing_date(val):
                return str(val)[:10]
    return None


def send_pushplus(title, content):
    """通过 PushPlus 发送微信推送"""
    if not PUSHPLUS_TOKEN:
        print("[WARN] PUSHPLUS_TOKEN 未设置，跳过推送（仅打印）")
        print(f"--- {title} ---\n{content}\n---")
        return False
    resp = requests.post(
        "http://www.pushplus.plus/send",
        json={"token": PUSHPLUS_TOKEN, "title": title, "content": content, "template": "txt"},
        timeout=15,
    )
    data = resp.json()
    ok = data.get("code") == 200
    print(f"[PUSH] {title} -> {'OK' if ok else data}")
    return ok


def main():
    config = load_json(CONFIG_FILE, {"items": []})
    state = load_json(STATE_FILE, {})
    items = config.get("items", [])

    if not items:
        print("config.json 中无监控条目，退出。")
        return

    new_discoveries = []
    status_lines = []

    for item in items:
        code = item["code"]
        name = item["name"]
        item_type = item["type"]
        key = f"{item_type}_{code}"

        # 已通知过的直接显示已知状态
        if state.get(key, {}).get("notified"):
            date = state[key]["date"]
            print(f"[DONE] {name}({code}) 已通知过，上市日期: {date}")
            status_lines.append(f"✅ {name}（{code}）— 上市日期: {date}")
            continue

        print(f"[CHECK] {name}({code}) type={item_type} ...", end=" ")

        try:
            if item_type == "bond":
                listing_date = fetch_bond_listing_date(code)
            else:
                listing_date = fetch_stock_listing_date(code)
        except Exception as e:
            print(f"查询异常: {e}")
            status_lines.append(f"⚠️ {name}（{code}）— 查询异常")
            continue

        if listing_date:
            if is_recent(listing_date):
                print(f"上市日期确定: {listing_date}")
                new_discoveries.append(f"✅ {name}（申购代码 {code}）— 上市日期确定: {listing_date}")
                state[key] = {"date": listing_date, "notified": True, "at": TODAY}
                status_lines.append(f"✅ {name}（{code}）— 上市日期: {listing_date}（新发现！）")
            else:
                print(f"上市日期 {listing_date}（已过期，跳过）")
                status_lines.append(f"⏳ {name}（{code}）— 已过期: {listing_date}")
        else:
            print("尚未公布上市日期")
            status_lines.append(f"⏳ {name}（{code}）— 尚未公布上市日期")

    # 发送状态汇报
    status_body = "\n".join(status_lines)
    if new_discoveries:
        title = f"🔔 新发现！{len(new_discoveries)} 个上市日期确定"
        body = "【新发现】\n" + "\n".join(new_discoveries) + "\n\n【全部状态】\n" + status_body
    else:
        title = "📋 上市监控日报"
        body = "暂无新发现，以下是当前监控状态：\n\n" + status_body

    print(f"\n[NOTIFY] 推送状态汇报...")
    send_pushplus(title, body)

    save_json(STATE_FILE, state)
    print("[DONE] state.json 已保存")


if __name__ == "__main__":
    main()
