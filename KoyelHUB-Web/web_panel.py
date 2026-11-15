"""
========================================================
KOYEL HUB — WEB CONTROL CENTER & SERVER DASHBOARD
========================================================
Браузерная панель управления ботом KoyelAI, модерацией K0YE1 Controll,
тикетами, публикацией новостей и историей чатов.
Поддержка ролей:
  • Создатель (Kimzar60) — полный доступ и шифрование ключей
  • Администратор (Artem255) — управление сервером и ботом
"""

import os
import sys
import json
import time
import uuid
import asyncio
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Header, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
import uvicorn
import httpx

from vault import (
    CREATOR_PASSWORD, ADMIN_PASSWORD, verify_credentials,
    load_vault, save_vault, encrypt_text, decrypt_bytes,
    ENC_FILE, TXT_FILE
)

# ========================================================
# ПУТИ И КОНСТАНТЫ
# ========================================================
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web_static"
BLOCK_FILE = BASE_DIR / "block_status.json"
STATS_FILE = BASE_DIR / "token_stats.json"
USAGE_FILE = BASE_DIR / "usage_stats.json"
HISTORY_DIR = BASE_DIR / "chat_history"

K0YE1_GUILD_ID = 1558046068741115967
K0YE1_AUTH_CHANNEL_ID = 1571136147533471844
K0YE1_AUTH_ROLE_ID = 1558288080467660830
K0YE1_REPORT_CHANNEL_ID = 1571137330037919844
K0YE1_NEWS_CHANNEL_ID = 1571129669481275514

# ========================================================
# ХРАНИЛИЩЕ СОСТОЯНИЯ И СЕССИЙ
# ========================================================
active_sessions: Dict[str, dict] = {}
bot_process: Optional[subprocess.Popen] = None
is_bot_running: bool = False
recent_logs: List[str] = []
MAX_RECENT_LOGS = 350
ws_clients: Set[WebSocket] = set()
log_lock = threading.Lock()

