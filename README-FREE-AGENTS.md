# OpenRouter Free → Codex Agents （v2.5.0）

**請先注意限制：Codex 的 custom agent TOML 不能改變父代理的 `model_provider`。** 這個發行包為每個經 API 確認的免費模型產生橋接角色 (`orfree_*.toml`)；文字模型額外產生原生角色 (`orfree_*_native.toml`)。兩者不一樣：

- **橋接角色**：Codex 父代理可保持 ChatGPT 訂閱／GPT Sol，用 shell 執行 Python `free_agents.py call`，才真的向 OpenRouter 發出所選 `:free` 的請求。橋接角色本身仍使用父代理模型，**不是**免費模型本體。
- **原生角色**：設定 `model = "提供商/模型:free"`，只適用於已在 OpenRouter provider 下啟動的 Codex 父工作階段。原生模型只聲明官方目錄中的 tool 參數，不能保證 Codex 的 Responses 工具迴圈完全相容。
- **單獨子 Codex**：`free_agents.py codex-run` 使用一次性 `-c` 指定 OpenRouter provider，透過標準輸入送任務，可不改原本的 `~/.codex/config.toml`；需要模型支援 `tools`。此路徑屬於「Codex 開新子程序」，**不是**在已存在的主代理中無縫換 provider。

## 1. 一鍵同步及安裝（Windows PowerShell）

解壓至固定資料夾（例如 `E:\tools\luna-router-v2.5.0`）並執行：

```powershell
cd 'E:\tools\luna-router-v2.5.0'
python free_agents.py doctor
.\install-free-agents.ps1 -Scope user
# 專案限定，可使用：.\install-free-agents.ps1 -Scope project -Project 'E:\myrepo'
```

**Key 在 Windows 系統環境變數 `OPENROUTER_API_KEY`**，程式只讀取該變數，ZIP、`registry.json`、TOML、log 不儲存 Key。請先在環境變數設定好，再完全重開 PowerShell/Codex；本工具不協助輸入或儲存密鑰。

```powershell
[bool]$env:OPENROUTER_API_KEY  # 只顯示是否有值；不要回顯密鑰
python free_agents.py sync     # 由 OpenRouter 公開目錄取得所有輸出模態，生成每模型入口
python free_agents.py list
python free_agents.py list --kind decisions
python free_agents.py list --json
python free_agents.py doctor
```

`install-free-agents.ps1` 預設會先執行線上 `sync`，同步最新價格、停用已過期／付費的模型。**不修改**任何 `config.toml`、Codex 登入、模型預設或費用設定。使用 `-UseBundledSnapshot` 可離線安裝，但包內快照只記錄 2026-10-26 的 18 個文字／隨機路由入口，*不代表目前所有免費模型*。在實機連線後請重新執行線上安裝。已安裝但後來失效的角色仍可能留在 Codex 選單；橋接執行時會重新查目錄並拒絕使用失效或改為付費的模型。

## 2. 每個模型的兩種直接使用方法

任何 Agent 名都能透過 `python free_agents.py list` 取得。若用橋接，Codex 可先用 shell 將獲准公開的 prompt 保存到檔案，再呼叫：

```powershell
python free_agents.py call --agent 'recursive/flint-v1.0:free' --text-file .\prompt.txt --data-policy public --output .\result.json
# 或使用生成的名稱：--agent orfree_recursive_flint_v1_0_free_<hash>
.\run-free-agent.ps1 -Agent 'recursive/flint-v1.0:free' -TextFile .\prompt.txt
```

要讓免費模型像 Codex 工作者一樣讀取專案，**必須另啟一個子 Codex**，而非使用單次文字完成。由於子程序可能自行讀取整個專案，`codex-run` 強制使用 `--data-policy approved --approved`，不接受 public 快捷路徑：

```powershell
python free_agents.py codex-run --agent 'recursive/flint-v1.0:free' --project 'E:\myrepo' --text-file .\task.txt --data-policy approved --approved --dry-run
# 確認準備上傳的任務摘要不含秘密；預設 sandbox=read-only
python free_agents.py codex-run --agent 'recursive/flint-v1.0:free' --project 'E:\myrepo' --text-file .\task.txt --data-policy approved --approved
# 若要修改程式，再額外加 --allow-edit，改為 workspace-write
```

`--dry-run` 會先查即時免費狀態（並且需要 key），只列出命令，不實際呼叫推理；它不保證 Codex 內部可以成功使用該模型。可選 `--skip-git-repo-check` 適用於非 Git 目錄。子 Codex 只有 exit code，不等於程式修復通過驗收；仍需使用原路由 `--verify-argv-file` 或外部測試。任何使用該 OpenRouter provider 的工具執行程序都應隔離憑證與敏感檔案。

## 3. 不同模型用不同 API，不能當成聊天模型

`sync` 使用 `GET /api/v1/models?output_modalities=all`，以型別產生橋接角色：

