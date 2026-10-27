# v2.7.0 primary sources — checked 2026-10-27

- OpenRouter Luna Decisions model/provider endpoint: https://openrouter.ai/openai/gpt-6-luna-decisions/providers
  POST https://openrouter.ai/api/alpha/decisions; exact model openai/gpt-6-luna-decisions; state + typed questions.
- OpenAI Codex MCP configuration: https://developers.openai.com/codex/mcp
  Local stdio command/args, env_vars, shared config, tool_timeout_sec; web ChatGPT does not read local Codex configuration.
- OpenAI Codex App Server: https://developers.openai.com/codex/app-server
  model/list describes model capabilities; does not prove this account has successfully run that model.
- Input source: attached luna-router-v2.6.0.zip. Prior claims are not used as proof of live execution.

## Historical source notes (not revalidated in this patch)

# 官方來源與設計依據

查證日期：2026-10-26（台灣）。模型目錄會變動；快照不代表執行時可用性或帳號權限。下列均為官方來源。

## OpenAI

| 代號 | 來源 | 本方案使用的事實 |
|---|---|---|
| O1 | [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) | 精確ID、模型定位與API支援effort；目前Standard一般文字input/output每百萬tokens US$2/US$10 |
| O2 | [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) | 精確ID與困難任務定位；支援effort以模型頁及runtime為準 |
| O3 | [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) | 精確ID、聚焦及大量工作定位 |
| O4 | [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) | 精確ID、能力與成本的定位 |
| O5 | [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) | 精確ID；目前Standard一般文字input/output每百萬tokens US$2/US$12，名稱不代表一定比新Sol便宜 |
| O6 | [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) | 精確ID與既有世代定位 |
| O7 | [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) | 精確ID與聚焦工作定位 |
| O8 | [Codex App Server](https://learn.chatgpt.com/docs/app-server) | initialize/initialized握手；model/list、native supportedReasoningEfforts、分頁與account/client差異 |
| O9 | [Authentication](https://learn.chatgpt.com/docs/auth) | ChatGPT與API key認證途徑分開，login status可辨識使用方式 |
| O10 | [CLI reference](https://learn.chatgpt.com/docs/cli/reference) | exec、model與config覆寫、sandbox、stdin及JSON輸出參數 |
| O11 | [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) | model_reasoning_effort、model_provider、forced_login_method設定 |
| O12 | [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) | JSONL事件及非互動執行整合 |
| O13 | [Using GPT-6](https://developers.openai.com/api/docs/guides/latest-model) | 若未來加入OpenAI API executor，6.1 Sol/Astra的工具工作應走Responses，不能把Chat Completions的限制直接套用於Codex |

## OpenRouter

| 代號 | 來源 | 本方案使用的事實 |
|---|---|---|
| R1 | [GPT-6 Luna Decisions](https://openrouter.ai/openai/gpt-6-luna-decisions) | 精確ID openai/gpt-6-luna-decisions；input US$0.10/M、output US$0；1,050,000 context，text/image輸入、decisions輸出 |
| R2 | [Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request) | POST /api/alpha/decisions；state/questions；choice的probabilities與confidence是不同欄位 |
| R3 | [Models API](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) | 依output_modalities分開讀text與decisions；以metadata作能力篩選 |
| R3a | [公開免費文字目錄搜尋](https://openrouter.ai/api/v1/models?q=free) | 僅用於來源查核；正式程式讀完整modality catalog再依價格判定 |
| R3b | [公開文字目錄](https://openrouter.ai/api/v1/models?output_modalities=text) | 本次public GET smoke test來源，facts-only快照在evidence |
| R3c | [公開決策目錄](https://openrouter.ai/api/v1/models?output_modalities=decisions) | Luna Decisions付費條目的price/modality/context資料 |
| R4 | [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router) | openrouter/free按請求能力篩選後隨機選免費模型，需記錄actual model |
| R5 | [Free variants](https://openrouter.ai/docs/guides/routing/model-variants/free) | 真實:free variant與付費模型fallback的計費差異 |
| R6 | [Provider routing](https://openrouter.ai/docs/guides/routing/provider-selection) | max_price、require_parameters與provider fallback控制 |
| R7 | [Limits](https://openrouter.ai/docs/api_reference/limits) | 免費請求配額、帳號容量、錯誤及GET /api/v1/key檢查方式 |
| R8 | [Jev API surfaces](https://openrouter.ai/docs/guides/community/jev) | Decisions HTTP與TypeSafe System One兩種API surface |
| R9 | [Multimodal Decisions](https://openrouter.ai/docs/guides/community/multimodal-decisions) | Luna Decisions圖片輸入能力及格式；這版未實作圖片transport |

| R10 | [Luna Decisions providers](https://openrouter.ai/api/v1/models/openai/gpt-6-luna-decisions/endpoints) | 供應商目前的輸入、輸出與額外價格欄位；未列單次request價目 |
| R11 | [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions) | 原生OpenAI Decisions與OpenRouter的wire ID、schema不同；本包維持OpenRouter介面 |

## 事實、假設與未驗證項目

**官方支持的事實：**上述API形式、7個GPT模型ID、公開目錄的當下價格與能力欄位、Codex原生能力探索方式。

**本方案設計：**Luna/Sol/Astra分工、legacy優先序、複雜度與風險下限、0.85/0.95機率門檻、0.20/0.30 margin、18/min總請求、40/day免費worker、1000/day付費決策、本地US$0.01/次與US$0.10/day決策上限、30分鐘快取、每次最多一個worker。

**尚未驗證：**實際帳號model entitlement、Luna Decisions推理連線、真實模型切換、免費模型完成率、成本節省幅度、Windows原生Codex與PowerShell執行。

不存在足夠資料支持「某免費模型等同Sol」或「這些機率門檻已達到指定任務成功率」，因此本包沒有這類宣稱。

## v2.3 官方依據補充

- [OpenAI Reasoning models — reasoning effort](https://developers.openai.com/api/docs/guides/reasoning)：reasoning effort 支援集合依模型而變，可包含 `none`、`low`、`medium`、`high`、`xhigh`、`max`。不可任意對所有模型要求 `max`；本包以原生 runtime 可用性為準。
- [OpenRouter 建立 API Key](https://openrouter.ai/docs/api/api-reference/api-keys/create-keys)：`limit` 以美元為單位、`limit_reset` 可設每日／每週／每月。
- [OpenRouter 更新 API Key](https://openrouter.ai/docs/api/api-reference/api-keys/update-keys)：管理端可更新 API Key 花費上限。
- [OpenRouter Guardrails](https://openrouter.ai/docs/guides/features/guardrails/overview)：支出限制、模型白名單及資料政策由 OpenRouter 平台控制。

**v2.3 校正：**上文舊版「本地 USD 上限／每次最多一個 worker」為 v2.1 史實，不再適用於本版；實際規範以新版 `README.md`、`CHANGELOG.md` 及 `config.json` 為準。

## v2.5.0 新增官方來源（2026-10-27 核對）

- Codex custom agent TOML，關鍵限制：agent 繼承 parent `model_provider`、sandbox、MCP；不可由個別 TOML 變更 provider。 https://learn.chatgpt.com/docs/agent-configuration/subagents
- Codex CLI `-c key=value`、`codex exec -m`, `--cd`, stdin `-`, `--sandbox`。 https://learn.chatgpt.com/docs/cli/reference
- OpenRouter Codex CLI provider `wire_api=responses`、`OPENROUTER_API_KEY`。 https://openrouter.ai/docs/cookbook/coding-agents/codex-cli
- OpenRouter 模型目錄：`output_modalities=all`、`q` 是全文搜尋，不是完整免費價格驗證。 https://openrouter.ai/docs/api/api-reference/models/get-models
- OpenRouter `:free` 低額度變體。 https://openrouter.ai/support
- OpenRouter Decisions 端點。 https://openrouter.ai/openai/gpt-6-luna-decisions
- OpenRouter Embeddings 端點。 https://openrouter.ai/docs/api/api-reference/embeddings/create-embeddings
- OpenRouter Rerank 端點。 https://openrouter.ai/docs/api/api-reference/rerank/create-rerank
- OpenRouter Speech endpoint。 https://openrouter.ai/blog/tutorials/text-to-speech/

## v2.6 參考（發行當時）

- https://learn.chatgpt.com/docs/app-server — `model/list`、`supportedReasoningEfforts` 與帳號/client 相容性注意事項。
- https://learn.chatgpt.com/docs/developer-commands — `codex exec` 與非互動執行。
- https://openrouter.ai/docs/api-reference — OpenRouter API 与模型目錄；實際執行仍需在用戶帳號驗證。
