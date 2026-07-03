#!/usr/bin/env python3
"""新股/新可转债上市日期提醒 — 查到日期即通知"""

import json
import os
import re
from datetime import datetime, date

import akshare as ak
import requests

# ---------- 配置 ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "state.json")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")

TODAY = datetime.now().strftime("%Y-%m-%d")
TODAY_DATE = datetime.now().date()


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


def parse_date(date_str):
    """解析 YYYY-MM-DD，失败返回 None"""
    try:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def fetch_bond_listing_date(code):
    """查询可转债上市日期（通过申购代码匹配）"""
    df = ak.bond_zh_cov()
    if "申购代码" not in df.columns or "上市时间" not in df.columns:
        raise RuntimeError(
            f"akshare.bond_zh_cov 列名变化: 期望 ['申购代码','上市时间'], 实际 {list(df.columns)}"
        )
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
    if "上市日期" not in df.columns:
        raise RuntimeError(
            f"akshare.stock_xgsglb_em 列名变化: 缺少 '上市日期', 实际 {list(df.columns)}"
        )
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
    """通过 PushPlus 发送微信推送（HTTPS + 1 次重试）"""
    if not PUSHPLUS_TOKEN:
        print("[WARN] PUSHPLUS_TOKEN 未设置，跳过推送（仅打印）")
        print(f"--- {title} ---\n{content}\n---")
        return False

    url = "https://www.pushplus.plus/send"
    payload = {"token": PUSHPLUS_TOKEN, "title": title, "content": content, "template": "txt"}

    last_err = None
    for attempt in range(2):  # 1 次正常 + 1 次重试
        try:
            resp = requests.post(url, json=payload, timeout=15)
            data = resp.json()
            ok = data.get("code") == 200
            print(f"[PUSH] {title} -> {'OK' if ok else data}")
            if ok:
                return True
            # PushPlus 业务错误（如 token 失效）不重试
            return False
        except Exception as e:
            last_err = e
            print(f"[WARN] 推送第 {attempt + 1} 次失败: {e}")
            if attempt == 0:
                import time
                time.sleep(2)

    print(f"[ERROR] 推送最终失败: {last_err}")
    return False