| Model output | 呼叫 API | `--request-file` 範例內容 |
|---|---|---|
| text | `/api/v1/chat/completions` | 用 `--text-file` 傳文字 |
| decisions | `/api/alpha/decisions` | `{"state":"公開事件", "questions":{"category":{"type":"choice","criteria":{"a":"A", "b":"B"}}}}` |
| embeddings | `/api/v1/embeddings` | `{"input":"公開字串"}` |
| rerank | `/api/v1/rerank` | `{"query":"查找問題", "documents":["公開段落一", "公開段落二"]}` |
| speech | `/api/v1/audio/speech` | `{"input":"Welcome", "voice":"實際模型支援的語音名稱", "response_format":"mp3"}` |

對專用型模型使用：

```powershell
python free_agents.py call --agent 'inception/mercury-decide:free' --request-file .\decision-request.json --data-policy public --output .\decision-result.json
python free_agents.py call --agent '某一個嵌入模型:free' --request-file .\embedding-request.json --data-policy public --output .\vectors.json
python free_agents.py call --agent '某一個免費語音模型:free' --request-file .\speech-request.json --data-policy public --audio-output .\voice.mp3
```

`決策/嵌入/排序/語音` 角色**不是**文字生成 LLM，不會通過 Codex 原生 `model=` 欄位被當成編程代理。橋接角色以已登入 Codex 的模型調用專用 endpoint。各種專用端點需要各自合法的請求、支援聲音、輸出格式，可能依提供者不同；尚未完成實際聯機合約驗證時應回報 API 錯誤，不得偽造成功。

## 4. 若要使用 Codex 原生子代理

Codex 支援 `~/.codex/agents/*.toml`，但不接受每個 Agent 各自覆寫 provider。原生角色只會在 parent 本來就是 OpenRouter 時生效。請在 **使用者級別** `~/.codex/config.toml` 合併（並事先備份）供應商定義，無需改 ChatGPT 默認 `model`：

```toml
[model_providers.openrouter_free]
name = "OpenRouter Free"
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
wire_api = "responses"
supports_websockets = false
```

然後在**獨立**的 Codex 工作階段用 `-c 'model_provider="openrouter_free"'` 選擇該供應商，並選擇一個 `.toml` 的 native Agent。Windows PowerShell 例如：

```powershell
codex -c 'model_provider="openrouter_free"' -m 'recursive/flint-v1.0:free'
```

如果 OpenRouter 子代理的非 OpenAI 模型被 Codex 回報 `Unknown model`，可能是使用 `env_key` 時沒有取得模型的原生 metadata；可使用官方文檔的 command-backed `auth` 配置，但不要和 `env_key` 並存。沒有實機連線驗證前，不應聲稱原生 Agent 已可處理所有工具操作。

## 5. 安全、限制與模型同步

- **免費 ≠ 無限制**。OpenRouter 帳號與 API Key rate limits、支出上限由平台控制，本專案不另加本地美元費用閘門；免費角色仍檢查 `:free` ID、`prompt`／`completion`／其他已列出的費用欄位全部為零。`openrouter/free` 是隨機免費路由，結果未必與同名模型一致。
- 執行前必須再次即時查 OpenRouter 目錄；無法查證、過期、漲價、缺少 text context metadata 都會拒絕，不自動轉用付費模型。
- `call` 預設 `--data-policy no_external`（阻止上傳）。需要明確指定 `--data-policy public`；如果是**已獲使用者批准的刪敏任務**，使用 `--data-policy approved --approved`。不應傳送完整私有原始碼、金鑰、帳號資料或機密文件。
- `codex-run` 是**新的 Codex 子程序**；模型是否支援工具、Responses API 與驗證能力，仍要實機檢驗；允許工具的程式可能存取系統環境變數，因此建議使用隔離執行環境，並限制工具網路與目錄權限。
- Luna Decisions 的選模型與 effort 邏輯保持不變，另外新增「最多 16 個免費自動候選」供評估；所有免費文字模型皆可透過 `requested_model` 指定，不受到自動候選數上限的影響。非文字模型不會被強塞進 Luna 的文字 worker 列表。
- 本版不會自動安裝到遠端 5900X，ZIP 在使用者解壓並執行安裝後才生效。

## 6. 發行包內產物

`free_agents.py` 動態目錄／橋接／生成器、`generated/registry.json`、`generated/codex-agents/*.toml`、`install-free-agents.ps1`、`refresh-free-agents.ps1`、`run-free-agent.ps1`、`tests/test_free_agents.py`、原先 v2.4 的 Luna Router 和 runtime controller。執行 `python -m unittest discover -s tests -q` 檢查單元與整合測試。

**參考來源：** OpenAI Codex [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[Codex CLI flags](https://learn.chatgpt.com/docs/cli/reference)、OpenRouter [Codex CLI](https://openrouter.ai/docs/cookbook/coding-agents/codex-cli)、[Models API](https://openrouter.ai/docs/api/api-reference/models/get-models)、[Decisions](https://openrouter.ai/openai/gpt-6-luna-decisions)、[Embeddings](https://openrouter.ai/docs/api/api-reference/embeddings/create-embeddings)、[Rerank](https://openrouter.ai/docs/api/api-reference/rerank/create-rerank)。