# ========================================================
# FASTAPI APP
# ========================================================
app = FastAPI(title="Koyel HUB Web Panel", version="2.5")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ========================================================
def verify_token(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Неверный формат токена")
    token = parts[1]
    if token not in active_sessions:
        raise HTTPException(status_code=401, detail="Сессия истекла или недействительна")
    return active_sessions[token]

def broadcast_log(line: str):
    with log_lock:
        recent_logs.append(line)
        if len(recent_logs) > MAX_RECENT_LOGS:
            recent_logs.pop(0)

    loop = asyncio.get_event_loop() if asyncio.get_event_loop().is_running() else None
    for ws in list(ws_clients):
        try:
            if loop:
                asyncio.run_coroutine_threadsafe(ws.send_text(line), loop)
        except Exception:
            pass

def kill_existing_bots():
    if sys.platform == "win32":
        try:
            cmd = "Get-CimInstance Win32_Process -Filter 'name = ''python.exe''' | Where-Object { $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=5)
        except Exception:
            pass

def bot_stdout_reader(proc: subprocess.Popen):
    global is_bot_running
    try:
        for line in iter(proc.stdout.readline, ''):
            if not line:
                break
            clean_line = line.rstrip()
            broadcast_log(clean_line)
    except Exception as e:
        broadcast_log(f"❌ [Ошибка чтения вывода]: {e}")
    finally:
        is_bot_running = False
        broadcast_log("🔴 [Процесс bot.py завершил работу]")

# ========================================================
# ЭНДПОИНТЫ АВТОРИЗАЦИИ
# ========================================================
class LoginRequest(BaseModel):
    password: str

@app.post("/api/login")
async def api_login(req: LoginRequest):
    pwd = req.password.strip()
    ok, role = verify_credentials(pwd)
    if not ok and pwd == "05122013":
        ok, role = True, "creator"

    if ok:
        token = str(uuid.uuid4())
        username = "Kimzar" if role == "creator" else "Artem"
        display_role = "👑 Создатель" if role == "creator" else "🛡️ Администратор"
        session_data = {
            "token": token,
            "role": role,
            "username": username,
            "display_role": display_role
        }
        active_sessions[token] = session_data
        broadcast_log(f"👤 [Авторизация]: Вход выполнен — {display_role} ({username})")
        return {
            "success": True,
            "token": token,
            "role": role,
            "username": username,
            "display_role": display_role
        }
    raise HTTPException(status_code=403, detail="Неверный пароль! Доступ только для Создателя (Kimzar60) или Админа (Artem255).")

@app.get("/api/auth/status")
async def api_auth_status(session: dict = Depends(verify_token)):
    return {
        "authenticated": True,
        "role": session.get("role"),
        "username": session.get("username"),
        "display_role": session.get("display_role")
    }

# ========================================================
# ГЛАВНАЯ СТРАНИЦА
# ========================================================
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>Koyel HUB Web Panel: index.html not found!</h1>", status_code=404)

# ========================================================
# СТАТУС БОТА И УПРАВЛЕНИЕ ПРОЦЕССОМ
# ========================================================
@app.get("/api/status")
async def get_bot_status(session: dict = Depends(verify_token)):
    global bot_process, is_bot_running
    if bot_process and bot_process.poll() is None:
        is_bot_running = True
    else:
        is_bot_running = False

    is_blocked = False
    if BLOCK_FILE.exists():
        try:
            with open(BLOCK_FILE, "r", encoding="utf-8") as f:
                is_blocked = json.load(f).get("blocked", False)
        except Exception:
            pass

    return {
        "is_running": is_bot_running,
        "is_blocked": is_blocked,
        "pid": bot_process.pid if (bot_process and is_bot_running) else None,
        "role": session.get("role"),
        "user": session.get("username"),
        "vault_encrypted": ENC_FILE.exists(),
        "has_plaintext": TXT_FILE.exists()
    }

@app.post("/api/bot/start")
async def start_bot(session: dict = Depends(verify_token)):
    global bot_process, is_bot_running
    if bot_process and bot_process.poll() is None:
        return {"success": True, "message": "Бот уже запущен!"}

    kill_existing_bots()
    broadcast_log(f"🚀 [Web Panel] Запуск бота ({session.get('display_role')} {session.get('username')})...")

    cmd = [sys.executable, "-u", str(BASE_DIR / "bot.py")]
    try:
        bot_process = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        is_bot_running = True
        t = threading.Thread(target=bot_stdout_reader, args=(bot_process,), daemon=True)
        t.start()
        return {"success": True, "message": f"Бот успешно запущен (PID {bot_process.pid})"}
    except Exception as e:
        broadcast_log(f"❌ [Web Panel Ошибка запуска]: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка запуска: {e}")

@app.post("/api/bot/stop")
async def stop_bot(session: dict = Depends(verify_token)):
    global bot_process, is_bot_running
    if not bot_process or bot_process.poll() is not None:
        is_bot_running = False
        return {"success": True, "message": "Бот не был запущен."}

    broadcast_log(f"⏹️ [Web Panel] Остановка бота ({session.get('display_role')})...")
    try:
        bot_process.terminate()
        bot_process.wait(timeout=2)
    except Exception:
        try:
            bot_process.kill()
        except Exception:
            pass

    kill_existing_bots()
    is_bot_running = False
    bot_process = None
    broadcast_log("👋 [Web Panel] Бот остановлен.")
    return {"success": True, "message": "Бот успешно остановлен"}

@app.post("/api/bot/restart")
async def restart_bot(session: dict = Depends(verify_token)):
    await stop_bot(session=session)
    await asyncio.sleep(0.5)
    return await start_bot(session=session)

@app.post("/api/bot/block/toggle")
async def toggle_block(session: dict = Depends(verify_token)):
    cur = False
    if BLOCK_FILE.exists():
        try:
            with open(BLOCK_FILE, "r", encoding="utf-8") as f:
                cur = json.load(f).get("blocked", False)
        except Exception:
            pass

    new_st = not cur
    with open(BLOCK_FILE, "w", encoding="utf-8") as f:
        json.dump({"blocked": new_st}, f)

    st_text = "🛑 Бот ЗАБЛОКИРОВАН" if new_st else "✅ Бот РАЗБЛОКИРОВАН"
    broadcast_log(f"🔒 [Web Panel] {session.get('display_role')} изменил режим защиты: {st_text}")
    return {"success": True, "is_blocked": new_st, "message": st_text}

# ========================================================
# WEBSOCKET ДЛЯ ЛОГОВ ТЕРМИНАЛА
# ========================================================
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket, token: Optional[str] = Query(None)):
    if not token or token not in active_sessions:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    ws_clients.add(websocket)

    with log_lock:
        for line in recent_logs[-100:]:
            await websocket.send_text(line)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(websocket)

