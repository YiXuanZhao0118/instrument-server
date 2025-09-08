# Instrument Control Web – README

## 1) 環境安裝（Windows）

> 目標資料夾假設為 `D:\330A-server`，其中包含 `server_api.py` 與 `instrument_package` 內各個驅動（drivers）。

```powershell
cd "D:\330A-server"
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 2) 啟動 `server_api.py`

### 啟動指令

```powershell
@echo off
REM === 切換到專案目錄 ===
cd /d D:\330A-server

REM === 啟用虛擬環境 ===
call .venv\Scripts\activate.ps1

REM === 啟動 FastAPI 伺服器 ===
python -m uvicorn server_api:app --host 172.30.10.70 --port 9999 --reload

REM === 保持視窗開啟直到手動關閉 ===
pause
```

啟動後：
- 健康檢查：`GET http://172.30.10.70:9999/healthz`
- 文件頁：`http://172.30.10.70:9999/docs`

---

## 2.0 `instrument_server.json` 的資料格式

- `driver`: 驅動清單
- `instrument`: 儀器清單
- `instrument_state`: 儀器當前狀態

---

## 2.1 Drivers：新增、編輯、移除與規範

- 放置於 `instrument_package/*.py`
- 必須包含：
  - `initialize(self) -> dict({"ok": bool, "message": str})`
  - `shutdown(self) -> None`
  - `is_opened(self) -> bool`

API：
- `GET /drivers`
- `POST /drivers/scan`
- `PUT /drivers/file`
- `DELETE /drivers/{driver_id}`

---

## 2.2 Instruments：新增、編輯、移除與規範

結構：
```python
class InstrumentConfig(BaseModel):
    id: Optional[str] = None
    name: str
    driverId: str
    port: Optional[str] = None
    init_args: List[Any] = []
    init_kwargs: Dict[str, Any] = {}
    connect: bool = True
    capabilities: List[CapabilityCall] = []
```

API：
- `GET /instruments`
- `PUT /instruments`
- `DELETE /instruments/{instrument_id}`
- `POST /instruments/{name_or_id}/connect`
- `POST /instruments/{name_or_id}/disconnect`
- `POST /instruments/{name_or_id}/reconnect`

---

## 3.3 儀器控制（RPC）

範例：
```bash
curl -X POST "http://172.30.10.70:9999/rpc" ^
  -H "Content-Type: application/json" ^
  -d "{\"instrument\":\"SpinCore-1\",\"command\":\"get_board_info\",\"args\":[],\"kwargs\":{}}"
```

`client.py` 範例：
```python
import json, urllib.request

def post(ip: str, instrument: str, command: str, args=None, kwargs=None):
    url = f"http://{ip}/rpc"
    payload = {
        "instrument": instrument,
        "command": command,
        "args": args or [],
        "kwargs": kwargs or {}
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))
```

---

## 4) `run_server.bat` 範例

```bat
@echo off
set BASE_DIR=D:\330A-server
set VENV=%BASE_DIR%\.venv
set HOST=172.30.10.70
set PORT=9999

cd /d "%BASE_DIR%"
call "%VENV%\Scripts\activate.bat"
python -m uvicorn server_api:app --host %HOST% --port %PORT% --reload
```
