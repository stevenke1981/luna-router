# Native Luna medium coordinator routing (2026-10-28)

- Native coordinator preferences: Terra for computer use; Sol for code and difficult problems.
- The default native workflow now records a real Host Luna/medium coordinator selection directly, without calling OpenRouter Decisions.
- Restrict native worker choices to GPT-6.1 Sol/Terra and low, medium, high, xhigh, max, intersected with live Codex model capabilities.
- Persist coordinator agent identity and truthful Host-reported provenance; preserve atomic claims and independent verification.
- Move API-based decision registration to explicit legacy decide-online; update project/global skills and fresh-session defaults.

# v2.7.0 patch — Instant Luna dispatch（2026-10-27）

- Live fix: OpenRouter returns the dated snapshot `openai/gpt-6-luna-decisions-20261006`; the exact-id check rejected it as `decision_model_mismatch`, blocking every live run. Responses now accept the configured id or `<id>-YYYYMMDD`.
- Windows: `codex` resolving to npm's `codex.cmd` shim made discovery fail (`codex_unavailable`) outside the Codex app; the backend now uses `CODEX_CLI_PATH` or the bundled native `codex.exe`.
- MCP hang fix: on Windows the MCP stdout used cp950, so any non-ASCII result (e.g. a Chinese worker message) was unreadable to Codex and the call never returned. stdin/stdout are now UTF-8 and responses are ASCII-only JSON.
- Worker recursion fix: the dispatched worker read the global AGENTS.md rule "call luna_run first" and tried to route again, was blocked by approval, and quit with NOT_RUN. Workers now get a `[LUNA ROUTER WORKER]` header and, when the server is registered, `-c mcp_servers.luna_router_v270.enabled=false`.
- HTTP 400 fix: high-risk or complexity-4 tasks leave only `gpt-6-astra`, and the live Decisions API rejects a one-option choice ("needs at least 2 choices"). Single-option questions are now omitted, so Luna then decides effort only; the lone option is recorded with probability 1.
- Stale key: the Codex app keeps the OPENROUTER_API_KEY it started with and forwards it to MCP; after a key rotation every call got HTTP 401. On a 401 with a process key, the request is retried once with the live Windows User/System value when it differs (401 = not processed, no double charge). Values are never printed or stored.
- A valid Luna Decisions answer is now used immediately, even below the uncalibrated 0.95/0.30/0.80 certainty gate; the run records `luna_low_certainty_accepted` instead of blocking for human review. Restore the old gate with `policy.block_low_certainty=true`.
- Luna also owns reasoning effort: its choice is used as-is (only moved to the nearest level the chosen model supports), no longer raised to the local complexity floor. Restore the floor with `policy.local_effort_floor=true`. Offline/local routing keeps the floor.
- Codex workers run with explicit `approval_policy="never"` and `--skip-git-repo-check` (sandbox unchanged), so unattended turns neither pause for approval nor fail with "Not inside a trusted directory" in non-git project folders.
- Skill and MCP tool text: call `luna_run` first without asking, no `luna_plan` pre-pass, no extra review rounds; removed the "first task must be read-only" rule.
- Tests: `LUNA_ROUTER_IGNORE_REGISTRY_KEY=1` lets isolated tests ignore a key stored in the Windows registry (previously 3 tests failed and one installer test could launch a live run on machines with a registry key); UTF-8 read fix for cp950 locales.

# v2.7.0 — Required Online / Bound Execution

- New default: real Luna success is required for auto/luna. Missing key, invalid responses, low confidence and transport/API failure are errors, not local fallback.
- Single candidate and pinned workers no longer skip the strict online decision. New jobs bypass response cache; genuine Luna retry provenance is persisted separately.
- `luna.py` defaults to run. New `probe` sends exactly one live POST without depending on Codex discovery. No local USD gates added.
- Read Windows User/System environment if the process has no OPENROUTER_API_KEY. Never store or print values.
- Full installer binds the updated skill to the installed engine; backups, manifest validation, optional isolated MCP configuration. Added real luna_run/luna_plan MCP tools with live=true implemented server-side.
- Persist receipts on blocked/successful paths. Surface response IDs when provided and worker process start evidence. Block recursive worker routing.
- Fixed free-agent installation dependency list and a pre-existing nondeterministic test that accidentally selected a native TOML instead of a bridge TOML.
- Previous pure offline helpers remain for explicit local routes/demos. Current ChatGPT UI/in-flight dynamic switching are outside this patch.

## Historical releases below

# v2.6.0 — Auto Routing 預設模式（2026-10-27）

