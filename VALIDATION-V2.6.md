# v2.6.0 Auto Routing release verification — 2026-10-27

## 實作完成

- `config.routing.default_mode` 設為 `auto`；`router.py run`／`plan` 不再需要 `--online`，有 Key 時嘗試 Luna，無 Key 時選本地策略。
- 用 `automatic_metadata_state()` 製造固定的類別／複雜度／風險等 metadata，不把 `objective`、使用者 `summary` 或專案路徑偷渡給 Luna；沒有竄改 `summary_approved` 旗標。
- 新增 `luna-auto.ps1`、`doctor`、`--prompt`／`--prompt-file`／`--stdin`、`--routing`／`--offline`，以及分類規則的覆寫選項。
- `model/list` 故障時保留安全診斷碼；由 `codex login status` 證實 ChatGPT 已登入，且任務未指定模型且非最高風險，允許 `codex exec` 使用 Codex **預設模型**；不宣稱使用 Astra／努力程度。
- 成功取回 `model/list` 時，當次已驗證能力資料 90 秒內重用，避免 Codex worker 前重查故障；仍不把目錄會員資格與模型實際切換相提並論。
- OpenRouter Key／免費模型機制保留 v2.5 的零價驗證及請求次數保護；既有兩次獨立驗證失敗升級規則保留。

## 本地測試

執行環境：Linux、Python 3.13；Windows 原生尚未在此執行。

```
python -m unittest discover -s tests -q
---------------------------------------------------------------------
Ran 235 tests in 16.789s

OK
```

其中新測試包含：
- 沒有 Key 時直接 `run` 執行模擬 Codex worker；有 Key 時無須 `--online` 即使用 Luna mock。
- `no_external`、`--offline`、指定模型及最高風險不會錯誤走 OpenRouter／未驗證模型備援。
- Codex `app-server` 故障的**跨程序假 CLI**：真的啟動 `codex exec` 預設模型路徑，完成 JSONL 模擬回合，且不虛報 model／effort。
- Codex `app-server` 正常的跨程序假 CLI：選 `gpt-6.1-sol`／`medium`，一次模型清單查詢，使用明確 `--model` 和 `model_reasoning_effort` 交給 worker。
- `model/list` 錯誤碼可追查、Key 不會寫入任何 log 或輸出、原有 2 次失敗升級與 Free Agents 不回歸。

## NOT_RUN / Limitations

- 真正 OpenRouter Luna Decisions API 推理：NOT_RUN（沒有使用使用者的 Key）。
- 真正 OpenRouter 免費 worker 推理：NOT_RUN。
- 真正 Windows / 5900X Codex 執行、帳號可用模型驗證：NOT_RUN。
- Windows PowerShell 腳本本機原生驗證：NOT_RUN（目前環境沒有 PowerShell）。
- ChatGPT 對話／Codex 桌面 UI 全域自動改模型：**不支援**。
- 多回合執行中的 v2.4 Runtime 動態 effort 調整：尚未接進 worker。

**驗證標準：** 只把真正呼叫且已觀察到 turn.completed 的 worker 算作「完成模型回合」，不把它當作工作驗收通過；`actual_model`／`actual_effort` 缺少可靠證據時必須是 null。
