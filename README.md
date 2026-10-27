# Luna Router — 直接由 GPT-6 Luna medium 決策

預設流程：`gpt-6-luna/medium` 原生協調代理拆分任務並直接選擇 worker → Host 記錄選擇並啟動可見 agents → `gpt-6.1-sol/high` 整合 → 獨立驗收。

Worker 僅可選 `gpt-6.1-sol`、`gpt-6.1-terra`；effort 為 `low`、`medium`、`high`、`xhigh`、`max`，並以即時 Codex 能力查詢確認。此流程不呼叫 OpenRouter Decisions，也不需要 OpenRouter API key。

`luna_decide` 接收原生 Luna 已做出的選擇與實際 coordinator agent ID；MCP 驗證並保存 `native_coordinator_host_reported` 證據，不假稱 MCP 自己呼叫模型。缺少選擇不會自動改走線上或本地規則。參閱 [完整 workflow](docs/WORKFLOW.md) 與 [專案技能](.agents/skills/luna-workflow/SKILL.md)。

`.codex/config.toml` 讓新的受信任專案對話使用 `gpt-6-luna/medium`；`config.json` 的 workflow 設定預設整合角色與併行數。這些設定不切換目前對話模型。升級後重新啟動 Codex，讓新的 MCP schema 與全域技能載入。

## 安裝與驗證

使用 `install-luna-router.ps1` 安裝 runtime、全域 skill 與 CLI，`-RegisterMcp` 可登記 MCP。執行 `python -X utf8 -m unittest discover -s tests` 與 `python verify-package.py` 驗證套件。測試中的假模型不是 live 驗證；實際交付記錄見 result08.md。

## 舊線上入口（僅明確選用時使用）

下方為保留的 v2.7 線上命令說明。`luna_run`、`luna_plan`、`probe`、`luna_decide_online`／`workflow decide-online` 仍使用 OpenRouter；其 required-online 與付費描述僅適用這些舊入口，不適用上方原生 workflow。

## Windows 安裝（Python 3.11 以上）

專案與目錄名稱使用 `luna-router`，例如 `E:\luna-router`；版本號保留在版本文件與執行收據。

解壓縮**在安裝目的地以外**的資料夾，執行：

```powershell
.\install-luna-router.ps1 -RegisterMcp
```

這次會真的安裝完整 Python 執行器，而非只複製 SKILL.md：

```text
%USERPROFILE%\.codex\luna-router\                   完整程式與設定
%USERPROFILE%\.codex\skills\gpt-luna-decision\       綁定絕對路徑的新技能
%USERPROFILE%\.codex\bin\luna.ps1                    統一呼叫入口
%USERPROFILE%\.codex\luna-router-backups\<timestamp> 舊安裝、技能及必要設定備份
```

預設會尊重已設定的 `CODEX_HOME`；未設定時才用 `%USERPROFILE%\.codex`。也可用 `-CodexHome '<真正的 Codex 設定目錄>'` 明確指定，避免裝到另一個帳號的 HOME。

`-RegisterMcp` 只新增／更新帶標記的 `[mcp_servers.luna_router_v270]` 區塊，設定工具逾時 1900 秒，不變更其他 model/provider 設定，不儲存 Key 值。沒有該參數則不修改 Codex config.toml，仍可用 CLI。

舊入口 `install-codex-skill.ps1` 已轉接完整安裝器；不再只有安裝文字。安裝後重啟 Codex／IDE，使新技能及 MCP 設定載入。其他名稱的舊 MCP 服務不會被自動刪除；使用 **luna_router_v270**，不要繼續呼叫舊服務的 `luna_plan(live:false)`。

## Key 來源

讀取 `OPENROUTER_API_KEY`。優先使用目前行程環境；Windows 讀不到時，依序唯讀查詢使用者、系統環境變數，解決已開啟的程式沒有繼承新變數的情況。只報告有無 Key 與來源，不輸出或存入金鑰值。行程中的 Key 若收到 401，且 Windows User/System 環境有不同的 Key，會以該環境值重試一次。

## 先證明 Luna，再跑一個唯讀任務

```powershell
# 唯讀環境診斷：顯示實際版本／路徑／Key 來源，不執行推理
& "$HOME\.codex\bin\luna.ps1" doctor

# 一次真正的 Luna Decisions POST；不依賴 Codex 模型查詢，不啟動 worker
& "$HOME\.codex\bin\luna.ps1" probe

# 真正線上決策後啟動唯讀 Codex worker；不需要 --online
& "$HOME\.codex\bin\luna.ps1" run `
  --prompt "只讀取此專案的 Cargo.toml，回報 package.name；不要修改任何檔案" `
  --project "E:\my-project" --sandbox read-only --output ".\luna-live-result.json"
```

`probe` 會產生一筆 OpenRouter 推理請求及可能的費用；不是離線模擬。
`doctor` 只診斷，不能被當成 probe 或 run 成功。

## Codex 工具模式

新 MCP 的三個工具：

| 工具 | 缺省 live | 行為 |
|---|---|---|
| `luna_run` | true | 真正呼叫 Luna，決策有效後啟動 worker |
| `luna_plan` | true | 真正呼叫 Luna，但僅回傳計畫 |
| `luna_status` | 不適用 | 本地診斷，不執行推理 |

