# 新股/转债上市日期提醒系统

## 项目用途

用户中签新股/可转债后，自动监控上市日期，通过 PushPlus 微信推送。每个条目最多推送 2 次：查到上市日期时 + 上市当日。

## 架构（2026-07-02 加固版）

```
腾讯云 SCF (北京时间 09:05 定时)
  → HTTP POST 到 GitHub API (repository_dispatch)
    → GitHub Actions workflow (concurrency group 串行)
      → notify.py 查询 akshare + PushPlus 推送
      → persist_state.py 通过 GitHub API PUT state.json
```

## 关键文件

| 文件 | 作用 |
|------|------|
| `config.json` | 监控列表 |
| `state.json` | 通知状态（date_notified + day_notified） |
| `notify.py` | 主脚本：查询 akshare + 推送 PushPlus |
| `persist_state.py` | 通过 GitHub API 持久化 state.json（替代 git push） |
| `scf_handler.py` | SCF 触发器（~50 行，仅 HTTP POST） |
| `deploy.py` | 一键部署 SCF |
| `.github/workflows/daily-check.yml` | GitHub Actions 定义 |

## 重要约束（务必遵守）

1. **PAT 不能硬编码在代码里**：用 `os.environ.get("GITHUB_PAT")`，通过 SCF 环境变量传入。仓库是 PUBLIC，硬编码会泄漏。
2. **state.json 持久化用 `persist_state.py`**，不要回退到 `git push`（网络偶发超时）。
3. **不要加 workflow 层去重**：每次 schedule 都真正执行查询，去重靠 state.json 条目级字段。
4. **SCF 只是触发器**：业务逻辑全在 notify.py。修改 scf_handler.py 才需要 `python3 deploy.py` 重新部署。
5. **上市日期判断分三档**：`== today` → "今日上市"；`< today` 且未通知 → "补通知"；`> today` → "上市日期"。
6. **不抓取预测价格**：预测功能已移除。纯正则从饕餮海文章抓价格不可靠（一篇文章多只转债易误抓、申购期预估 vs 上市日测算语义不同、涨停价 vs 合理价难区分）。只推送上市时间，预测用户自行查。如需恢复，优先用 config.json 的 `prediction` 字段手动填，不要重新接 Firecrawl 自动抓取。

## 常用命令

```bash
# 查看最近运行
gh run list --limit 10

# 手动触发检查（通过 SCF，验证完整链路）
tccli scf Invoke --FunctionName ipo-notify --region ap-shanghai

# 手动触发检查（直接 GitHub Actions）
gh workflow run daily-check.yml

# 重新部署 SCF（仅改 scf_handler.py 时需要）
GITHUB_PAT=ghp_xxx python3 deploy.py

# 添加新监控条目（推荐用 skill）
/ipo-notify
```

## 当前状态

- **监控条目**：6 个（5 转债 + 1 已上市新股），见 config.json
- **系统加固**：2026-07-02 完成 14 项可靠性与安全性修复，通过 29 项对抗性测试
- **月费用**：¥0（SCF 免费额度 + GitHub Actions 公开仓库免费 + PushPlus 免费）
- **最近 commit**：`fbd532e` workflow heredoc 修复

## 详细文档

更深入的背景在 memory 系统（`~/.claude/projects/-Users-bt-dingshi-renwu/memory/`）：

- `project_overview.md` — 架构细节、腾讯云/GitHub 资源、关键决策
- `known_issues.md` — 17 个历史问题与解决方案（必读，避免重复踩坑）
- `notification_logic.md` — 通知时机、state.json 格式、去重机制
- `operations.md` — 日常运维命令、PAT 更换流程
- `monitor_list.md` — 当前监控条目状态

## Git 信息

- 仓库: `xxdd3808-lgtm/dingshi-renwu` (PUBLIC)
- 主分支: `main`
- Git push 偶发超时是已知问题，用 `gh api` PUT contents 绕过
