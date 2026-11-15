@echo off
chcp 65001 > nul
title KoyelAI Discord Bot

cd /d "%~dp0"

echo [1/3] Closing any old KoyelAI bot instances...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'name = ''python.exe''' | Where-Object { $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" > nul 2>&1

echo [2/3] Checking dependencies...
python -m pip install -q -U discord.py google-genai openai Pillow httpx gTTS imageio-ffmpeg hachoir

echo [3/3] Starting KoyelAI Discord Bot...
python -u bot.py

pause
