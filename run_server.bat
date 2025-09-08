@echo off
REM === 切換到專案目錄 ===
cd /d D:\330A-server\server_api.py

REM === 啟用虛擬環境 ===
call .venv\Scripts\activate.bat

REM === 啟動 FastAPI 伺服器 ===
python -m uvicorn server_api:app --host 172.30.10.70 --port 9999 --reload

REM === 保持視窗開啟直到手動關閉 ===
pause
