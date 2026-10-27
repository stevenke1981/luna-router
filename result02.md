# 2026-10-27 — 修正 Codex 內 luna_run 回 HTTP 401（luna_api_error）

## 根因
- 401 那次的收據（`~/.codex/luna-router/.state/receipts/4c25ca27….json`）中 `key_status.source = process`：Key 來自 Codex app 的程序環境，由 MCP 設定 `env_vars = ["OPENROUTER_API_KEY"]` 轉交給路由器。
- 同一時間，Windows 使用者登錄檔中的 Key 於 shell 中 probe 成功（`gen-dec-1793068151-…`）。
- 推論：Codex app 啟動後主人更換過 Key，Codex 仍帶著舊 Key，所以每次都 401。（Codex 程序內的 Key 值無法直接比對，故此為推論；其餘證據皆一致。）

## 修正
- `credentials.py`：拆出 `registry_key()`，讀取當下的 User／System 登錄檔值。
- `openrouter_backend.request_json`：Key 來自程序環境且收到 **401** 時，若登錄檔中有**不同**的 Key，就用它**重送一次**。401 表示請求未被處理，不會重複計費。其他錯誤碼（402、429…）或登錄檔值相同時不重試，照常回報 blocked。Key 值不印出也不存檔。
- 新增 2 個測試；CHANGELOG、MANIFEST 已更新，並已重新安裝（備份 `~/.codex/luna-router-backups/20261027T023028Z-60a70f`）。

## 驗證
- `python -m unittest discover -s tests -q` → 272 OK（17 skipped）；`verify-package.py` PASS。
- Live：程序環境放一把故意作廢的 Key，模擬 Codex 傳下舊 Key：
  - `luna.py probe` → `response_received`，id `gen-dec-1793068231-Lcp5x9Q4SJNP6VLGVktG`
  - MCP `luna_run` → `luna_decisions`（id `gen-dec-1793068241-fOEgKkK5kTaJGK0DoHXa`），`gpt-6-luna`／low，`turn_completed`，回答 `42` ✔

## 尚待
- 實際由 Codex app 呼叫未重測，須主人在 Codex 中重跑原任務（Archiver 右鍵整合）。新程式已安裝，MCP 每次呼叫都會載入新的 Python 程序；若 Codex 仍沿用舊的 MCP 程序，重啟 Codex 即可。
- 若登錄檔中的 Key 本身也失效，仍會回 401 blocked，這屬正確行為。
