# 2026-10-27 — Luna Router：effort 改由 Luna 決定，取消本地下限

## 改動
- `policy.enforce_selection` 新增參數 `effort_floor`（預設 True，離線／local 路徑行為不變）。設為 False 時直接採用 Luna 選的 effort；只有所選模型不支援該檔時，才改用最近的可用檔（往上找，找不到則取最高檔）。
- `router.make_plan` 的 Luna 路徑改以 `config.policy.local_effort_floor` 決定是否套用下限，預設 false，即不套用。
- `router.resume_plan` 重試時不再把 Luna 存下的 effort 拉回下限（否則會因 `saved_effort_no_longer_supported` 被擋）。
- `config.json` 新增 `"local_effort_floor": false`。SKILL.md、CHANGELOG、MANIFEST 已同步更新；`~/.codex/AGENTS.md` 的 luna 區塊改為「模型與 effort 皆由 Luna 決定」。
- 新增 3 個測試：Luna 選的 low 低於下限仍照用；開啟選項時下限仍有效；不支援的 effort 改用最近的可用檔。

## 驗證
- `python -m unittest discover -s tests -q` → 270 tests OK（17 skipped）。
- `python verify-package.py` → PASS 122 entries。已重新安裝，備份於 `~/.codex/luna-router-backups/20261027T022408Z-475d2d`。
- Live（同一任務：讀 `data.txt` 回答 alpha，預期 42，read-only）：

| run | Luna 決策 id | Luna effort | 實際派工 effort | 回合 | 回答 |
|---|---|---|---|---|---|
| 4 | gen-dec-1793067858-GzalyT4dLgfWpi0vW55X | low | low（reason `accepted`） | turn_completed | **0 ✗** |
| 5 | （略） | low | low | turn_completed | **0 ✗** |
| 6 | （略） | low | low | turn_completed | 42 ✔ |
| 對照：改前 run3／MCP | — | low | medium（被下限提高） | turn_completed | 42 ✔（2/2） |

## 發現
- 路由行為已符合要求：Luna 選 low，就以 low 派工。
- 但此 read-only 小題在 low 下 3 次只答對 1 次。run4 的 transcript 顯示 worker 沒呼叫任何工具讀檔，直接回答 `0`。改前被提到 medium 時 2/2 正確。
- 可行的補救（未實作，待主人決定）：
  1. 實作任務加 `--job-id --verify-argv-file --auto-retry`，經驗證失敗兩次後自動升一檔 effort（既有機制）。
  2. 在送給 Luna 的 effort 說明中註明「需讀檔或用工具者勿選 none／low」。
  3. 只對 low 以下設一個很小的下限（例如需用工具時至少 medium）。
