# Luna Router v2.7.0

- 實際工作一律用本套件的 luna.py run／MCP luna_router_v270.luna_run；不要以 plan、doctor、demo 充當執行。
- auto/luna 都需要真實 Luna Decisions 成功；未明確要求離線，不可插入 live:false、--offline 或 --routing local。
- OpenRouter API／能力失敗必須回報 blocked 與實際原因；不要退回 local_policy 或其他決策模型。
- Key 只讀取環境；Windows 可唯讀讀取 User/System environment，不得列印金鑰值。
- 預設 metadata_only。不要傳專案原文、路徑、私鑰或未核准摘要到 Decisions API。
- 兩次經驗證失敗才升原生 effort，歸零計數；原生上限仍失敗兩次才升模型。不把網路、認證、額度當解題失敗。
- LUNA_ROUTER_WORKER=1 時不得遞迴呼叫路由器；直接執行已交派的工作。
- 交付要分開報決策回應、worker 啟動、回合完成、獨立驗收。假 HTTP/Codex 測試不是 live 驗收。
- v2.4 即時多回合切換仍未整合；不能宣稱已更換目前 ChatGPT 對話模型。
