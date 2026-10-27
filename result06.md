# Luna Router 修正與實際驗收 — 2026-10-27

版本：v2.7.0；交付工作目錄：`E:\luna-router`。

## 修正

- 重現 Windows CP950 管線輸出包含 emoji 時，CLI 以 `UnicodeEncodeError` 結束。`luna.py` 現在將標準串流設為 UTF-8；新增強制 CP950 的子程序回歸測試。
- 實際 live 範例第一次取得有效決策並完成 worker，但 PowerShell 產生帶 BOM 的 JSON，使驗收讀取失敗。驗收器改用 `utf-8-sig`，並補上 BOM 測試。
- 修正 routed worker 的測試夾具繼承 `LUNA_ROUTER_WORKER=1`，以及 TEMP 在專案內造成遞迴複製的問題。Production 遞迴防護保留。
- 安裝時排除 `.git`、`.tmp`、`.venv`、`.env*`；忽略本機狀態與原始 live receipt，避免混入公開原始碼。
- 新增 `examples/live-smoke.py`：呼叫真正 `luna.py run`，使用隨機輸入與 nonce，由主機獨立檢查 worker 產物。舊目錄、缺少產物、錯誤答案、模擬決策與失敗回合均不得通過。

## 驗證證據

- `python -m unittest discover -s tests -q`：283 項，266 通過，17 跳過。跳過的 Unix-only process fixtures 不是 Windows live 證據。
- 真實範例：`python examples/live-smoke.py --output-dir evidence/live-smoke-verified`。
- 決策回應：`gen-dec-1793072809-LlBvKixZMDABxyOIVRG3`，來源 `luna_decisions`，非模擬。
- worker 啟動：PASS；回合完成：PASS；獨立驗收：PASS；四層結果見 `evidence/live-smoke-verified/summary.json`。
- 本次隨機數字總和：`4488290`，nonce 與輸入相符；輸入與產物均保存在該 evidence 目錄。
- 原 `E:\luna-router-v2.7.0` 被目前程式占用，直接改名失敗；改以不含版本號的新目錄交付，舊目錄保留，未強制關閉程式或刪除。

決策服務目前已實際成功；沒有改模型策略、增加本地 effort 下限、取消沙箱或以本地決策冒充成功。回合完成不等於驗收通過，範例另行驗證產物。worker 的網路限制仍保留，需網路的主機工作由已授權的主機端執行。

## 已安裝入口

- 更新 `%USERPROFILE%\.codex\luna-router`，保留原安裝備份；沒有變更全域 model/provider 或安全設定。
- 已安裝入口真實決策：`gen-dec-1793072919-xOPGkegpy9EKIT3jzDYk`；四層驗收全部 PASS，見 `evidence/live-smoke-installed/summary.json`。
- 新入口與來源的程式碼雜湊一致；安裝目的地沒有 `.git`。
- 現有 MCP 程序可能尚保留舊 import，需重啟 Codex 使其載入更新；本次已驗證的是新啟動的已安裝 CLI。
- 交付儲存庫：`https://github.com/stevenke1981/luna-router`，公開、主分支 `main`。Git commit 與遠端 HEAD 由交付回覆列出，避免文件自我參照雜湊。

CI、其他作業系統與重新啟動後的 MCP GUI 呼叫：NOT_RUN。
