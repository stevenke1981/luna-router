# 真實原生 Agent 操作證據

2026-10-27 實際執行；不是 mock Decisions 或假 worker。

1. 由 Host 建立兩個獨立任務：讀取隨機數列，分別算總和與最大值。寫入範圍分別為 `sum.json`、`maximum.json`。
2. 兩次真實 Luna Decisions 回傳 `gpt-6-luna/low`，回應 ID 保存在 `receipt.json`。快速決策未啟動 worker。
3. Host 分別呼叫原生 `spawn_agent`，取得 `/root/live_sum`、`/root/live_maximum`，登記 ID 後兩個 agent 實際讀取、計算、寫入並讀回檔案。工具動作可在原生 Host 對話中查看。
4. 使用者設定的 `/root/live_integration`，模型 `gpt-6.1-sol/high`，讀取三份輸入並寫入 `integrated.json`。它是設定的整合角色，不是第三次 Decisions 選出的 worker。
5. Host 獨立執行 Python 檢查，重新計算總和與最大值，精確比較三份輸出與 nonce。exit code 0，workflow 狀態 `completed`。新 MCP stdio 程序以 `tools/call luna_workflow_status` 讀回相同完成狀態。

結果：sum **43093**，maximum **9322**。`receipt.json` 保存決策來源、原生 ID、事件及獨立驗收 hash；四份 JSON 保存本次實際輸入與產物。Host 證據由本次工具操作支持，不是供應商簽章，也不包含隱藏思維鏈。

此範例驗證路由／平行代理／整合／驗收流程，不代表 Archiver 的 Explorer 功能已驗收。要執行自己的專案，依專案 [workflow skill](../../.agents/skills/luna-workflow/SKILL.md) 建立新 job，重新取得真實決策；不得重播本次 dispatch。
