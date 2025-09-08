# Multi-Server Instrument Console – README

## 檔案總覽
- **Multi-Server Page.html**：前端網頁控制台，透過瀏覽器管理多個儀器伺服器（drivers/instruments/rpc）。
- **config.json**：伺服器清單的設定檔，用於自動載入伺服器列表。
- **client.py**：簡易 RPC 客戶端，可從 Python 腳本直接呼叫伺服器 API。

---

## 1) Multi-Server Page.html – 網頁控制台

### 功能說明
`Multi-Server Page.html` 是一個單檔前端介面（HTML + CSS + JavaScript），提供完整的操作儀表板：
- **Servers 管理**
  - 新增伺服器（手動輸入名稱與 base URL）
  - 掃描 IP 範圍（透過 `/healthz` 檢查）
  - 儲存至 localStorage
  - 匯出為 `config.json`
- **Drivers 管理**
  - 查看已載入的驅動
  - Rescan 驅動檔案
  - 上傳新的 `.py` 驅動檔案（自動驗證是否符合 `initialize / shutdown / is_opened` 規範）
- **Instruments 管理**
  - 建立新儀器（輸入名稱、綁定 Driver、設定參數 init_args/init_kwargs、Capabilities）
  - 列出所有儀器
  - Connect / Disconnect / Reconnect
  - 編輯已存在的儀器（含重新設定 capabilities）
  - 刪除儀器
- **RPC 控制**
  - 選擇儀器與可用命令
  - 自動顯示函式簽名與 Docstring
  - 輸入 `args`、`kwargs` 並送出 RPC 請求

### 使用方式
1. 將 `Multi-Server Page.html` 放在任何靜態伺服器或直接用瀏覽器開啟。
2. 輸入伺服器位址（例如 `http://172.30.10.18:9999`），並按下 **Add**。
3. 按下 **Use** 選擇伺服器，然後可以：
   - 按 **Drivers → Load** 檢視伺服器上有哪些驅動。
   - 按 **Instruments → List** 檢視並操作儀器。
   - 使用 **RPC** 直接對儀器送命令。

---

## 2) config.json – 伺服器清單

### 結構
```json
{
  "servers": [
    {
      "name": "QM",
      "base": "http://172.30.10.18:9999"
    }
  ]
}
```

- **name**：伺服器的顯示名稱（可省略）
- **base**：伺服器的 URL（必填，需包含 `host:port`）

### 使用方式
- 在 `Multi-Server Page.html` 啟動時會嘗試讀取同目錄的 `config.json`，並將伺服器自動加到列表。
- 使用網頁上的 **Download config.json** 功能，可以匯出最新的伺服器列表，並手動覆蓋現有檔案。

---

## 3) client.py – Python RPC 客戶端

### 功能說明
`client.py` 提供一個簡單的函式 `post()`，可以從 Python 腳本直接呼叫伺服器的 `/rpc` API。

### 安裝需求
```bash
pip install requests
```

### 使用範例
```python
from client import post

base = "http://172.30.10.18:9999"   # 或 "172.30.10.18:9999"
instrument = "HF1"                  # 儀器名稱或 ID
cmd = "set_wavelength"              # Driver 的公開方法
args = [1550.0]                     # 位置參數 → list
kwargs = {"unit": "nm"}             # 關鍵字參數 → dict

result = post(base, instrument, cmd, args, kwargs)
print("RPC result:", result)
```

### post() 參數
- **IP (str)**：伺服器位置，例如 `"172.30.10.18:9999"` 或 `"http://172.30.10.18:9999"`
- **Instrument (str)**：儀器名稱或 ID
- **command (str)**：要執行的 driver 方法
- **args (list)**：傳給方法的參數（預設 `[]`）
- **Kwargs (dict)**：傳給方法的關鍵字參數（預設 `{}`）
- **timeout (float)**：HTTP 請求逾時秒數（預設 15 秒）
- **return_full (bool)**：若設為 True，回傳完整 JSON；否則只回傳 `result`

### 錯誤處理
- **RPCError**：若伺服器回傳 `ok=False`、HTTP 非 200、或 JSON 格式錯誤，會丟出此例外。

---

## 4) 推薦目錄結構
```
project/
├─ Multi-Server Page.html   # 前端控制台
├─ config.json              # 預設伺服器清單
└─ client.py                # Python RPC 客戶端
```
