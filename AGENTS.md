# Luna Router v2.7.0

- 開始前讀取並使用專案技能 `.agents/skills/luna-workflow/SKILL.md`；內含完整 MCP／CLI 請求、原生 agent 派送、事件、整合及驗收操作。設定角色與併行數讀取 `config.json` 的 `workflow` 區段。

- 優先使用可見的原生多代理流程：Luna 協調、Decisions 逐項選擇 worker 模型與 effort、原生 subagents 派工，最後由可設定的 gpt-6.1-sol/high 整合。同步 luna_run 保留為相容入口，不再強制所有工作先等完整 worker 回合。
- auto/luna 都需要真實 Luna Decisions 成功；未明確要求離線，不可插入 live:false、--offline 或 --routing local。
- OpenRouter API／能力失敗要記錄該決策的 blocked 與實際原因，但不停止不依賴該決策的規格、診斷、修復與測試。暫時性決策服務失敗只做有上限的重試；認證、額度、無效回應不盲試，不重播已啟動或結果不明的 worker，不退回 local_policy 冒充成功。
- Key 只讀取環境；Windows 可唯讀讀取 User/System environment，不得列印金鑰值。
- 預設 metadata_only。不要傳專案原文、路徑、私鑰或未核准摘要到 Decisions API。
- 兩次經驗證失敗才升原生 effort，歸零計數；原生上限仍失敗兩次才升模型。不把網路、認證、額度當解題失敗。
- LUNA_ROUTER_WORKER=1 時不得遞迴呼叫路由器；直接執行已交派的工作。
- 交付要分開報決策回應、worker 啟動、回合完成、獨立驗收。假 HTTP/Codex 測試不是 live 驗收。
- v2.4 即時多回合切換仍未整合；不能宣稱已更換目前 ChatGPT 對話模型。