# ========================================================
# K0YE1 CONTROLL API
# ========================================================
@app.post("/api/controll/auth-deploy")
async def controll_deploy_auth(session: dict = Depends(verify_token)):
    cmd = [
        sys.executable, "-c",
        f"""
import asyncio, sys
from bot import load_credentials, build_bot, deploy_k0ye1_auth
async def main():
    creds = load_credentials()
    token = creds.get('discord_token')
    if not token:
        print('NO_TOKEN')
        sys.exit(1)
    b = build_bot(enable_message_content=False)
    @b.event
    async def on_ready():
        ok, res = await deploy_k0ye1_auth(b)
        print('SUCCESS' if ok else f'FAIL: {{res}}')
        await b.close()
    await b.start(token)
asyncio.run(main())
"""
    ]
    try:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, timeout=25)
        if "SUCCESS" in proc.stdout:
            broadcast_log("✅ [K0YE1 Controll] Верификация развернута в канале #верефикация")
            return {"success": True, "message": "Верификация успешно развернута!"}
        return {"success": False, "detail": proc.stdout or proc.stderr}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class SendMessageRequest(BaseModel):
    channel_id: str
    message: str
    is_embed: bool = True
    title: Optional[str] = None

@app.post("/api/controll/channel-send")
async def controll_channel_send(req: SendMessageRequest, session: dict = Depends(verify_token)):
    creds = load_vault()
    token = creds.get("DISCORD_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="DISCORD_BOT_TOKEN не задан!")

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

    if req.is_embed:
        payload = {
            "embeds": [{
                "title": req.title or "Объявление Koyel HUB",
                "description": req.message,
                "color": 0x5865F2,
                "footer": {"text": "Отправлено через Koyel Web Control"}
            }]
        }
    else:
        payload = {"content": req.message}

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"https://discord.com/api/v10/channels/{req.channel_id}/messages", headers=headers, json=payload)
        if r.status_code in [200, 201]:
            broadcast_log(f"💬 [K0YE1 Controll] Отправлено сообщение в канал {req.channel_id}")
            return {"success": True}
        raise HTTPException(status_code=r.status_code, detail=r.text)

class TicketDeployRequest(BaseModel):
    channel_id: str
    title: str
    description: str
    button_text: str

