@echo off
chcp 65001 > nul
title Koyel HUB — Web Control Center

cd /d "%~dp0"

echo [1/3] Checking web dependencies (FastAPI, Uvicorn, HTTPX)...
python -m pip install -q -U fastapi uvicorn httpx pydantic

echo [2/3] Opening browser at http://localhost:5000 ...
start http://localhost:5000

echo [3/3] Starting Koyel HUB Web Server...
python web_panel.py

pause