- `config.routing.default_mode=auto`；`run` 預設自動路由並啟動 worker，`plan` 預設自動路由但不啟動。`--online` 改為可選相容別名，新增 `--routing auto|local|luna`、`--offline`。
- 有 `OPENROUTER_API_KEY` 且允許外送時，自動嘗試 Luna Decisions；預設只傳固定任務分類 metadata，不需傳未經核准的 summary／原文。未有 Key 改本地路由。
- `model/list` 查詢失敗保留安全錯誤碼；對非最高風險、未釘選模型、已確認 ChatGPT 登入的工作，允許 Codex **預設模型** CLI 執行，不假冒 Astra 或 effort 切換。指定模型／高風險仍阻擋。
- 同一輪工作採用新鮮 model/list 快照，減少執行前重複查詢失敗；原生 CLI 仍不提供可驗證的實際 wire model，因此 `actual_model=null`。
- 新增 `--prompt`／`--prompt-file`／`--stdin`、`luna-auto.ps1`、`doctor`、`README-AUTO-ROUTING.md` 與跨程序 fake CLI 整合測試。
- 原 OpenRouter 費用政策、免費模型 0 價安全閘、兩次驗證失敗升級、v2.4 動態狀態機原型均保留。未作全域 Codex 或 ChatGPT UI 模型修改。
- 測試細節與限制見 `VALIDATION-V2.6.md`。

# v2.5.0 — Dynamic Free Agents（2026-10-27）

- 新增 free_agents.py：讀取 OpenRouter `output_modalities=all`，即時判斷 free ID + 全欄位零價格，按 text/decisions/embeddings/rerank/speech 產生每模型橋接 agent。
- 文字模型另外產生 native Codex TOML；但不能從 custom agent 覆寫 parent provider，需 root session 已選 OpenRouter。
- 新增 `codex-run` 建立一次性 OpenRouter provider 子 Codex（預設 read-only，stdin 輸入，不能假稱真實執行驗證）。
- 新增 Windows 同步/安裝/呼叫 PowerShell 腳本、Codex skill、免重複編輯的 Agent 目錄。
- 原路由器可自動擴充最多16個 catalog free 候選，或以 `requested_model` 指定當時目錄中任何免費文字模型。
- Key 只讀 OPENROUTER_API_KEY，不寫入套件，OpenRouter USD 本地預算不恢復；免費 worker 仍不自動換付費。
- 既有 v2.4 runtime controller 為獨立原型，尚未與 Codex event loop 整合。

# 版本紀錄

## v2.3.0 — 2026-10-26

- 基於 v2.2 的 OpenRouter 自行控制消耗費用模式；OpenRouter 決策不檢查本地 USD 上限，OpenAI direct 費用模組獨立保留（目前不實作 direct executor）。
- 新增 SQLite 永續驗收紀錄與兩次實證失敗升級：同模型提高 `low → medium → high → xhigh → max`，達到有效最高檔才升下一個可用的 `free → luna → terra → sol → astra` 等級。
- 新增 `run --job-id --verify-argv-file --auto-retry --max-attempts`、`retry-status`、`feedback --evidence-file`；沒有獨立驗收證據時不計入失敗、環境阻塞不升級。
- `policy.max_effort` 預設改為 `max`，**只在實際 runtime 支援 max 時使用**。舊的 `implementation_failures` metadata 不再觸發一次失敗就提高 effort。
- 續跑跳過新的 Luna Decisions API 推理，保留 `decision_source=retry_state` 證據；模型即時能力仍會驗證。
- 每 OpenRouter job 的非金額請求上限從 2 增為 3，容許一次決策 + 兩次免費文字 worker 嘗試。仍保留全域每分鐘與每日次數控制。
- 新增 Agent SKILL、Windows 安裝參考指引、驗收範例、端到端模擬測試及 SHA256 manifest。
- 此版離線測試通過，沒有呼叫付費決策或真實 Codex 實作；詳見 `VALIDATION.md`。

## v2.2.0（基礎功能）

- OpenRouter 管理 API Key spend limit/guardrails；移除本地決策費用保留／每日美元上限；新增唯讀 `spend-status`。
- 提供獨立 `cost_control.py` 僅供未來 OpenAI direct router 使用；保留免費 worker 的零價安全檢查。

## v2.1.0

- 決策模型從 Mercury 改為 `openai/gpt-6-luna-decisions`；保留 GPT 工作模型與免費文字 worker 池。
- 舊版曾實作本地美元預算保留；v2.2 開始 OpenRouter 已不再使用該費用閘門。

## v2.0.0

- 首次提供決策路由、Codex worker 與免費文字 worker 的參考實作。
# Windows CLI and live acceptance follow-up — 2026-10-27

- Force UTF-8 for the `luna.py` CLI pipes so non-CP950 worker replies do not crash completed runs.
- Add an actual `run` smoke example with random inputs, per-run nonce, four separate evidence layers and independent output verification; accept PowerShell UTF-8 BOM files and reject stale evidence directories.
- Isolate host test fixtures from the worker recursion marker and avoid recursive fixture copies when TEMP is inside the checkout.
- Exclude Git metadata, temporary workspaces, virtual environments and local environment files from installation.
- Use the unversioned project/repository directory name `luna-router`.
