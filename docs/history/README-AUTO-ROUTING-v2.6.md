# Luna Router v2.6.0 — Auto Routing 預設工作模式

本版將原先「路由計畫／額外加 `--online` 才決策」改成**從路由器啟動的任務預設自動選模型、設定 effort 並啟動 worker**。Codex CLI／PowerShell 呼叫器是實際進入點；**不會自動改變已開啟的 ChatGPT 對話模型、Codex 桌面版內未經此入口的對話，或其他未經啟動器執行的工作**。

## 0. 目標與原則

- `config.json` 的 `routing.default_mode` 預設為 `auto`。`router.py plan` 自動規劃，`router.py run` 自動規劃 **並執行**（除非缺少可用／合格的工作後端）。不再需要 `--online`。
- **環境變數有 `OPENROUTER_API_KEY`**、任務允許 OpenRouter 且存在合格候選時，**自動嘗試** `openai/gpt-6-luna-decisions` 的 Decisions API；此模型可能產生 OpenRouter 費用，消耗上限由 OpenRouter API Key 管理。若 Luna 不可用、回傳低信心或 request limit，落回本地路由並保留原因，不謊稱 Luna 已使用。
- 沒有 API Key、`--routing local`、`--offline` 或 `data_policy: "no_external"`，**不呼叫 Luna**；若有適合的 Codex worker，就執行它。`no_external` 只限制**傳往 OpenRouter**的任務資料；Codex CLI 執行仍會把工作傳給 Codex 所屬的 OpenAI 服務，並不是本機離線模型。
- 預設 `metadata_only`：可自動傳送**固定且不含使用者原文**的分類欄位（`kind`／`complexity`／`risk`／`needs_tools`／token 預估等）給 Luna；`summary_approved` 不會被程式偽造為 true。已有使用者明確核准的 `summary` 才會使用原有摘要傳送路徑。`public` 明確允許傳送任務原文，**僅用於公開資料**。
- 保留 OpenRouter 免費文字 worker 的零價驗證。免費模型不具備本機執行工具的權限；只有公開、文字、風險低且無工具需求的任務可自動交給它們。
- 不寫入全域 `config.toml`，也不改變目前 ChatGPT 模型選擇。

## 1. Windows 快速啟動

將 ZIP 解壓至任何資料夾，Windows PowerShell：

```powershell
cd 'E:\tools\luna-router-v2.6.0'

# 診斷：只檢查環境、Codex 登入與模型清單，不執行模型推論。
python .\router.py doctor

# Auto Routing：直接在專案中啟動 worker。沒有 --online 參數。
.\luna-auto.ps1 -Prompt '修復 Rust Slint 專案的 UI 狀態同步錯誤，執行測試並報告結果' -Project 'E:\my-project'

# 也可直接使用 Python CLI（包括 Windows / Linux / macOS）
python .\router.py run --prompt '修復 Rust Slint 專案的 UI 狀態同步錯誤' --project 'E:\my-project'
```

`luna-auto.ps1` 只會從本發行包呼叫 `router.py`，不編輯 Codex 全域設定。如系統的 PowerShell 執行政策阻擋腳本，可以直接使用上面的 `python router.py run`。

可指定 `--task` JSON、`--prompt-file` 路徑或 `--stdin`，避免把敏感文字放在命令列引數中。不要把 API Key 放入命令列、JSON、Git 或報告檔。

### 系統 API Key

```powershell
# 僅確認目前 PowerShell 行程是否可讀，不顯示金鑰
[bool]$env:OPENROUTER_API_KEY
```

若先前才新增系統環境變數，請重新啟動 PowerShell 或 Codex 執行環境。**只在 API Key 可見且任務容許 OpenRouter 時**才自動嘗試 Luna 決策。沒有 Key 仍可走 ChatGPT 登入的 Codex；不需要為 OpenRouter 補一個本地 USD 費用閘門。

## 2. 路由行為

| 條件 | 自動行為 | 如實回報 |
|---|---|---|
| 有 Key + 合格候選 + metadata_only（預設） | 送分類**而非工作內容**給 Luna；接受有效決策或退回 local fallback | `decision_source=luna_decisions / luna_decisions_cache / local_fallback` |
| 無 Key | 不嘗試付費決策，改用本地規則 | `local_policy`；`decision_model_called=false` |
| `--offline` / `--routing local` | 不傳任務給 OpenRouter | `local_policy` |
| 使用者指定模型 | 不重新選不同模型，受模型清單／effort 支援檢查 | `explicit_model` 或 `blocked` |
| `model/list` 查詢失敗，但可確認 ChatGPT 登入且未鎖模型、非最高風險 | 不偽造模型清單，實際啟動 **Codex CLI 預設模型** | `codex_cli_default_fallback`，`selected_model=null`／`requested_effort=null` |
| `model/list` 失敗而任務風險高／指定模型 | 不偷偷改用其他模型 | `blocked`，顯示安全的診斷碼 |
| 模型回合完成但無獨立驗收 | 不宣稱問題修好了 | `acceptance=not_independently_verified` |

