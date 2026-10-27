# 可見 Luna Workflow 交付紀錄

日期：2026-10-27。正式工作目錄：`E:\luna-router`；公開儲存庫：`stevenke1981/luna-router`。

## 已實作

- 快速 Decisions 與實際派工分離：新增七個 MCP workflow 工具及等價 CLI。舊同步 `luna_run` 保留相容。
- 設定的 Luna coordinator → 真實 Decisions 逐項選擇 worker → Host 原生可見 agents → 可更換 Sol high integrator → 獨立驗收。
- SQLite 持久化工作與事件，任務依賴、寫入範圍衝突及併行上限、原子派工與驗證 claim，結果不明時不重播。
- 移除單一決策 blocked 就停止所有工作的指令；HTTP 500/502/503/504 最多三次嘗試，其他獨立分支可繼續。
- 專案 `.agents/skills/luna-workflow/SKILL.md`、AGENTS 指引、可安裝的 decision skill、專案 `.codex/config.toml` 與 workflow 設定。
- 一次獨立整合檢查發現並修正：回傳未清理 plan、驗證工具失敗未持久化、已完成 job 可能再執行驗證命令。

## 驗證

| 項目 | 結果與範圍 |
| --- | --- |
| 完整 unittest | PASS：321 項，304 通過、17 跳過，17.650 秒；跳過項目不是本次 live 證據 |
| Workflow 受影響測試 | PASS：33 項，涵蓋原子 claim、持久化、狀態門檻、清理回應、真實本機 verifier |
| 兩份 skill 結構 | PASS：skill-creator quick_validate |
| Luna 實讀 skill 操作測試 | 初次找出事件欄位與 outcome 不明確；補齊 JSON／enum／失敗操作後重測 PASS |
| 真實 Decisions | PASS：兩次實際成功回應，均選 `gpt-6-luna/low`；不是離線或本地 fallback |
| 原生 worker | PASS：兩個實際 agent 各自讀、寫、回讀指定檔案 |
| 原生整合 | PASS：`gpt-6.1-sol/high` 獨立 native agent 整合三份輸入 |
| 獨立驗收 | PASS：實際 Python 命令 exit 0，job `completed` |
| MCP 新程序 | PASS：真實 stdio `tools/call` 讀回已完成 job |
| 已安裝 runtime 與 skill | PASS：165 檔 manifest、核心模組與 source hash 一致、skill 路徑已綁定 |
| 安裝後 MCP smoke | PASS：新程序列出七個 workflow 工具、建立並跨程序讀回 job；此項不呼叫模型、不啟動 worker |

完整範例與收據在 [evidence/native-workflow](evidence/native-workflow/README.md)。早期真實 API 502 未當成成功；獨立實作持續，恢復後才派送上述 worker。設定角色不是 fallback worker。

已安裝到 `%USERPROFILE%\.codex\luna-router`，並更新 `%USERPROFILE%\.codex\skills\gpt-luna-decision\SKILL.md`。全域 AGENTS 只替換 Luna 管理區塊，保留其他規則與原檔備份；不修改全域模型或安全設定。安裝 smoke 執行時 manifest 為 165 檔，後續加入此安裝收據與文字後重新產生 manifest 並同步安裝。

## 邊界

原生 UI 及工具動作由 Host 提供，MCP 不會自行建立 UI agent，也不會切換現有對話模型。寫入範圍與併行上限在單一 workflow 內協調，不是 OS sandbox 或跨 job 鎖。新 MCP 工具與新專案模型預設須由新 Host 工作階段載入；目前對話的工具清單不會被宣稱已熱更新。

Archiver Explorer GUI 驗收、任意複雜專案端到端驗收：NOT_RUN；本次只驗證 Luna workflow 本身。舊版本目錄因原有程序占用而保留；正式開發與交付使用不帶版本號的 `E:\luna-router`。