```json
{
  "task": {
    "objective": "只讀取 Cargo.toml 並回報 package.name，不修改檔案",
    "kind": "coding",
    "complexity": 1,
    "risk": "low",
    "needs_tools": true,
    "data_policy": "metadata_only"
  },
  "project": "E:\\my-project",
  "sandbox": "read-only"
}
```

將以上參數交給 **luna_router_v270 的 luna_run**。沒有 `live` 會在實作中預設 true，不只是 schema 上寫一個 default。`live:false` 是明確離線要求，仍會被尊重，但不能回報 Luna 已使用。

## 外送與驗收

預設只向 Luna 外送固定分類 metadata；不發送任務原文、路徑、專案內容或未核准摘要。真正工作的 prompt 會交給 Codex 雲端 worker。`public` 才可把全文交給 OpenRouter；`no_external` 不會被強制覆蓋。

新工作不需先成功讀取公共 Decisions 目錄才能發出指定模型的 POST；固定呼叫 `openai/gpt-6-luna-decisions`，以真正 API 回應驗證可用性。候選 worker 仍要求真實 Codex 能力，沒有就回報 `no_verified_worker_candidates`，不偽造模型名單。可以先用 `probe` 分離診斷。

每次結果自動存入 `.state/receipts/`，包括：版本、實際執行路徑、Key 來源（無值）、決策來源、是否收到 Luna 回應、服務端回應 ID（若有）、worker 是否啟動／完成，以及驗收狀態。stderr 會即時輸出階段事件。

`worker_started=true` 只代表程序啟動；`worker_completed=true` 只代表回合完成。不代表任務通過獨立驗收，也不表示目前 ChatGPT 對話模型已切換。API 沒回 ID 時保持 null，不偽造。

## 兩次失敗升級保留

```powershell
& "$HOME\.codex\bin\luna.ps1" run --task task.json --project "E:\my-project" `
  --job-id fix-001 --verify-argv-file verify.json --auto-retry --max-attempts 20
```

同模型、同推理程度的獨立驗收失敗累積兩次才提高一級；切換檔位歸零。到原生最高檔仍失敗兩次才升模型。驗收指令不存在、認證、配額、網路故障不是解題失敗。沒有 Luna 來源證據的舊 job-id 在 strict mode 不可續跑；使用新的 job-id，或由使用者明確選擇本地路由。

## 故障時

- `openrouter_key_missing_online_required`：這個執行環境沒有可讀的 Key；不是離線成功。
- `no_verified_worker_candidates`：Codex 模型查詢／登入或候選篩選未通過；先看 doctor，probe 可獨立驗證 Luna。
- `luna_api_error`：查看 HTTP status。401、402、429 等都不會被包裝成本地成功，也不自動重送未知結果的付費請求。
- `luna_decision_below_threshold`：模型真的有回覆，但未達既有尚未校準的路由門檻；不假裝完成路由。可從 receipt 檢視機率，這版不暗中放寬門檻。
- 已裝新 ZIP 卻看到 2.4／2.6：舊捷徑、舊 MCP 或舊 skill 還在被用；依 doctor 的 `entrypoint` 核對。下載檔案本身不會更新執行中的程式。

## 測試與限制

```powershell
python -m unittest discover -s tests -v
python verify-package.py
```

歷史 `VALIDATION.md` 記錄 loopback HTTP 與假 Codex 測試。2026-10-27 已另行完成 Windows 真實 OpenRouter Decisions、Codex 寫檔與主機獨立驗收；來源目錄與已安裝入口皆通過，見 [result06.md](result06.md) 及 `evidence/live-smoke-verified/summary.json`、`evidence/live-smoke-installed/summary.json`。完整測試為 283 項：266 通過、17 跳過。

尚未接入 v2.4 的多回合即時切換狀態機；此版修正新任務線上決策與執行入口，不宣稱已控制 ChatGPT UI、5900X 或既有的第三方 MCP。MCP 是同步 stdio 實作，不提供背景任務或中途續接。

## 實際寫檔與獨立驗收

在可連線的 host 端執行（不是已派出的 worker）：

```powershell
python .\examples\live-smoke.py --output-dir .\evidence\live-smoke-001
```

此工具實際呼叫 `luna.py run`，在全新暫存專案寫入 `input.json`，讓 worker 計算數字總和並寫出 `result.json`。Host 端在 worker 專案之外核對精確答案與每次隨機 nonce，避免舊產物被算成成功。Decisions 明確使用 `metadata_only`，不傳任務原文或專案路徑；不使用 probe、plan、demo 或離線替代執行。

JSON 分別列出決策回應、worker 啟動、回合完成與獨立驗收四層。任一層失敗、blocked、缺產物或程序失敗都回傳非零；輸出目錄保存 receipt、產物及 summary。請每次使用新的輸出目錄。Unit tests 的 fake subprocess 僅為 fixture，不代表 live 驗收。

網路受限的 worker 可以修改授權的本機檔案，但線上 API 與 git push 可能需要 host 端已核准的執行環境。已派出的 worker 不應再次呼叫路由器；此工具會拒絕 `LUNA_ROUTER_WORKER=1`，不調整 sandbox 或安全設定。上述 smoke 必須由 host 執行，不能以本機 fixture 通過代替。