def search_taotiehai_prediction(bond_name):
    """搜索饕餮海对某转债的上市价格预测

    优先级：
    1. config.json 中的 prediction 字段（用户手动填，100% 准确）
    2. Firecrawl 抓饕餮海雪球专栏文章，搜索转债名称提取价格

    返回字符串如 "157-180元 (来源: 饕餮海雪球)" 或 None
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key:
        print("  [WARN] FIRECRAWL_API_KEY 未设置，无法搜索饕餮海预测")
        return None

    # 转债名称关键词：春风发债 → 春风
    keyword = bond_name.replace("发债", "").replace("转债", "").strip()
    # 饕餮海文章里用 "XX 转债" 格式
    bond_alias = keyword + "转债"

    print(f"  [FIRECRAWL] 搜索饕餮海对 {bond_alias} 的预测...")

    try:
        # 步骤1: 抓饕餮海雪球用户页面，拿最近文章列表
        resp = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": "https://xueqiu.com/u/1314783718",
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            print(f"  [WARN] Firecrawl 抓用户页面失败: {data}")
            return None

        md = data["data"]["markdown"]
        # 提取文章 ID（格式：xueqiu.com/1314783718/数字）
        article_ids = re.findall(r"xueqiu\.com/1314783718/(\d+)", md)
        # 去重保持顺序，最多抓 20 篇（覆盖约 1 个月）
        unique_ids = list(dict.fromkeys(article_ids))[:20]
        print(f"  [FIRECRAWL] 找到 {len(unique_ids)} 篇文章，逐篇搜索...")

        # 步骤2: 逐篇抓内容，搜索转债名称
        for i, article_id in enumerate(unique_ids):
            article_url = f"https://xueqiu.com/1314783718/{article_id}"
            try:
                resp = requests.post(
                    "https://api.firecrawl.dev/v1/scrape",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "url": article_url,
                        "formats": ["markdown"],
                        "onlyMainContent": True,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("success"):
                    continue

                article_md = data["data"]["markdown"]

                # 必须同时包含转债名称和"价格估算"关键词，才算找到预测文章
                # （避免大盘复盘文章顺带提到转债名称的误匹配）
                if bond_alias not in article_md and keyword not in article_md:
                    continue

                if "价格估算" not in article_md and "价格在" not in article_md:
                    print(f"  [SKIP] 文章 {article_id} 含 {bond_alias} 但无价格估算段落")
                    continue

                print(f"  [FIRECRAWL] 在文章 {article_id} 中找到 {bond_alias} 的价格估算")

                # 提取价格：格式 "**7. 价格估算：** 我认为可能价格在 **175—180元。**"
                # 支持全角破折号 —、半角 -、~ 等；用 [^\d]*? 跳过 ** 等符号
                price_match = re.search(
                    r"价格估算[：:][^\d]*?(\d{2,4}(?:\.\d+)?)\s*[—~\-至到～]\s*(\d{2,4}(?:\.\d+)?)\s*元",
                    article_md,
                )
                if price_match:
                    low, high = price_match.group(1), price_match.group(2)
                    return f"{low}-{high}元 (来源: 饕餮海雪球)"

                # 备选：匹配 "每手盈利 XXX-XXX 元"
                profit_match = re.search(
                    r"每手.*?盈利[：:]?\s*(\d{2,4})\s*[—~\-至到～]\s*(\d{2,4})\s*元",
                    article_md,
                )
                if profit_match:
                    low, high = profit_match.group(1), profit_match.group(2)
                    return f"每手盈利{low}-{high}元 (来源: 饕餮海雪球)"

                print(f"  [WARN] 文章 {article_id} 有价格估算段落但未提取到价格")
                return None

            except Exception as e:
                print(f"  [WARN] 抓文章 {article_id} 失败: {e}")
                continue

        print(f"  [FIRECRAWL] 最近 {len(unique_ids)} 篇文章未提到 {bond_alias} 的价格估算")
        return None

    except Exception as e:
        print(f"  [WARN] Firecrawl 查询异常: {e}")
        return None


def main():
    config = load_json(CONFIG_FILE, {"items": []})
    state = load_json(STATE_FILE, {})
    items = config.get("items", [])

    if not items:
        print("config.json 中无监控条目，退出。")
        return

    new_discoveries = []
    listing_alerts = []  # 上市当日提醒 + 补通知（state 丢失时）
    status_lines = []

    for item in items:
        code = item["code"]
        name = item["name"]
        item_type = item["type"]
        note = item.get("note", "")
        label = f"{name}[{note}]" if note else name
        key = f"{item_type}_{code}"
        s = state.get(key, {})

        print(f"[CHECK] {label}({code}) type={item_type} ...", end=" ")

        try:
            if item_type == "bond":
                listing_date = fetch_bond_listing_date(code)
            else:
                listing_date = fetch_stock_listing_date(code)
        except Exception as e:
            print(f"查询异常: {e}")
            status_lines.append(f"⚠️ {label}（{code}）— 查询异常")
            continue

        if listing_date:
            print(f"上市日期: {listing_date}")
            # 更新 state 中的日期
            if s.get("date") != listing_date:
                s["date"] = listing_date

            # 解析为 date 对象用于比较
            ld = parse_date(listing_date)
            if ld is None:
                print(f"[WARN] 上市日期格式异常: {listing_date}，跳过该条目")
                status_lines.append(f"⚠️ {label}（{code}）— 上市日期格式异常: {listing_date}")
                continue

            # 通知1: 查到上市日期（首次发现）
            # 兼容旧版 state 格式（notified=True 等同于 date_notified）
            if not s.get("date_notified") and not s.get("notified"):
                # 优先用 config.json 的 prediction 字段（用户手动填，100% 准确）
                manual_prediction = item.get("prediction", "")
                if manual_prediction:
                    pred_text = f"\n    预涨幅: {manual_prediction} (手动填)"
                    print(f"  [INFO] 使用 config.json 中的 prediction: {manual_prediction}")
                else:
                    pred = search_taotiehai_prediction(name)
                    if pred:
                        pred_text = f"\n    预涨幅: {pred}"
                    else:
                        pred_text = f"\n    预涨幅: 未查到"
                new_discoveries.append(f"✅ {label}（申购代码 {code}）— 上市日期确定: {listing_date}{pred_text}")
                s["date_notified"] = True
                s["date_notified_at"] = TODAY

            # 通知2: 上市当日（仅当上市日期 == 今天，避免补报过去日期误说"今日上市"）
            # 如果 state 丢失导致 day_notified=False 但上市日期已过，补一次"已上市"通知
            if ld == TODAY_DATE and not s.get("day_notified"):
                listing_alerts.append(f"🚀 {label}（申购代码 {code}）— 今日上市！（{listing_date}）")
                s["day_notified"] = True
                s["day_notified_at"] = TODAY
            elif ld < TODAY_DATE and not s.get("day_notified"):
                # 补通知：state 丢失或新增条目时，已过上市日期但未通知过
                listing_alerts.append(f"📌 {label}（申购代码 {code}）— 已于 {listing_date} 上市（补通知）")
                s["day_notified"] = True
                s["day_notified_at"] = TODAY

            # 状态行
            if s.get("day_notified"):
                status_lines.append(f"✅ {label}（{code}）— 已上市: {listing_date}")
            elif ld == TODAY_DATE:
                status_lines.append(f"🔔 {label}（{code}）— 今日上市: {listing_date}")
            elif ld < TODAY_DATE:
                status_lines.append(f"📌 {label}（{code}）— 已上市（未通知）: {listing_date}")
            else:
                status_lines.append(f"📋 {label}（{code}）— 上市日期: {listing_date}")

            state[key] = s
        else:
            print("尚未公布上市日期")
            status_lines.append(f"⏳ {label}（{code}）— 尚未公布上市日期")

    # 构建通知内容 — 只有新发现或上市当日才推送
    alerts = []
    if new_discoveries:
        alerts.append("【新发现上市日期】\n" + "\n".join(new_discoveries))
    if listing_alerts:
        alerts.append("【上市提醒】\n" + "\n".join(listing_alerts))

    if alerts:
        status_body = "【全部状态】\n" + "\n".join(status_lines)
        title = f"🔔 上市提醒（{len(new_discoveries) + len(listing_alerts)} 条）"
        body = "\n\n".join(alerts) + "\n\n" + status_body
        print(f"\n[NOTIFY] 推送中...")
        send_pushplus(title, body)
    else:
        print(f"\n[INFO] 无新发现，不推送（{len(status_lines)} 个条目）")
        for line in status_lines:
            print(f"  {line}")

    save_json(STATE_FILE, state)
    print("[DONE] state.json 已保存")


if __name__ == "__main__":
    main()
