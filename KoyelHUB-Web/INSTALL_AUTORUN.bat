@echo off
chcp 65001 > nul
title Установка автозапуска Koyel HUB

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET_FILE=%STARTUP_DIR%\KoyelHUB_Startup.vbs"

copy /y "%~dp0START_SILENT.vbs" "%TARGET_FILE%" > nul 2>&1

if exist "%TARGET_FILE%" (
    echo.
    echo ========================================================
    echo  АВТОЗАПУСК УСПЕШНО НАСТРОЕН!
    echo ========================================================
    echo Теперь при каждом включении компьютера:
    echo - Бот KoyelAI запустится автоматически в фоновом режиме
    echo - Окно CMD не появляется (работает через pythonw)
    echo - Панель доступна в браузере: http://localhost:5000
    echo ========================================================
) else (
    echo Ошибка при добавлении в автозагрузку.
)

echo.
pause