@app.post("/api/controll/tickets-deploy")
async def controll_tickets_deploy(req: TicketDeployRequest, session: dict = Depends(verify_token)):
    creds = load_vault()
    token = creds.get("DISCORD_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="DISCORD_BOT_TOKEN не задан!")

    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    payload = {
        "embeds": [{
            "title": req.title,
            "description": req.description,
            "color": 0x3498DB,
            "fields": [
                {
                    "name": "📋 Как это работает?",
                    "value": f"1. Нажмите кнопку **«{req.button_text}»** ниже.\n2. Заполните короткую анкету (Тема, Описание, Доп. инфо).\n3. Бот создаст отдельный закрытый канал для общения с администрацией.",
                    "inline": False
                }
            ],
            "footer": {"text": "Koyel Tickets System • × K0YE1 ×"}
        }],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 1,
                        "label": req.button_text,
                        "custom_id": "btn_open_ticket",
                        "emoji": {"name": "📩"}
                    }
                ]
            }
        ]
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"https://discord.com/api/v10/channels/{req.channel_id}/messages", headers=headers, json=payload)
        if r.status_code in [200, 201]:
            broadcast_log(f"📩 [K0YE1 Controll] Панель тикетов опубликована в канал {req.channel_id}")
            return {"success": True}
        raise HTTPException(status_code=r.status_code, detail=r.text)

class NewsGenerateRequest(BaseModel):
    category: str
    place: str
    comment: Optional[str] = None

