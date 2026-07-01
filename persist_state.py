#!/usr/bin/env python3
"""通过 GitHub API 持久化 state.json

替代 git push，绕过网络超时问题。
并发安全：先拉取远程最新版本，合并本地变更后 PUT。

环境变量：
  GH_TOKEN          — GitHub token (默认使用 GITHUB_TOKEN)
  GITHUB_REPOSITORY — 仓库全名（GitHub Actions 自动设置）
  GITHUB_REF_NAME   — 分支名（GitHub Actions 自动设置）
"""

import base64
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "main")

    if not token:
        print("[ERROR] GH_TOKEN / GITHUB_TOKEN 未设置")
        sys.exit(1)
    if not repo:
        print("[ERROR] GITHUB_REPOSITORY 未设置")
        sys.exit(1)

    state_path = "state.json"
    if not os.path.exists(state_path):
        print("[INFO] state.json 不存在，跳过提交")
        return

    api = f"https://api.github.com/repos/{repo}/contents/state.json"

    # 1. 拉取远程 state.json 的 sha（必填，否则 PUT 会 409 conflict）
    remote_sha = None
    remote_content = ""
    req = urllib.request.Request(
        api + f"?ref={branch}",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "dingshi-renwu-bot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            remote = json.load(r)
        remote_sha = remote.get("sha")
        if remote.get("content"):
            remote_content = base64.b64decode(remote["content"]).decode("utf-8")
        print(f"[INFO] 远程 state.json sha={remote_sha[:8] if remote_sha else 'None'}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("[WARN] 远程 state.json 不存在，将创建")
        else:
            body = e.read().decode("utf-8", errors="ignore")[:200]
            print(f"[ERROR] 拉取远程 state.json 失败 HTTP {e.code}: {body}")
            sys.exit(1)
    except Exception as e:
        print(f"[ERROR] 拉取远程 state.json 异常: {e}")
        sys.exit(1)

    # 2. 读取本地（运行后的）state.json
    with open(state_path, "r", encoding="utf-8") as f:
        local_content = f.read()

    # 3. 如果远程有更新（并发已提交过），合并后重写
    if remote_sha and remote_content:
        try:
            remote_state = json.loads(remote_content)
            local_state = json.loads(local_content)
            # 合并：本地新增 key 保留，已有 key 的字段以本地为准（本地刚运行完，更新）
            # 但远程可能写入过新条目（race），也保留
            merged = dict(remote_state)
            for k, v in local_state.items():
                if k in merged:
                    merged[k] = {**merged[k], **v}
                else:
                    merged[k] = v
            new_content = json.dumps(merged, ensure_ascii=False, indent=2)
            if new_content != local_content:
                print("[INFO] 检测到远程 state.json 更新，已合并")
                with open(state_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                local_content = new_content
        except Exception as e:
            print(f"[WARN] 合并 state 失败，使用本地版本: {e}")

    # 4. PUT 更新
    payload = json.dumps({
        "message": f"update state {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "content": base64.b64encode(local_content.encode("utf-8")).decode(),
        "branch": branch,
        "sha": remote_sha,
    }).encode("utf-8")
    req = urllib.request.Request(
        api,
        data=payload,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "dingshi-renwu-bot",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"[OK] state.json 已提交: HTTP {r.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"[ERROR] state.json 提交失败 HTTP {e.code}: {body}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] state.json 提交异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
