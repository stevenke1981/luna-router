# 2026-10-27 — 修正 Codex 內 luna_run 回 HTTP 400（luna_api_error）

## 根因（已用線上 API 證實）
- 收據 `~/.codex/luna-router/.state/receipts/18d2ed5a….json`：stage `decision`，`http_status 400`，未取得回應。
- 依本機實際的 Codex 模型清單計算：凡 `risk=high` 或 `complexity=4` 的任務，候選只剩 `gpt-6-astra` 一個。route 題因此只有一個選項。
- 直接打 API 驗證：一個選項 → `400 OpenAI Decisions needs at least 2 choices; question "route" has 1`；兩個選項 → 200。
- 所以凡被歸為高風險或最高複雜度的任務（Archiver 右鍵整合很可能被歸為 high），必定 400。以前之所以沒發生，是因為先前的信心門檻與模型 id 問題讓請求更早就失敗。

## 修正
- `decision.build_request`：只有一個選項的題目不送出。單一候選時只問 effort，單一 effort 時只問 route；兩題都只有一個選項則報 `no_decision_question_has_two_options`（現有模型皆有 ≥5 檔 effort，實務上不會發生）。
- `decision.parse_response`：沒送出的那一題，以唯一選項、機率 1.0 補上。
- 原規範「單一候選仍由 Luna 決定 effort」依然成立。
- 更新 2 個舊測試（原本測的就是會被線上拒絕的單一選項格式），新增 1 個測試；CHANGELOG、MANIFEST 已更新，並已重新安裝（備份 `~/.codex/luna-router-backups/20261027T023426Z-a54ed9`）。

## 驗證
- `python -m unittest discover -s tests -q` → 273 OK（17 skipped）；`verify-package.py` PASS。
- Live：`luna.py run --risk high --sandbox read-only`（讀 data.txt，預期 42）
  - 決策：`luna_decisions`，id `gen-dec-1793068469-MtxoAO2fORhavFBvlELT`，候選 1 個（`gpt-6-astra`），Luna 選 effort `low`（機率 0.76）
  - worker：啟動、`turn_completed`，回答 `42` ✔

## 尚待
- 未在 Codex app 內重跑 Archiver 原任務，請主人重跑。若仍有錯，貼上新的錯誤即可；每次的收據都在 `~/.codex/luna-router/.state/receipts/`。