若 App Server 查詢成功，使用當次的 `model/list` 能力結果（90 秒內）配置 worker，**避免同一輪任務再次重查造成單點失敗**。這只是一次有效的能力快照，不是帳號 entitlement、模型實際套用或模型輸出品質的保證。`run` 的輸出含 `requested_model`、`requested_effort`、`execution.status`；若原生事件無法確認實際 wire model，`actual_model=null` 且 `model_switch_verified=false`。

## 3. 控制模式與外送資料

```powershell
# 全本地路由，不呼叫 OpenRouter，但 Codex worker 本身是雲端服務
python router.py run --prompt '修復程式並測試' --project 'E:\repo' --offline

# 強制優先嘗試 Luna 路由（向下相容舊版 --online）
python router.py run --prompt '修復程式並測試' --project 'E:\repo' --routing luna

# 公開、可外送的純文字任務可用 OpenRouter Free Worker
python router.py run --prompt 'Summarize this public article: ...' --text-only --data-policy public --project '.'

# 預覽而不執行：預設仍是 Auto Routing；有 Key 時可能使用付費 Luna
python router.py plan --prompt '分析架構設計' --project 'E:\repo'
```

`--routing` 支援 `auto`（預設）、`luna`、`local`；`--online` 是相容別名；`--offline` 禁止 OpenRouter 路由請求。`--routing local` 禁止免費 OpenRouter worker 執行。

以 `--prompt` 輸入時使用**明確標示為 local heuristic**的規則做初步 task kind 分類，僅為合適候選的初步篩選；可以用 `--kind`、`--complexity`、`--risk` 覆寫。Luna 可以選合格候選，但不會突破本地能力與安全約束。

## 4. 兩次失敗升級

既有功能保持不變。自動路由與「驗收失敗才升級」是不同層次：**預設自動選 worker 不代表不經驗收就自動無限重試**。

```powershell
python router.py run --task examples/task-rust.json `
  --project 'E:\repo' --job-id fix-001 `
  --verify-argv-file examples/verify-unittest.json `
  --auto-retry --max-attempts 20 --output result.json
python router.py retry-status --job-id fix-001
```

在兩次獨立驗證的解題失敗後，按 native supported efforts 升級；到該模型最高支援值後再向上換模型。缺少驗收資料、認證錯誤、配額或網路失敗不會當作解題失敗。不通過原生模型清單時，無法安全執行跨模型自動升級，因此上述帶 job-id 的流程會 `blocked`，而不是假裝使用指定的 GPT。

## 5. 診斷

```powershell
python router.py doctor
python router.py discover --output discovered-models.json
python router.py demo
python -m unittest discover -s tests -q
python verify-package.py
```

`doctor` 是唯讀、**零推理請求**的本地診斷：Codex CLI 是否存在、ChatGPT 登入狀態、`model/list` 是否可讀、是否具備安全的預設 CLI 備援，以及 OpenRouter Key 是否存在（布林值，**不輸出 Key**）。若執行 `run` 而 OpenRouter 無 Key，報告會標示「本地路由」，而不是宣稱使用 Luna。

## 6. 目前技術界限

1. **只會自動控制透過 `router.py run`／`luna-auto.ps1` 啟動的 Codex worker**。無法由本地程式切換已存在的 ChatGPT 文字對話、Codex 桌面 UI 或不走路由器的 Codex 工作。
2. 真實帳號可用模型、App Server 相容性、Windows 原生 CLI 實作仍需要該電腦上的 end-to-end 驗收；本發行版測試沒有執行付費 Decisions API 或真實 Codex 推理。
3. v2.4 `runtime_controller.py` 保留為純狀態機，**目前尚未接入執行中多回合 Codex 切換**。Auto Routing 目前作用在**任務初始路由／每次續跑的安全檢查點**；不代表在單次推理過程中改變模型。
4. OpenRouter Key 的付費控制在服務端配置；本地 request-count guardrail 仍在，不是本地 USD 計費門檻。
5. 如需真正不連網的本機 LLM，應另建本機推理後端；這版 Codex fallback 是 ChatGPT 訂閱的雲端 Codex，不是離線推理。

完整既有功能、免費 Agent 入口見 `README.md` 與 `README-FREE-AGENTS.md`。
