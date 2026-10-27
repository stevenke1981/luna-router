# v2.7.0 驗證紀錄

日期：2026-10-27。原始輸入：本對話的 `luna-router-v2.6.0.zip`。

## 已完成且有執行紀錄

| 檢查 | 結果 | 證據 |
|---|---|---|
| 完整回歸測試 | **263／263 PASS** | `evidence/tests-v2.7.txt` |
| 最後安裝器預設路徑修訂後的新增測試重跑 | **28／28 PASS**，為上述測試子集，不另加總 | `evidence/tests-v2.7-focused.txt` |
| HTTP→決策→worker 執行流程 | PASS，loopback HTTP 及假 Codex 程序 | `tests/test_required_online.py` |
| 不加 --online 仍真正發送 HTTP POST | PASS，在測試伺服器驗證 | 同上 |
| 缺 Key、401／402／404／429／503 不退回離線成功 | PASS，不啟動 worker | 同上 |
| 單一候選、指定模型仍走決策 | PASS | 同上 |
| 每個新工作不以快取冒充新呼叫 | PASS | 同上 |
| plan 不執行；luna.py 無子命令預設 run | PASS | 同上 |
| MCP 的 live 預設 true；luna_run 與 luna_plan 分離 | PASS，含 stdio initialize/tools/list | 同上 |
| Windows 系統環境變數 fallback | PASS，mock winreg；非 Windows 原生測試 | 同上 |
| 安裝完整引擎、技能絕對路徑、跨目錄啟動 | PASS，Linux 暫存安裝目錄 | 同上 |
| MCP 設定可重複安裝且保留既有 model／其他設定 | PASS | 同上 |
| 兩次驗收失敗提升 effort，升級後重新計數 | PASS，包含跨次續跑不重叫 Luna | 同上與既有 escalation 測試 |

完整測試用 Python 3.13.5。測試 API Key 均為假值；沒有付費推論。

## 真實環境檢查

本次真的執行過 `luna.py doctor`：Key 不存在、Codex CLI 不存在；見
`evidence/doctor-v2.7-build-environment.json`。

本次真的執行過 `luna.py probe`：回傳 `openrouter_key_missing_online_required`、exit code 2、
`decision_call_attempted=false`，沒有送出推理請求；見
`evidence/probe-v2.7-build-environment.json`。

- **真實 OpenRouter Luna Decisions 推理：NOT_RUN**。
- **真實 Codex 推理／模型切換：NOT_RUN**。
- **5900X 安裝／Windows PowerShell 原生驗收：NOT_RUN**。
- **目前 ChatGPT 對話模型切換：未執行，也不由此程式控制**。
- **v2.4 多回合即時切換：仍未接入**。

這些狀態不會因 mock PASS、模型目錄或安裝檔存在而改寫為成功。

## 原包基線

重跑原封 v2.6.0：235 項中有 1 項失敗。原因是免費 Agent 安裝測試用未排序 glob 選第一個 TOML，
有時拿到 native 版卻以 bridge 版規則斷言。此版明確排除 `_native`，沒有刪除此測試。

## 發行檔案

122 個 manifest 項目。`MANIFEST.sha256` 列出檔案校驗；ZIP 的 SHA-256 放在 ZIP 旁的 `.sha256` 檔。
封裝排除 `.state`、`__pycache__`、`.pyc`；無真實 Key。可用 `python verify-package.py` 驗證。

安裝腳本現在部署完整程式，並綁定新版技能；`-RegisterMcp` 可新增新命名空間。
僅下載或解壓 ZIP 不會改變使用者既有的舊服務與已載入的舊技能。
