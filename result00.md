# 2026-10-27 — Luna Router：即時動態決策，不再追問、不再審核

規模：S（主控自做，未派工）。專案非 git repo，故無 commit。

## 根因（四處，皆已修）
1. **信心門檻擋下所有實際決策** — `decision.assess_decision` 要求 route 機率 ≥0.95、effort ≥0.80；實測 Luna 回 0.64／0.68，故每次都 `blocked: luna_decision_below_threshold`，代理只得回頭問主人。
   → `router.make_plan`：Luna 有效答覆即採用、立即派工；低信心只記 `luna_low_certainty_accepted`。舊門檻改為選用：`config.json` 之 `policy.block_low_certainty`（預設 false）。
2. **Luna 回應被誤判為假模型** — OpenRouter 實際回 `openai/gpt-6-luna-decisions-20261006`（日期快照），程式以字串全等比對 → `decision_model_mismatch`，所有 live run 皆 blocked。
   → `decision.is_decision_model()` 接受原 id 或 `<id>-YYYYMMDD`；`router.py` 三處、`luna.py probe` 一處改用之。
3. **Worker 啟動失敗／可能停下等審批**
   - Windows 上 `codex` 只解析到 npm `codex.cmd`，`Popen(shell=False)` 無法啟動 → `codex_unavailable`。→ `codex_backend._windows_native_codex()` 改用 `CODEX_CLI_PATH` 或 npm 內建原生 `codex.exe`。
   - `codex exec` 於非 git 目錄拒跑（`Not inside a trusted directory`）。→ 加 `--skip-git-repo-check`。
   - 明示 `-c approval_policy="never"`，worker 不停下等核准；sandbox 不變（仍限 read-only／workspace-write）。
4. **全域指令要求先問** — `~/.codex/AGENTS.md` 之舊 luna 區塊強令 `--offline`，且「未經明確允許不用 Luna」。→ 改寫為：第一步直接呼叫 `luna_run`，不問模型／effort／是否呼叫 Luna，不先 `luna_plan`，完工只跑任務本身測試一次，不另起 review 輪；只有 `blocked` 才停並回報原因。
   - SKILL.md 與 MCP 工具描述同步改寫；刪除「第一個任務須唯讀」一條。

## 另修（測試隔離）
- 本機 Windows 登錄檔有 Key，致 3 個「無 Key」測試失敗，且安裝器測試會在本機真的發起 live run。新增 `LUNA_ROUTER_IGNORE_REGISTRY_KEY=1` 供測試隔離；cp950 讀檔錯誤改以 UTF-8 讀。

## 驗證
- 單元／整合：`python -m unittest discover -s tests -q` → 267 tests OK（17 skipped，皆為 Unix-only fixture）。基線原為 3 failures + 1 error。新增測試：低信心立即派工、選用門檻仍可擋、日期快照 id、npm shim 解析。
- `python verify-package.py` → PASS 122 entries（MANIFEST 已重算）。
- 已安裝：`python install_router.py --register-mcp` → `~/.codex/luna-router`，MCP `luna_router_v270` 已註冊入 `~/.codex/config.toml`。備份：`~/.codex/luna-router-backups/20261027T021549Z-9e86c9`（首次，含舊 AGENTS.md 與 config.toml），其後數次重裝亦各有備份。
- **Live 驗收（真實 API／真實 Codex）**，任務：讀 `data.txt` 回 alpha 值，預期 `42`，`--sandbox read-only`：

| 階段 | 結果 |
|---|---|
| probe | `response_received`，model `openai/gpt-6-luna-decisions-20261006`，id `gen-dec-1793067439-RXlhF61QpN0n3ToPYXZu`，cost $0.000013 |
| CLI run 決策回應 | `luna_decisions`，id `gen-dec-1793067592-NnJGcbhu6gWpgIJwI5bg`，選 `gpt-6-luna`，prob 0.64（舊門檻必擋） |
| CLI run worker 啟動 | `worker_started: true`，thread `01a17b28-28b9-7dd3-9082-e27abb99f11b` |
| CLI run 回合完成 | `turn_completed`，exit 0 |
| CLI run 獨立驗收 | worker 回 `42` = 預期值 ✔ |
| MCP luna_run | `luna_decisions` id `gen-dec-1793067635-b2yBcP4NK4UFdhYyXFzy`，`gpt-6-luna`／low，`turn_completed`，回 `42` ✔ |

## 限制與未驗證
- 需重啟 Codex（app／CLI）方載入新 MCP、skill 與 AGENTS.md。重啟後由 Codex 主代理自動呼叫之行為未實測。
- Luna 之 effort 仍受本地「任務複雜度下限」約束（CLI 例：Luna 選 low，因 inline 分類 complexity 2 提為 medium）。此為既有安全下限，未改。
- `codex exec` 不回報實際 wire model，`actual_model` 仍為 null。
- v2.4 即時切換目前對話模型仍未整合；本路由只控制所啟動之 worker。

## 改動檔案
`router.py`、`decision.py`、`luna.py`、`codex_backend.py`、`credentials.py`、`luna_mcp.py`、`config.json`、`skills/gpt-luna-decision/SKILL.md`、`CHANGELOG.md`、`MANIFEST.sha256`、`tests/test_{auto_routing,codex_backend,decision,openrouter_backend,required_online,auto_cli_integration}.py`；專案外：`~/.codex/AGENTS.md`（luna 區塊）、`~/.codex/config.toml`（新增受管 MCP 區塊）、`~/.codex/luna-router/`、`~/.codex/skills/gpt-luna-decision/`。
