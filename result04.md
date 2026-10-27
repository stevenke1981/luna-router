# 2026-10-27 — 修正 Codex 內 luna_run「一直等不到回應」與 worker 遞迴

## 實際情況（依收據與 worker session 判定）
- 收據 `~/.codex/luna-router/.state/receipts/dfe91246….json`：Luna 決策成功（`gen-dec-1793068551-l1O9VSWGeL81skmsUGWm`，`gpt-6-astra`／low），worker 已啟動並 `turn_completed`，10:36:21 即完成。路由器本身沒有卡住。
- 有兩個問題：

### 1. 上層 Codex 讀不懂回應，因此一直等
- Windows 上 MCP 的 stdin／stdout 預設編碼是 cp950。worker 回的是中文訊息，MCP 以 cp950 寫出，Codex 以 UTF-8 解讀失敗，這一行回應被丟棄，呼叫就一直掛著。
- 實證：在 pipe 中寫出中文 JSON 後以 UTF-8 解碼 → `UnicodeDecodeError ... byte 0xa5`。
- 先前測試沒抓到，是因為回傳的是 ASCII 的 `42`。
- 修正（`luna_mcp.py`）：stdin／stdout 改為 UTF-8，回應一律輸出純 ASCII JSON（`\uXXXX` 跳脫），stderr 改用 `backslashreplace`，以免遇到無法編碼的字元而崩潰。`luna.py` 的 stderr 也比照處理。

### 2. worker 又回頭呼叫路由器
- worker（另一個 Codex）讀到全域 `AGENTS.md` 的「第一步呼叫 `luna_run`」，於是又去呼叫路由器。這次呼叫被核准政策（`approval_policy never`）拒絕，worker 便放棄並回報 NOT_RUN。`LUNA_ROUTER_WORKER=1` 是環境變數，模型看不到。
- 修正：
  - `router.WORKER_HEADER`：派給 worker 的任務前加上 `[LUNA ROUTER WORKER] … do the task directly. Do not call luna_run …`。
  - `codex_backend._router_mcp_disable()`：`config.toml` 中有登錄 `luna_router_v270` 時，worker 加上 `-c mcp_servers.luna_router_v270.enabled=false`。實測：該 server 未定義時，此覆寫會讓 Codex 啟動失敗（`invalid transport`），所以只在有登錄時才加。
  - 全域 `~/.codex/AGENTS.md`、SKILL.md、MCP 工具描述加註：見到 `[LUNA ROUTER WORKER]` 就直接做事；`luna_run` 是同步呼叫，可能要數十分鐘，須持續等待（1900 秒逾時）。

## 驗證
- `python -m unittest discover -s tests -q` → 275 OK（17 skipped）。新增測試：MCP 對中文結果輸出純 ASCII 並能讀入 UTF-8 輸入；停用覆寫只在已登錄時才加；worker 任務帶有標頭。
- `verify-package.py` PASS；已重新安裝（備份 `~/.codex/luna-router-backups/20261027T024452Z-dad14c`）。
- Live（模擬 Archiver 的情境：中文任務、complexity 4 → 單一候選，以 MCP 協定經 UTF-8 bytes 呼叫）：
  - 決策：`luna_decisions`，id `gen-dec-1793069108-8TebE1zSSvG0WuQWYJRf`，`gpt-6-astra`／low
  - worker：`turn_completed`，thread `01a17b3f-4b4b-7c32-9e61-f79483b8783c`；session 中只有一次讀檔工具呼叫，沒有呼叫路由器
  - 回應每一行皆為 ASCII，耗時 12 秒，解出「alpha 的值是 42。」✔

## 尚待
- 未在 Codex app 內重跑 Archiver 原任務。**請先重啟 Codex**（讓新的全域 AGENTS.md 生效，並重新啟動 MCP server），再重跑。
- Archiver 是實際修復工作，worker 可能要跑很久；`luna_run` 要等 worker 做完才回傳。
