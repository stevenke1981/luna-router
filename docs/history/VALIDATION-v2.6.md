# v2.6.0 new validation: [VALIDATION-V2.6.md](VALIDATION-V2.6.md)

# 驗證紀錄 — v2.3.0

日期：2026-10-26（Asia/Taipei）。製作環境：Linux / Python 3.13。

## 已驗證 PASS（不消耗模型額度）

1. `python -m unittest discover -s tests`：**185 項通過**。測試範圍包括 v2.2 OpenRouter 金額閘門分離、OpenAI direct fee guard、Decisions schema、免費模型精確零價篩選、Codex 能力處理、SQLite 請求數量、兩次失敗升級、原生 max、模型等級升級與 pin、同一 job-id 跨執行續跑、人工 QA 證據、驗收程序成功／失敗／逾時／無法執行。
2. `python router.py demo`：**7 個**離線任務範例可輸出，全部都標示 `simulation:true`、`decision_source:local_policy`、`decision_model_called:false`，沒有宣稱已叫 Luna。
3. `python router.py fee-policy`：OpenRouter／Codex `local_fee_check:false`；直接 OpenAI route 的費用模組獨立保留且目前沒有 executor。
4. `python router.py plan --task examples/task-rust.json --demo`：離線計畫是 `gpt-6.1-sol`、`medium`，**沒有執行**該 worker。
5. 跨執行路由整合測試：模擬 Sol 的 `medium×2`、`high×2`、`xhigh×2`、`max×2` 驗收失敗，才升級 `gpt-6-astra`；模擬續跑時不會重新呼叫 Luna Decisions。
6. 錯誤阻擋：假設 worker 認證或權限錯誤，狀態為 `paused`，不得計為兩次實作失敗；已鎖定模型的最高檔不得跨模型升級。

## 公開 API 目錄驗證資料

`evidence/openrouter-text-free.json`、`evidence/openrouter-decisions-luna.json` 是前一版實際公開 GET 的局部必要欄位快照，不是即時模型可用性；本次沒有再呼叫任何模型推論 API。`SOURCES.md` 列出模型目錄及官方 reasoning effort／API Key spend cap 來源。

## 未執行 NOT_RUN

| 項目 | 原因 |
|---|---|
| `openai/gpt-6-luna-decisions` 真實推論 POST | 未使用使用者憑證；離線驗證沒有付費請求 |
| OpenRouter 免費 worker 真實推論 POST | 無法從公開目錄 GET 證明實際執行品質 |
| 本機 Codex CLI 真實 `model/list` / 任務 worker | 製作環境沒有使用者的 Codex CLI、帳號權限與本地專案 |
| Windows / PowerShell 原生可執行性 | 安裝檔與參考指令已封裝，須在目標 Windows 主機驗收 |
| GPT max 的帳號實際支援程度 | 以真實 App Server 動態發現，這裡僅覆蓋模擬 model/list 情境 |
| 自動修復實際工程成功率 | 無真實工程專案／benchmark，不能把模擬測試當成修好程式 |

**安全界線：**自動重試僅用於使用者明確指定 `run --auto-retry --verify-argv-file` 的任務。指令可修改專案，請在版本控制的工作樹、沙盒內使用。已正常結束回合但沒有 verifier 時，狀態必須是 `needs_verification`；timeout/認證/網路問題不得盲目重試。
