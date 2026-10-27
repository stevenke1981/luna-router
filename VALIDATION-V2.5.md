# Luna Router v2.5.0 — 驗收記錄（2026-10-27）

## 已執行（PASS）

- 延續 v2.4 的 195 項測試，新增 21 項 Free Agent 目錄、生成、安裝、呼叫型別、隱私、子 Codex 隔離與路由測試，共 216 項離線測試通過。
- OpenRouter 原有歷史目錄快照（2026-10-26）生成 18 個免費文字或 `openrouter/free` 入口，對應 18 個橋接角色和 18 個原生文字角色，共 36 個 Codex TOML。
- 模擬 `text`／`decisions`／`embeddings`／`rerank`／`speech` 五種輸出類型，能在測試目錄分別生成橋接角色，並只為 text 生成原生角色。
- 每次免費模型呼叫前重新檢查目錄模型是否仍存在、全欄位零價、未過期、模態相符；過期/非零價/未知模型拒絕。
- 模擬 Codex 安裝場景，使用已安裝路徑的 CLI 載入登錄檔而非讀取 release 原始路徑。
- 路由器既有 7 項 demo 操作執行，沒有啟動付費推理。
- 原路由器 `build_candidates` 對 public/low-risk/no-tools 可增加最多 16 個自動探索的 text free 候選；明確指定模型可在 shortlist 外選取；私有任務不會新增自由候選。
- ZIP SHA-256 manifest 逐檔驗證及 zipfile CRC 驗證（產物封裝後執行）。

## 未執行（NOT_RUN）

- 制作環境對 OpenRouter API 進行完整 live `GET /api/v1/models?output_modalities=all` 連線失敗（網路受限）。因此當前發行包內的 18 模型是有來源的**歷史快照，不是 2026-10-27 的完整免費模型總數**。Windows 使用者可執行 `python free_agents.py sync` 取得即時所有模態。
- 沒有 OpenRouter API Key，**沒有**呼叫任何免費文字/Decision/Embedding/Rerank/Speech 推理 API；端點格式只依官方公開文件及 mock 驗證。
- 制作環境沒有 `codex` CLI，故未在 Windows 實測 TOML agent spawn、provider override 與 Codex Responses 工具鏈。CLI 子執行模式與網路 sandbox 支援仍由本機版本決定。
- 未部署到使用者 5900X；未修改其全域 Codex 設定。

## 重要限制

- Codex 自訂 Agent 不能個別覆寫 parent `model_provider`。每個免費模型的 `orfree_*.toml` 是**GPT parent 的橋接角色**；額外 `_native.toml` 僅供 root Codex provider 已配置成 OpenRouter 的情況。`codex-run` 則是開新子 Codex，不能宣稱原父 session 無縫切換。
- 補充非文字模型以 per-modality 專用 API 包裝，不能宣稱其能當普通 LLM 自動修改程式。
- `free_agents.py call` 的回應不是工作驗收。需獨立 test 退出碼或人工證據；原本兩次失敗升級與 Runtime 狀態機依各自規則運作。
