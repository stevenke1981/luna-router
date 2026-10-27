# Luna Decisions Router / Agent Team Notes

- **Decision agent**: Luna Decisions 只選模型與努力程度，不直接執行工具；API 證據必須有 `decision_source` 與 `decision_model_called`。
- **Execution agent**: Codex / GPT worker 才能處理檔案或執行測試；free text worker 不能假稱有工具能力。不要將完整私有程式碼送到 Decisions API，只送經核准最小化摘要。
- **QA agent**: 以真實 exit code（驗證檔案指定的命令）或人工證據判斷結果；worker 自稱完成不算驗收。兩次經驗證失敗才升級，失敗計數/轉移由 SQLite 管理。
- **Safety agent**: OpenRouter 的本地美元閘門取消，key/guardrail 消費控制交給 OpenRouter；直接 OpenAI API router 才需另跑獨立費用控管（目前未實作）。配額、超時、權限、模型不可用時中止，不偽造推理級別或重送。
- **Release agent**: 執行 `python -m unittest discover -s tests`、`python router.py demo` 與 ZIP SHA256 檢查；明確區分離線 PASS 和 live NOT_RUN。

- **OpenRouter Free Agent**: `free_agents.py sync` 依完整免費目錄生成 per-model TOML。橋接 Agent 繼承 GPT parent，必須透過 `call` 執行 OpenRouter；原生 Agent 的 model key 只有 parent provider=OpenRouter 才有效。只傳 public／批准刪敏資料，僅讀取 `OPENROUTER_API_KEY` 環境變數；每次呼叫前重查免費價錢與失效日期。

- **Auto Routing v2.6**: 只有由 `router.py run`／`luna-auto.ps1` 啟動的 Codex worker 才能保證走自動路由入口；不能改變 ChatGPT 目前對話模型。`config.routing.default_mode=auto`；有 `OPENROUTER_API_KEY` 時依 metadata-only 規則嘗試 Luna，沒有 Key 則本地路由。`--offline` 完全禁止 OpenRouter 路由外送，但 Codex 本身仍是雲端工作。
- **Discovery fallback**: model/list 失敗但 ChatGPT 登入已確定、沒有 exact pin 且非最高風險時，允許以 Codex CLI 預設模型執行；回報 `selected_model=null`、`actual_model=null`、`decision_source=codex_cli_default_fallback`，不得宣稱切換到 Astra。帶 job-id 驗收升級仍要求已知模型目錄。
