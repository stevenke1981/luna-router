# 可見 Luna 多 Agent 工作流程

本流程以持久化 workflow job 記錄拆分後的 tasks，由真實 Luna Decisions 為每個 worker task 選擇模型與 effort，再由 Codex Host 的原生 `spawn_agent` 平行啟動可見 Agent。Coordinator 與 integrator 是固定設定角色；預設 coordinator 為 `gpt-6-luna` / `low`，integrator 為 `gpt-6.1-sol` / `high`，`max_parallel` 預設為 2，可在 workflow start 的 config 覆寫。

## 工具邊界

- MCP 提供持久化 job、決策、派送描述、事件、整合與驗證介面。Host 使用派送描述呼叫原生 `spawn_agent`，並把 Host 產生的 agent ID 記入 job。
- Agent UI 與原生 ID 來自 Host；MCP server 本身不能建立 UI Agent，也不能切換目前對話模型。背景 CLI 執行不等於原生可見 Agent。
- 工作記錄保存狀態、可見動作、工具輸出證據與產物參照，不包含或聲稱提供隱藏思維鏈。
- Worker 完成只表示該 Agent 回合已結束。整體完成還需要整合與獨立驗證證據。

## 啟動持久化 job

Coordinator 先把目標整理成明確的 task 清單，每個 task 指定驗收目標、資料政策、寫入範圍與相依 task。再呼叫 MCP `luna_workflow_start(project, tasks, config?)`，或使用 CLI：

```powershell
python <repo-root>\luna.py workflow start --request <UTF-8 JSON file>
```

Request 範例：

```json
{
  "project": "E:\\my-project",
  "tasks": [
    {
      "id": "task-01",
      "objective": "修正匯入器並補上回歸測試",
      "kind": "coding",
      "complexity": 2,
      "risk": "low",
      "data_policy": "metadata_only",
      "write_scopes": ["src/importer.py", "tests/test_importer.py"],
      "depends_on": []
    }
  ],
  "config": {
    "coordinator_model": "gpt-6-luna",
    "coordinator_effort": "low",
    "integrator_model": "gpt-6.1-sol",
    "integrator_effort": "high",
    "max_parallel": 2
  }
}
```

Start 回傳 job snapshot。Job 會保存 project、config、status、integration、verification；tasks 是扁平清單，含 `id`、`objective`、`kind`、`complexity`、`risk`、`data_policy`、`write_scopes`、`depends_on`、`status`、`plan`、`agent_id`、`outcome`、`evidence`。依賴必須成為有效 DAG；同時執行的 task 寫入範圍不可重疊。只把絕對專案路徑交給本機 Host。預設 `metadata_only` 只向 Decisions 傳固定分類 metadata，不傳 task 原文、專案路徑、原始碼、私鑰或未核准摘要。

## 決策後才派送

對 task 呼叫 `luna_decide(job_id, task_id)` 取得真實 Luna Decisions 選出的 route。CLI 對應：

```powershell
python <repo-root>\luna.py workflow decide --request <UTF-8 JSON file>
```

這一步只記錄決策，絕不啟動 worker。Decision 有效後呼叫 `luna_workflow_dispatch(job_id, task_id)`，CLI 對應 `workflow dispatch --request <UTF-8 JSON file>`。Host 收到含模型與 effort 的 dispatch descriptor 後，使用原生工具：

```text
spawn_agent(fork_turns="none", model=<descriptor.model>, reasoning_effort=<descriptor.effort>)
```

向 worker 傳入該 task 的目標、驗收條件、專案綁定、寫入範圍、依賴結果和禁止遞迴呼叫 Router 的要求。Host 收到 agent ID 後，先用 workflow event 記錄 `started` 與 `agent_id`，再回報進度。只讓依賴已完成且寫入範圍不重疊的 task 平行跑，並遵守 `max_parallel`。

若 Decisions 失敗，worker 不得自行選模型。若 spawn 回應不確定，不可自動重播以免出現重複寫入者；先查詢 Host 狀態並記錄阻塞原因。沒有原生 `spawn_agent` 的 Host 不可用背景 CLI 假裝原生 Agent 已啟動。

## 事件、進度與狀態

呼叫 `luna_workflow_event(job_id, task_id?, action, agent_id?, summary?, outcome?, evidence?)` 記錄 Host 回報的事件。支援的 action 為 `started`、`progress`、`completed`、`integration_started`、`integration_completed`。Progress 記錄可見動作和實際工具證據，不記錄隱藏推理，也不應把尚未執行的命令描述成已完成。

使用 `luna_workflow_status(job_id, after?)` 讀取 job snapshot 和游標之後的 events。CLI 對應 `workflow status --request <UTF-8 JSON file>`。事件包含序號、時間、task、action 與受限摘要/證據，可顯示 Decisions route 結果、Host 原生 agent ID、可見動作、工具結果、worker outcome、整合與驗證狀態。

HTTP 500/502/503/504 最多嘗試三次，每次等待最多五秒；認證失敗、配額/帳務限制、結果不明的傳輸及無效決策不自動重試，也不切成本地路由。只阻塞失敗 task 和相依分支；Coordinator 可繼續獨立規劃、檢查或自己的工作，Integrator 可使用已有產物先做獨立整合準備；未取得有效 Decisions route 的 worker 仍不可派送。

## 整合與驗證

Task 結果準備好後，呼叫 `luna_workflow_integrate(job_id)` 取得整合工作，交給設定的 integrator。記錄 `integration_started` 和 `integration_completed` 事件、整合動作及證據。Integrator 預設 `gpt-6.1-sol` / `high`，可由 workflow config 修改；它不是 Luna Decisions 選出的 worker。

然後呼叫 `luna_workflow_verify(job_id, check_file)`，CLI 對應 `workflow verify --request <UTF-8 JSON file>`。`check_file` 使用絕對路徑，內容例如 `{"argv":["python","-m","unittest"],"timeout_seconds":300}`。Host 在專案目錄執行，保存 exit code 與輸出 hash，不接受 caller 直接填入 PASS。Verifier 必須獨立於 worker 與 integrator。只有必要 task、整合與獨立驗證都有成功證據時，job 才能標記 `completed`。缺少驗證或工具無法執行維持 `awaiting_verification`；實際檢查失敗為 `verification_failed`。驗證已 claim 時不重播，已完成的 job 不重跑驗證。

## 工具索引

| MCP tool | 用途 |
| --- | --- |
| `luna_workflow_start(project, tasks, config?)` | 建立 job 與 task 清單 |
| `luna_decide(job_id, task_id)` | 取得 worker 的 Decisions route，不派送 |
| `luna_workflow_dispatch(job_id, task_id)` | 取得有效 route 的原生派送描述 |
| `luna_workflow_event(job_id, task_id?, action, agent_id?, summary?, outcome?, evidence?)` | 記錄啟動、進度、完成與整合事件 |
| `luna_workflow_status(job_id, after?)` | 讀取 job 與事件 |
| `luna_workflow_integrate(job_id)` | 取得設定 integrator 的整合工作 |
| `luna_workflow_verify(job_id, check_file)` | 執行本機 verifier 並保存實際證據 |

每個 MCP tool 的等價 CLI action 是 `workflow start|decide|dispatch|event|status|integrate|verify --request <UTF-8 JSON file>`。報告時分開列出 Decisions 回應、原生派送/agent ID、worker 回合完成、整合和獨立驗證；這些階段不能互相代替。