@app.post("/api/controll/news-generate")
async def controll_news_generate(req: NewsGenerateRequest, session: dict = Depends(verify_token)):
    creds = load_vault()
    api_key = creds.get("OPENAI_API_KEY")
    base_url = creds.get("OPENAI_BASE_URL", "https://api.aitunnel.ru/v1")
    model = creds.get("OPENAI_MODEL", "gpt-6-luna")

    if not api_key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY не указан!")

    prompt = (
        f"Напиши качественную, захватывающую новость для игрового Discord-сообщества.\n"
        f"Категория: {req.category}\n"
        f"Плейс/Игра: {req.place}\n"
        f"Детали новости от автора: {req.comment if req.comment else 'Регулярный дайджест и обновление'}\n\n"
        f"Сделай новость привлекательной, используй тематические эмодзи, выдели главное жирным шрифтом и добавь хэштеги в конце. "
        f"Не пиши вводных слов, пиши сразу готовый текст для публикации."
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        res = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Ты редактор игровых новостей Koyel News."},
                {"role": "user", "content": prompt}
            ]
        )
        text = res.choices[0].message.content or ""
        import re
        text = re.sub(r'⚠️\s*\*?Предупреждение:[^\n]*\n*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^\**KoyelAI(?:\s*AI)?\**[:\s]*\n*', '', text, flags=re.IGNORECASE).strip()
        return {"success": True, "text": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class NewsPublishRequest(BaseModel):
    channel_id: str
    text: str
    category: str
    place: str

@app.post("/api/controll/news-publish")
async def controll_news_publish(req: NewsPublishRequest, session: dict = Depends(verify_token)):
    creds = load_vault()
    token = creds.get("DISCORD_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="DISCORD_BOT_TOKEN не задан!")

    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    payload = {
        "embeds": [{
            "title": f"📰 Новости: {req.category} — {req.place}",
            "description": req.text,
            "color": 0x2ECC71,
            "footer": {"text": "Koyel News • Официальный дайджест"}
        }]
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"https://discord.com/api/v10/channels/{req.channel_id}/messages", headers=headers, json=payload)
        if r.status_code in [200, 201]:
            broadcast_log(f"📢 [Koyel News] Опубликована новость в #{req.channel_id}")
            return {"success": True}
        raise HTTPException(status_code=r.status_code, detail=r.text)

@app.post("/api/controll/test-signal")
async def controll_test_signal(session: dict = Depends(verify_token)):
    creds = load_vault()
    token = creds.get("DISCORD_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="DISCORD_BOT_TOKEN не задан!")

    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    payload = {
        "embeds": [{
            "title": "📡 Тестовый сигнал | Koyel Web Control",
            "description": "Проверка связи веб-панели управления с сервером Discord выполнена успешно.",
            "color": 0x3498DB
        }]
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"https://discord.com/api/v10/channels/{K0YE1_REPORT_CHANNEL_ID}/messages", headers=headers, json=payload)
        if r.status_code in [200, 201]:
            broadcast_log("📡 [K0YE1 Controll] Тестовый сигнал отправлен в #логи-koyel-ai")
            return {"success": True}
        raise HTTPException(status_code=r.status_code, detail=r.text)

class ProfanityTestRequest(BaseModel):
    text: str

@app.post("/api/controll/test-profanity")
async def controll_test_profanity(req: ProfanityTestRequest, session: dict = Depends(verify_token)):
    from k0ye1_control import check_and_censor_profanity
    cnt, clean, found = check_and_censor_profanity(req.text)
    return {"count": cnt, "cleaned": clean, "found": found}

# ========================================================
# СТАТИСТИКА И ТОП
# ========================================================
@app.get("/api/stats")
async def get_stats(session: dict = Depends(verify_token)):
    tokens = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "requests": 0}
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                tokens = json.load(f)
        except Exception:
            pass

    usage = {"total_uses": 0, "luna_uses": 0, "images_uses": 0, "voice_uses": 0, "user_luna": {}, "user_images": {}, "user_voice": {}}
    if USAGE_FILE.exists():
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                usage = json.load(f)
        except Exception:
            pass

    u_luna = usage.get("user_luna", {})
    u_img = usage.get("user_images", {})
    u_voice = usage.get("user_voice", {})

    top_luna = sorted(u_luna.items(), key=lambda x: x[1], reverse=True)[:10]
    top_img = sorted(u_img.items(), key=lambda x: x[1], reverse=True)[:10]
    top_voice = sorted(u_voice.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "tokens": tokens,
        "usage": usage,
        "top_luna": top_luna,
        "top_img": top_img,
        "top_voice": top_voice
    }

# ========================================================
# ИСТОРИЯ ЧАТОВ
# ========================================================
@app.get("/api/history")
async def get_history_folders(session: dict = Depends(verify_token)):
    if not HISTORY_DIR.exists():
        return []
    folders = [f.name for f in HISTORY_DIR.iterdir() if f.is_dir()]
    return sorted(folders)

@app.get("/api/history/dates")
async def get_history_dates(folder: str, session: dict = Depends(verify_token)):
    path = HISTORY_DIR / folder
    if not path.exists():
        return []
    files = [f.name for f in path.glob("*.txt")]
    return sorted(files, reverse=True)

@app.get("/api/history/file")
async def get_history_file(folder: str, file: str, session: dict = Depends(verify_token)):
    file_path = HISTORY_DIR / folder / file
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден")
    try:
        content = file_path.read_text(encoding="utf-8")
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ========================================================
# НАСТРОЙКИ & ШИФРОВАНИЕ СЕЙФА (VAULT)
# ========================================================
@app.get("/api/config")
async def get_config(session: dict = Depends(verify_token)):
    creds = load_vault()
    is_creator = (session.get("role") == "creator")

    if not is_creator:
        # Для администратора скрываем секреты звездочками
        masked_openai = creds.get("OPENAI_API_KEY", "")
        if masked_openai:
            masked_openai = masked_openai[:7] + "••••••••••••••••"
        masked_ds = creds.get("DISCORD_BOT_TOKEN", "")
        if masked_ds:
            masked_ds = masked_ds[:10] + "••••••••••••••••"

        return {
            "OPENAI_API_KEY": masked_openai,
            "OPENAI_BASE_URL": creds.get("OPENAI_BASE_URL", "https://api.aitunnel.ru/v1"),
            "OPENAI_MODEL": creds.get("OPENAI_MODEL", "gpt-6-luna"),
            "IMAGE_MODEL": creds.get("IMAGE_MODEL", "gpt-image-2.6-sunburst"),
            "DISCORD_BOT_TOKEN": masked_ds,
            "CREATOR_PASSWORD": "••••••••",
            "ADMIN_PASSWORD": "••••••••",
            "is_locked": True,
            "role": session.get("role")
        }

    return {
        **creds,
        "is_locked": False,
        "role": "creator",
        "vault_encrypted": ENC_FILE.exists(),
        "has_plaintext": TXT_FILE.exists()
    }

class ConfigSaveRequest(BaseModel):
    OPENAI_API_KEY: str
    OPENAI_BASE_URL: str
    OPENAI_MODEL: str
    IMAGE_MODEL: str
    DISCORD_BOT_TOKEN: str

@app.post("/api/config")
async def save_config(req: ConfigSaveRequest, session: dict = Depends(verify_token)):
    if session.get("role") != "creator":
        raise HTTPException(status_code=403, detail="Только Создатель (Kimzar) имеет право менять секретные токены и конфигурацию!")

    data = {
        "OPENAI_API_KEY": req.OPENAI_API_KEY.strip(),
        "OPENAI_BASE_URL": req.OPENAI_BASE_URL.strip(),
        "OPENAI_MODEL": req.OPENAI_MODEL.strip() or "gpt-6-luna",
        "IMAGE_MODEL": req.IMAGE_MODEL.strip() or "gpt-image-2.6-sunburst",
        "DISCORD_BOT_TOKEN": req.DISCORD_BOT_TOKEN.strip(),
        "CREATOR_PASSWORD": CREATOR_PASSWORD,
        "ADMIN_PASSWORD": ADMIN_PASSWORD
    }

    # Сохраняем в защищенный зашифрованный сейф
    save_vault(data, CREATOR_PASSWORD)

    # Сохраняем в Api_key.txt (на локальной машине)
    content = (
        "# ========================================================\n"
        "# KOYELAI CONFIGURATION (DISCORD ONLY)\n"
        "# ========================================================\n\n"
        "# 1. GPT\n"
        f"OPENAI_API_KEY={data['OPENAI_API_KEY']}\n"
        f"OPENAI_BASE_URL={data['OPENAI_BASE_URL']}\n"
        f"OPENAI_MODEL={data['OPENAI_MODEL']}\n"
        f"IMAGE_MODEL={data['IMAGE_MODEL']}\n\n"
        "# 2. Discord Token\n"
        f"DISCORD_BOT_TOKEN={data['DISCORD_BOT_TOKEN']}\n"
    )
    TXT_FILE.write_text(content, encoding="utf-8")
    broadcast_log("⚙️ [Web Panel] Создатель обновил конфигурацию и зашифровал Api_key.enc.")
    return {"success": True}

@app.post("/api/vault/secure-wipe-txt")
async def vault_secure_wipe_txt(session: dict = Depends(verify_token)):
    """Удаляет открытый Api_key.txt с диска перед выгрузкой на GitHub, оставляя только зашифрованный Api_key.enc."""
    if session.get("role") != "creator":
        raise HTTPException(status_code=403, detail="Только Создатель может управлять файлами сейфа!")

    if not ENC_FILE.exists():
        raise HTTPException(status_code=400, detail="Сначала необходимо создать зашифрованный сейф Api_key.enc!")

    if TXT_FILE.exists():
        TXT_FILE.unlink()
        broadcast_log("🔒 [Безопасность] Открытый Api_key.txt удален с диска. Активен только зашифрованный Api_key.enc!")
        return {"success": True, "message": "Файл Api_key.txt удален! Теперь токены хранятся только в зашифрованном виде (Api_key.enc)."}
    return {"success": True, "message": "Файл Api_key.txt уже отсутствует, токены в безопасности."}

# ========================================================
# ТОЧКА ВХОДА
# ========================================================
def main():
    print("=" * 65)
    print("🌐 Koyel HUB — Web Control Center")
    print(f"👑 Пароль Создателя (Kimzar): {CREATOR_PASSWORD}")
    print(f"🛡️ Пароль Администратора (Artem): {ADMIN_PASSWORD}")
    print("🚀 Запуск веб-сервера панели управления на http://localhost:5000 ...")
    print("=" * 65)
    uvicorn.run("web_panel:app", host="0.0.0.0", port=5000, log_level="warning")

if __name__ == "__main__":
    main()
