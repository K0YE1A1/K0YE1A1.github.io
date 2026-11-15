import os
import sys
import io
import time
import json
import base64
import re
import socket
import subprocess
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Tuple
import logging

import discord
from discord import app_commands
from discord.ext import commands
import httpx
from PIL import Image, ImageOps
from gtts import gTTS
from openai import OpenAI

BASE_DIR = Path(__file__).parent.resolve()
API_KEY_FILE = BASE_DIR / "Api_key.txt"
ASSETS_DIR = BASE_DIR / "assets"
WM_16_9_PATH = ASSETS_DIR / "watermark_16_9.png"
WM_9_16_PATH = ASSETS_DIR / "watermark_9_16.png"
STATS_FILE = BASE_DIR / "token_stats.json"
USAGE_FILE = BASE_DIR / "usage_stats.json"
BLOCK_FILE = BASE_DIR / "block_status.json"
HISTORY_DIR = BASE_DIR / "chat_history"
HISTORY_DIR.mkdir(exist_ok=True)

TEXT_MODEL = "gpt-6-luna"
IMAGE_MODEL = "gpt-image-2.6-sunburst"
OWNER_PASSWORD = "Kimzar60"
ADMIN_PASSWORD = "Artem255"
BLOCK_PASSWORD = OWNER_PASSWORD
VALID_BLOCK_PASSWORDS = {OWNER_PASSWORD, ADMIN_PASSWORD, "05122013"}
DIALOGUE_TIMEOUT = 180  # 3 минуты активного диалога

# --- K0YE1 Controll (Верификация / Аутентификация / Логи / Тикеты / Новости) ---
K0YE1_GUILD_ID = 1558046068741115967
K0YE1_AUTH_CHANNEL_ID = 1571136147533471844
K0YE1_REPORT_CHANNEL_ID = 1571137330037919844
K0YE1_AUTH_ROLE_ID = 1558288080467660830
K0YE1_NEWS_CHANNEL_ID = 1571129669481275514
MAX_PROFANITY_LIMIT = 10

_lock_socket = None

def acquire_instance_lock() -> bool:
    """Гарантирует запуск только одного экземпляра бота на компьютере, предотвращая дублирование ответов."""
    global _lock_socket
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(('127.0.0.1', 49250))
        _lock_socket.listen(1)
        return True
    except Exception:
        return False

active_discord_dialogues = {}
processed_message_ids = set()
user_spam_tracker = {}

TRIGGER_WORDS = [
    "койл аи", "койл ии", "койл ai", "койл ii",
    "koyel ai", "koyel ii", "koyelai",
    "койл", "koyel"
]

def load_credentials():
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base_url = os.getenv("OPENAI_BASE_URL", "https://api.aitunnel.ru/v1").strip()
    discord_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()

    # Сначала проверяем зашифрованное хранилище vault.py
    try:
        from vault import load_vault
        v_data = load_vault()
        openai_key = v_data.get("OPENAI_API_KEY") or openai_key
        openai_base_url = v_data.get("OPENAI_BASE_URL") or openai_base_url
        discord_token = v_data.get("DISCORD_BOT_TOKEN") or discord_token
    except Exception:
        pass

    if API_KEY_FILE.exists():
        with open(API_KEY_FILE, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip().upper()
                    val = val.strip().strip('"').strip("'")
                    if key == "OPENAI_API_KEY":
                        openai_key = val or openai_key
                    elif key == "OPENAI_BASE_URL":
                        openai_base_url = val or openai_base_url
                    elif key in ("DISCORD_BOT_TOKEN", "DISCORD_TOKEN"):
                        discord_token = val or discord_token
                    elif "DISCORD" in key:
                        discord_token = val or discord_token
                else:
                    if not openai_key and (line.startswith("sk-") or "aitunnel" in line.lower()):
                        openai_key = line
                    elif not discord_token and line.startswith("MTU") and len(line) > 50:
                        discord_token = line

    return {
        "openai_key": openai_key,
        "openai_base_url": openai_base_url or "https://api.aitunnel.ru/v1",
        "discord_token": discord_token,
    }

CONFIG = load_credentials()

# --- Состояние блокировки бота ---
def is_bot_blocked() -> bool:
    if BLOCK_FILE.exists():
        try:
            with open(BLOCK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("blocked", False)
        except Exception:
            pass
    return False

def set_bot_blocked(blocked: bool):
    try:
        with open(BLOCK_FILE, "w", encoding="utf-8") as f:
            json.dump({"blocked": blocked}, f)
    except Exception as e:
        print(f"Ошибка сохранения состояния блокировки: {e}")

def estimate_tokens(text: str) -> int:
    clean = text.strip()
    words = len(clean.split())
    char_tokens = int(len(clean) / 3.2)
    return max(words, char_tokens)

def sanitize_folder_name(name: str) -> str:
    clean = "".join(c if c not in '\\/:*?"<>|' else "_" for c in name).strip()
    return clean or "General_Chat"

def get_chat_name(channel, guild=None) -> str:
    if channel and hasattr(channel, "name") and channel.name:
        return str(channel.name)
    if guild and hasattr(guild, "name") and guild.name:
        return str(guild.name)
    if channel and hasattr(channel, "recipient") and channel.recipient:
        return f"DM_{channel.recipient.name}"
    if channel and hasattr(channel, "recipients") and channel.recipients:
        names = [r.name for r in channel.recipients if hasattr(r, "name")]
        if names:
            return f"Group_{'_'.join(names)}"
    return "Private_Chat"

def get_tg_chat_name(chat) -> str:
    if not chat:
        return "Telegram_Chat"
    if hasattr(chat, "title") and chat.title:
        return str(chat.title)
    if hasattr(chat, "username") and chat.username:
        return f"TG_{chat.username}"
    if hasattr(chat, "first_name") and chat.first_name:
        name = chat.first_name
        if hasattr(chat, "last_name") and chat.last_name:
            name += f" {chat.last_name}"
        return f"TG_{name}"
    return f"TG_Chat_{getattr(chat, 'id', 'unknown')}"

# --- Логирование истории сообщений на ПК по папкам чатов ---
def log_chat_message(chat_name: str, username: str, content: str, custom_time: Optional[datetime] = None):
    now = custom_time or datetime.now()
    # Исключаем создание файлов старше даты открытия бота (14.11.2026)
    if hasattr(now, "year") and (now.year < 2026 or (now.year == 2026 and (now.month < 11 or (now.month == 11 and now.day < 14)))):
        return

    folder_name = sanitize_folder_name(chat_name)
    chat_folder = HISTORY_DIR / folder_name
    chat_folder.mkdir(parents=True, exist_ok=True)

    filename = now.strftime("%d.%m.%Y.txt")
    filepath = chat_folder / filename
    time_str = now.strftime("%d.%m.%Y %H:%M:%S")

    entry = (
        f"Канал: {chat_name}\n"
        f"Юзернейм: {username}\n"
        f"Что написал: {content.strip()}\n"
        f"Время: {time_str}\n"
        "----------------------------------------\n"
    )

    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"Ошибка записи лога: {e}")

def get_local_chat_history(chat_name: str, limit: int = 40) -> str:
    folder_name = sanitize_folder_name(chat_name)
    chat_folder = HISTORY_DIR / folder_name
    entries = []
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    for target_date in [today, yesterday]:
        fp = chat_folder / target_date.strftime("%d.%m.%Y.txt")
        if fp.exists():
            try:
                parts = fp.read_text(encoding="utf-8").split("----------------------------------------\n")
                for p in parts:
                    if p.strip():
                        entries.append(p.strip())
            except Exception:
                pass
    return "\n----------------------------------------\n".join(entries[-limit:])

async def get_combined_chat_history(channel, guild=None, limit: int = 50) -> str:
    """
    Получает живую историю сообщений из текущего чата Discord,
    сохраняет её в соответствующую папку chat_history/<Chat>/<Date>.txt
    и возвращает отформатированный текст для ИИ.
    """
    chat_name = get_chat_name(channel, guild)
    entries = []

    # 1. Считываем свежие сообщения напрямую из канала Discord
    if channel and hasattr(channel, "history"):
        try:
            raw_msgs = []
            async for msg in channel.history(limit=limit):
                raw_msgs.append(msg)

            # Сообщения приходят от новых к старым, разворачиваем в хронологический порядок
            for msg in reversed(raw_msgs):
                if msg.content:
                    author = msg.author.name
                    time_str = msg.created_at.strftime("%d.%m.%Y %H:%M:%S")
                    entry_text = f"Юзернейм: {author}\nЧто написал: {msg.content}\nВремя: {time_str}"
                    entries.append(entry_text)
                    # Также сохраняем в локальную папку
                    log_chat_message(chat_name, author, msg.content, msg.created_at)
        except Exception:
            pass

    # 2. Если live history не удалось получить (например, нет прав), читаем локальный файл
    if not entries:
        return get_local_chat_history(chat_name, limit=40)

    return "\n----------------------------------------\n".join(entries[-40:])

# --- Статистика токенов ---
def load_token_stats() -> dict:
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "requests": 0}

def add_tokens(prompt_tokens: int, completion_tokens: int):
    stats = load_token_stats()
    stats["prompt_tokens"] += prompt_tokens
    stats["completion_tokens"] += completion_tokens
    stats["total_tokens"] += (prompt_tokens + completion_tokens)
    stats["requests"] += 1
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения статистики токенов: {e}")

def get_token_message() -> str:
    stats = load_token_stats()
    total = stats.get("total_tokens", 0)
    prompt_tokens = stats.get("prompt_tokens", 0)
    completion_tokens = stats.get("completion_tokens", 0)
    requests_count = stats.get("requests", 0)

    return (
        f"📊 **KoyelAI Статистика токенов**\n\n"
        f"На **Chat GPT Luna** затратилось **{total:,}** токенов.\n"
        f"• Входящие токены (промпты): {prompt_tokens:,}\n"
        f"• Исходящие токены (ответы): {completion_tokens:,}\n"
        f"• Всего запросов: {requests_count:,}"
    )

# --- Статистика использований ---
def load_usage_stats() -> dict:
    if USAGE_FILE.exists():
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "total_uses": 0,
        "luna_uses": 0,
        "images_uses": 0,
        "voice_uses": 0,
        "user_luna": {},
        "user_images": {},
        "user_voice": {}
    }

def record_usage(username: str, model_type: str):
    stats = load_usage_stats()
    stats["total_uses"] = stats.get("total_uses", 0) + 1

    if model_type == "luna":
        stats["luna_uses"] = stats.get("luna_uses", 0) + 1
        stats.setdefault("user_luna", {})[username] = stats.setdefault("user_luna", {}).get(username, 0) + 1
    elif model_type == "images":
        stats["images_uses"] = stats.get("images_uses", 0) + 1
        stats.setdefault("user_images", {})[username] = stats.setdefault("user_images", {}).get(username, 0) + 1
    elif model_type == "voice":
        stats["voice_uses"] = stats.get("voice_uses", 0) + 1
        stats.setdefault("user_voice", {})[username] = stats.setdefault("user_voice", {}).get(username, 0) + 1

    try:
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения статистики: {e}")

def format_uses_message() -> str:
    stats = load_usage_stats()
    total = stats.get("total_uses", 0)
    luna = stats.get("luna_uses", 0)
    images = stats.get("images_uses", 0)
    voice = stats.get("voice_uses", 0)

    u_luna = stats.get("user_luna", {})
    if u_luna:
        best_luna = max(u_luna.items(), key=lambda x: x[1])
        leader_luna_str = f"{best_luna[0]} ({best_luna[1]})"
    else:
        leader_luna_str = "нет (0)"

    u_images = stats.get("user_images", {})
    if u_images:
        best_img = max(u_images.items(), key=lambda x: x[1])
        leader_img_str = f"{best_img[0]} ({best_img[1]})"
    else:
        leader_img_str = "нет (0)"

    u_voice = stats.get("user_voice", {})
    if u_voice:
        best_voice = max(u_voice.items(), key=lambda x: x[1])
        leader_voice_str = f"{best_voice[0]} ({best_voice[1]})"
    else:
        leader_voice_str = "нет (0)"

    return (
        f"📊 **KoyelAI Статистика использований:**\n\n"
        f"• Всего запросов: **{total:,}**\n"
        f"• Вызовы GPT-6 Luna: **{luna:,}**\n"
        f"• Генерации фото 1080P: **{images:,}**\n"
        f"• Озвучка Voice AI: **{voice:,}**\n\n"
        f"🏆 **Лидеры:**\n"
        f"• GPT-6 Luna: **{leader_luna_str}**\n"
        f"• GPT Images: **{leader_img_str}**\n"
        f"• Voice AI: **{leader_voice_str}**"
    )

def get_commands_help_message() -> str:
    return (
        "🤖 **Список команд KoyelAI:**\n\n"
        "💬 `/textai [промпт]` — спросить текстовую модель GPT-6 Luna\n"
        "🎨 `/photoai [16:9|9:16] [промпт]` — сгенерировать 1080P фото с водяным знаком\n"
        "🎙 `/voiceai [промпт]` — голосовой ответ (озвучка сообщения)\n"
        "💖 `/sponsor` — поддержать ИИ бота (баланс, стоимость генераций и помощь проекту)\n"
        "🔒 `/block [пароль]` — блокировка / разблокировка бота (для администратора)\n"
        "📊 `/uses` — статистика использований моделей и рейтинг пользователей\n"
        "🪙 `/token` — статистика затраченных токенов\n"
        "📋 `/commands` или `/help` или `/start` — показать этот список команд\n\n"
        "💡 **Секретный режим:**\n"
        "Добавьте `secret` или `секрет` (например: `/textai secret твой вопрос`), чтобы бот ответил, скрыв сам промпт в сообщении.\n\n"
        "👥 **В чатах и группах:**\n"
        "Можно писать без слэша: *«койл ...»*, *«koyel ...»*, отвечать на сообщения бота или упоминать его."
    )

openai_client = None
def get_openai():
    global openai_client
    if not openai_client:
        cfg = load_credentials()
        if cfg["openai_key"]:
            openai_client = OpenAI(
                api_key=cfg["openai_key"],
                base_url=cfg["openai_base_url"]
            )
    return openai_client

def apply_watermark_and_format(raw_bytes: bytes, aspect_ratio: str) -> bytes:
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")

    if aspect_ratio == "9:16":
        target_size = (1080, 1920)
        wm_path = WM_9_16_PATH
        pos = (0, 1113)
    else:
        target_size = (1920, 1080)
        wm_path = WM_16_9_PATH
        pos = (0, 825)

    fitted_img = ImageOps.fit(img, target_size, method=Image.Resampling.LANCZOS)

    if wm_path.exists():
        wm = Image.open(wm_path).convert("RGBA")
        fitted_img.paste(wm, pos, wm)

    out = io.BytesIO()
    fitted_img.convert("RGB").save(out, format="PNG", quality=95)
    return out.getvalue()

def generate_image(prompt: str, aspect_ratio: str) -> Tuple[bytes, str]:
    client = get_openai()
    if not client:
        raise RuntimeError("OPENAI_API_KEY не указан в Api_key.txt!")

    try:
        res = client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            response_format="b64_json",
            n=1
        )
    except Exception:
        # Резервная попытка со стандартным вызовом модели
        res = client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            n=1
        )

    raw_bytes = None
    if hasattr(res.data[0], "b64_json") and res.data[0].b64_json:
        raw_bytes = base64.b64decode(res.data[0].b64_json)
    elif hasattr(res.data[0], "url") and res.data[0].url:
        with httpx.Client(timeout=30.0) as hclient:
            r = hclient.get(res.data[0].url)
            raw_bytes = r.content

    if not raw_bytes:
        raise RuntimeError(f"Не удалось получить изображение от модели {IMAGE_MODEL}.")

    final_img = apply_watermark_and_format(raw_bytes, aspect_ratio)
    return final_img, IMAGE_MODEL

def clean_ai_answer(text: str) -> str:
    """Удаляет любые галлюцинированные паразитные префиксы, предупреждения о токенах и шаблонные рамки."""
    if not text:
        return ""
    # 1. Удаляем любые фейковые предупреждения о токенах в любом месте текста
    text = re.sub(r'⚠️\s*\*?Предупреждение:[^\n]*\n*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'.*?(?:сожрет|лимит токенов|запрос больше \d+ токенов)[^\n]*\n*', '', text, flags=re.IGNORECASE)
    # 2. Если модель вывела имя бота в начале сообщения
    text = re.sub(r'^\**KoyelAI(?:\s*AI)?\**[:\s]*\n*', '', text, flags=re.IGNORECASE)
    # 3. Если модель скопировала шаблон "Промпт: ... Ответ: ..."
    if "Ответ:" in text:
        parts = re.split(r'\**Ответ:\**\s*', text, maxsplit=1)
        if len(parts) == 2 and any(p in parts[0] for p in ["Промпт:", "промпт:", "Prompt:"]):
            text = parts[1]
    return text.strip()

def call_gpt_luna(messages: List[dict], chat_context: str = "", chat_name: str = "") -> str:
    client = get_openai()
    if not client:
        raise RuntimeError("OPENAI_API_KEY не указан в Api_key.txt!")

    system_instruction = (
        "Ты — искусственный интеллект KoyelAI.\n"
        f"Сейчас ты находишься и общаешься в чате/канале: '{chat_name}'.\n\n"
        "ВАЖНЫЕ ЗНАНИЯ О СЕБЕ И ТВОЕМ ОКРУЖЕНИИ:\n"
        "1. Ты работаешь локально на компьютере пользователя и прекрасно это знаешь! Твой код запущен на его ПК, а история переписки сохраняется на локальном диске в папке chat_history по датам. Если тебя спрашивают, где ты хостишься или сохраняешь ли данные на ПК — открыто и честно подтверждай: да, ты хостишься локально на компьютере пользователя и логируешь чаты на его жесткий диск.\n"
        "2. Если тебя спрашивают: 'кто ты', 'какая ты модель', 'как тебя зовут', 'кто тебя создал' — ты ВСЕГДА четко отвечаешь, что ты — KoyelAI (модель KoyelAI). Никогда не называй себя ChatGPT, GPT-4, GPT-6 или моделью OpenAI. Твое имя — KoyelAI.\n"
        "3. Ты открытый, честный и прямолинейный собеседник. Ты отлично разбираешься в играх (Minecraft, Rust и др.), лаунчерах и софте. В спорах о Minecraft-лаунчерах (TLauncher / Milida и скандалах с вирусами/рекламой) не пытайся отмазывать сомнительный софт, будь объективен, честен и на стороне игрока.\n"
        "4. Ты поддерживаешь живой непрерывный групповой диалог. В канале могут общаться сразу несколько участников. Ты прекрасно понимаешь, кто какую реплику написал (в контексте указаны имена и юзернеймы каждого), обращайся к участникам по имени, отвечай на вопросы нескольких людей одновременно и веди единую логичную беседу со всеми собеседниками.\n"
        "5. СТРОГИЙ ЗАПРЕТ: НИКОГДА не пиши в начале ответа 'KoyelAI', 'Промпт:' или 'Ответ:' — пиши сразу суть твоего ответа человеку без каких-либо префиксов и рамок."
    )

    if chat_context:
        system_instruction += (
            f"\n\nВот РЕАЛЬНЫЕ сообщения участников из этого чата ('{chat_name}'):\n"
            "--- НАЧАЛО ИСТОРИИ ЧАТА ---\n"
            f"{chat_context}\n"
            "--- КОНЕЦ ИСТОРИИ ЧАТА ---\n\n"
            f"Ты точно знаешь, в каком ты чате ('{chat_name}'). "
            "Используй историю сообщений участников выше, чтобы подробно и точно отвечать на вопросы пользователя "
            "(например: кто что писал, о чем говорили, кто активнее, кто больше играет и т.д.)."
        )

    final_messages = [{"role": "system", "content": system_instruction}]
    final_messages.extend(messages)

    res = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=final_messages
    )

    if res.usage:
        p_tokens = res.usage.prompt_tokens or 0
        c_tokens = res.usage.completion_tokens or 0
        add_tokens(p_tokens, c_tokens)

    raw_answer = res.choices[0].message.content or ""
    cleaned = clean_ai_answer(raw_answer)
    return cleaned or "⚠️ Пустой ответ."

def convert_to_ogg_opus(audio_bytes: bytes) -> Tuple[bytes, int]:
    duration = 1
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        p = subprocess.Popen(
            [ffmpeg_exe, "-y", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        out, err = p.communicate(input=audio_bytes)
        err_str = err.decode("utf-8", errors="ignore")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", err_str)
        if m:
            h, mn, s = m.groups()
            duration = max(1, int(float(h) * 3600 + float(mn) * 60 + float(s)))
        if p.returncode == 0 and len(out) > 0:
            return out, duration
    except Exception as e:
        print(f"Ошибка конвертации в OGG Opus: {e}")
    return audio_bytes, duration

def synthesize_voice(text: str) -> Tuple[bytes, int]:
    client = get_openai()
    clean_text = text[:4000]

    if client:
        try:
            res = client.audio.speech.create(
                model="tts-1",
                voice="alloy",
                input=clean_text,
                response_format="opus"
            )
            raw = None
            if hasattr(res, "content") and res.content:
                raw = res.content
            elif hasattr(res, "read"):
                raw = res.read()
            if raw:
                return convert_to_ogg_opus(raw)
        except Exception as e:
            print(f"Ошибка OpenAI TTS: {e}. Резервный gTTS...")

    tts = gTTS(text=clean_text[:1000], lang="ru")
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    mp3_data = buf.getvalue()
    return convert_to_ogg_opus(mp3_data)

async def download_url(url: Optional[str]) -> Tuple[Optional[bytes], Optional[str]]:
    if not url:
        return None, None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                mime = resp.headers.get("content-type", "application/octet-stream").split(";")[0]
                return resp.content, mime
    except Exception as e:
        print(f"Ошибка загрузки url {url}: {e}")
    return None, None

async def send_formatted_reply(target, prompt: str, answer: str, show_prompt: bool = True):
    answer = clean_ai_answer(answer)
    if show_prompt:
        body = f"**KoyelAI**\n**Промпт:** {prompt}\n**Ответ:** {answer}"
        header = f"**KoyelAI**\n**Промпт:** {prompt}\n**Ответ:** "
    else:
        body = f"**KoyelAI**\n**Ответ:** {answer}"
        header = f"**KoyelAI**\n**Ответ:** "

    if len(body) <= 2000:
        if isinstance(target, discord.Interaction):
            await target.followup.send(body)
        else:
            await target.reply(body)
    else:
        prefix_len = len(header)
        first_chunk = answer[: 2000 - prefix_len - 20]
        remaining = answer[len(first_chunk):]

        if isinstance(target, discord.Interaction):
            await target.followup.send(f"{header}{first_chunk}...")
            channel = target.channel
        else:
            await target.reply(f"{header}{first_chunk}...")
            channel = target.channel

        while remaining:
            chunk = remaining[:1950]
            remaining = remaining[len(chunk):]
            await channel.send(chunk)

def extract_trigger_prompt(text: str) -> Optional[str]:
    import re
    t_clean = text.strip()
    for tr in TRIGGER_WORDS:
        pattern = r"^" + re.escape(tr) + r"[\s,:]*(.*)$"
        m = re.match(pattern, t_clean, re.IGNORECASE)
        if m:
            extracted = m.group(1).strip()
            return extracted if extracted else "привет"
    return None

# --- Модуль фильтрации триггеров и матов (K0YE1 Controll) ---
PROFANITY_PATTERNS = [
    r'\b[а-яёa-z]*[хx][уy][йjеёяюи][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[хx][уy][лl][иея][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[пp][иeеi][зz3][дd][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[еeё][бb][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[бb][лl][яy][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[сc][уy][кk][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[пp][иeеi][дd][аоo][рr][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[пp][еe][дd][иe][кk][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[гg][аоao][нn][дd][оo][нn][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[мm][уy][дd][аa][кkч][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[зz][аa][лl][уy][пp][а-яёa-z]*\b',
    r'\b[а-яёa-z]*[шs][лl][юy][хx][а-яёa-z]*\b',
    r'\b(?:пох|нах|вых|нахуй|похуй)\b'
]
COMPILED_PROFANITY = [re.compile(p, re.IGNORECASE) for p in PROFANITY_PATTERNS]
EXCLUDED_PROFANITY_STEMS = (
    'колеб', 'потреб', 'оскорб', 'стебел', 'рубл', 'влюбл', 'хлеб', 'сабл',
    'дублик', 'тереб', 'погреб', 'лебед', 'хребет', 'греб'
)

def check_and_censor_profanity(text: str) -> Tuple[int, str, List[str]]:
    words = re.findall(r'\b[а-яёa-z0-9_-]+\b', text, re.IGNORECASE)
    found = []
    for w in words:
        w_low = w.lower()
        if any(exc in w_low for exc in EXCLUDED_PROFANITY_STEMS):
            continue
        if any(pat.fullmatch(w) for pat in COMPILED_PROFANITY):
            found.append(w)

    censored = text
    for w in sorted(set(found), key=len, reverse=True):
        pattern = re.compile(rf'\b{re.escape(w)}\b', re.IGNORECASE)
        censored = pattern.sub('***', censored)

    return len(found), censored, found

class K0ye1AuthView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Аутентификация",
        style=discord.ButtonStyle.success,
        emoji="🛡️",
        custom_id="k0ye1_control_auth_button"
    )
    async def auth_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ Доступно только на сервере!", ephemeral=True)
            return

        role = guild.get_role(K0YE1_AUTH_ROLE_ID)
        if not role:
            await interaction.response.send_message("❌ Ошибка: целевая роль верификации не найдена.", ephemeral=True)
            return

        member = interaction.user
        if role in member.roles:
            await interaction.response.send_message("ℹ️ **Вы уже прошли аутентификацию!**\nРоль **Верифицирован** уже у вас есть.", ephemeral=True)
            return

        try:
            await member.add_roles(role, reason="K0YE1 Controll: Успешная аутентификация пользователя")
            await interaction.response.send_message(
                f"✅ **Аутентификация успешно пройдена!**\nВам выдана роль **{role.name}**. Приятного общения на сервере **× K0YE1 ×**!",
                ephemeral=True
            )
            # Отправка отчета в отчетный канал
            report_channel = guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
            if report_channel:
                log_embed = discord.Embed(
                    title="🛡️ K0YE1 Controll | Новый участник верифицирован",
                    description=f"Пользователь {member.mention} (`{member.name}`) нажал кнопку **Аутентификация** и получил роль **{role.name}**.",
                    color=0x2ECC71,
                    timestamp=datetime.now()
                )
                if hasattr(member, "display_avatar") and member.display_avatar:
                    log_embed.set_thumbnail(url=member.display_avatar.url)
                log_embed.set_footer(text="K0YE1 Controll System")
                await report_channel.send(embed=log_embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав для выдачи роли (проверьте позицию роли бота на сервере).", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка при выдаче роли:\n```{e}```", ephemeral=True)

async def deploy_k0ye1_auth(client: discord.Client) -> Tuple[bool, str]:
    guild = client.get_guild(K0YE1_GUILD_ID)
    if not guild:
        try:
            guild = await client.fetch_guild(K0YE1_GUILD_ID)
        except Exception as e:
            return False, f"Сервер не найден: {e}"

    auth_channel = guild.get_channel(K0YE1_AUTH_CHANNEL_ID)
    if not auth_channel:
        try:
            auth_channel = await client.fetch_channel(K0YE1_AUTH_CHANNEL_ID)
        except Exception as e:
            return False, f"Канал верификации не найден: {e}"

    report_channel = guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
    if not report_channel:
        try:
            report_channel = await client.fetch_channel(K0YE1_REPORT_CHANNEL_ID)
        except Exception:
            pass

    role = guild.get_role(K0YE1_AUTH_ROLE_ID)

    # 1. Вычистить все сообщения из канала
    try:
        deleted = await auth_channel.purge(limit=100)
    except Exception as e:
        deleted = []
        print(f"Ошибка очистки канала: {e}")

    # 2. Отправить embed с кнопкой
    embed = discord.Embed(
        title="🛡️ ВЕРИФИКАЦИЯ • K0YE1 CONTROLL",
        description=(
            "Добро пожаловать на сервер **× K0YE1 ×**!\n\n"
            "Для защиты сообщества от спама и ботов на сервере включена обязательная аутентификация.\n"
            "Чтобы подтвердить, что вы не бот, и получить доступ к серверу, ознакомьтесь с правилами ниже и нажмите кнопку **«Аутентификация»**."
        ),
        color=0x2ECC71
    )
    embed.add_field(
        name="📜 Правила сообщества",
        value=(
            "• Соблюдайте уважение ко всем участникам и администрации.\n"
            "• Запрещены спам, реклама, оскорбления и вредоносные ссылки.\n"
            "• Запрещено использование сторонних читов/софта при совместной игре."
        ),
        inline=False
    )
    embed.add_field(
        name="⚡ Завершение верификации",
        value="Нажмите на зелёную кнопку **«Аутентификация»** ниже.\nВам сразу будет выдана роль **Верифицирован**, и откроются все каналы сервера!",
        inline=False
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="K0YE1 Controll • Защита сервера активна")

    await auth_channel.send(embed=embed, view=K0ye1AuthView())

    # 3. Отправить отчет в отчетный канал
    if report_channel:
        role_mention = f"<@&{role.id}> (`{role.name}`)" if role else str(K0YE1_AUTH_ROLE_ID)
        report_embed = discord.Embed(
            title="🛡️ K0YE1 Controll | Отчет о настройке верификации",
            description=(
                f"✅ **Система аутентификации K0YE1 Controll успешно развернута!**\n\n"
                f"• **Канал верификации:** <#{auth_channel.id}> (`{auth_channel.name}`)\n"
                f"• **Очищено старых сообщений:** {len(deleted)}\n"
                f"• **Выдаваемая роль:** {role_mention}\n"
                f"• **Статус:** Активен, ожидает участников"
            ),
            color=0x5865F2,
            timestamp=datetime.now()
        )
        report_embed.set_footer(text="K0YE1 Controll System")
        try:
            await report_channel.send(embed=report_embed)
        except Exception as e:
            print(f"Ошибка отправки отчета: {e}")

    return True, f"Очищено {len(deleted)} сообщений, меню аутентификации опубликовано, отчет отправлен."

# ========================================================
# K0YE1 TICKET TOOL (УПРОЩЕННЫЙ АНАЛОГ TICKET TOOL)
# ========================================================

class K0ye1TicketModal(discord.ui.Modal, title="📝 Анкета создания тикета"):
    subject = discord.ui.TextInput(
        label="Тема обращения",
        placeholder="Например: Вопрос, Помощь с игрой, Жалоба, Сотрудничество",
        max_length=100,
        required=True
    )
    description = discord.ui.TextInput(
        label="Подробное описание вопроса",
        placeholder="Опишите вашу ситуацию подробно...",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True
    )
    contact = discord.ui.TextInput(
        label="Дополнительно (Никнейм в игре / инфо)",
        placeholder="Ваш ник в Roblox/Minecraft или доп. ссылка (необязательно)",
        max_length=200,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ Создание тикетов доступно только на сервере!", ephemeral=True)
            return

        user = interaction.user
        safe_name = re.sub(r'[^a-zA-Z0-9а-яА-ЯёЁ_-]', '', user.name.lower())[:15]
        channel_name = f"тикет-{safe_name}"

        # Проверка, нет ли уже открытого тикета у пользователя
        existing = discord.utils.get(guild.text_channels, name=channel_name)
        if existing:
            await interaction.response.send_message(f"⚠️ У вас уже есть активный тикет: {existing.mention}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, read_messages=False),
            user: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, manage_channels=True, manage_messages=True)
        }

        # Ищем или создаем категорию для тикетов, чтобы не засорять общий список
        ticket_category = discord.utils.get(guild.categories, name="📩 ТИКЕТЫ")
        if not ticket_category:
            try:
                ticket_category = await guild.create_category(
                    name="📩 ТИКЕТЫ",
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(view_channel=False)
                    }
                )
            except Exception:
                ticket_category = interaction.channel.category if hasattr(interaction.channel, "category") else None

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=ticket_category,
                overwrites=overwrites,
                topic=f"Тикет пользователя {user.name} (ID: {user.id}) | Автор: {user.id}"
            )

            embed = discord.Embed(
                title=f"📩 Тикет: {self.subject.value}",
                description=f"Здравствуйте, {user.mention}! Ваше обращение принято.\nАдминистрация сервера скоро подключится к диалогу.",
                color=0x3498DB,
                timestamp=datetime.now()
            )
            embed.add_field(name="👤 Создатель тикета", value=f"{user.mention} (`{user.name}` | ID: `{user.id}`)", inline=False)
            embed.add_field(name="📌 Тема обращения", value=self.subject.value, inline=False)
            embed.add_field(name="📝 Описание", value=self.description.value, inline=False)
            if self.contact.value:
                embed.add_field(name="ℹ️ Дополнительно", value=self.contact.value, inline=False)
            if hasattr(user, "display_avatar") and user.display_avatar:
                embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="K0YE1 Ticket Tool • Управление тикетом ниже")

            ctrl_view = K0ye1TicketControlView(creator_id=user.id)
            await ticket_channel.send(
                content=f"{user.mention} Администрация уведомлена об открытии тикета.",
                embed=embed,
                view=ctrl_view
            )

            await interaction.followup.send(f"✅ Ваш тикет успешно создан: {ticket_channel.mention}", ephemeral=True)

            # Отправка аудита в канал логов
            log_c = guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
            if log_c:
                l_emb = discord.Embed(
                    title="📩 Создан новый тикет",
                    description=f"Пользователь {user.mention} создал канал {ticket_channel.mention}\n**Тема:** {self.subject.value}",
                    color=0x3498DB,
                    timestamp=datetime.now()
                )
                l_emb.set_footer(text="K0YE1 Ticket Tool")
                await log_c.send(embed=l_emb)

        except Exception as e:
            await interaction.followup.send(f"❌ Не удалось создать канал тикета: {e}", ephemeral=True)


class K0ye1TicketLaunchView(discord.ui.View):
    def __init__(self, button_label: str = "Открыть тикет", button_emoji: str = "📩"):
        super().__init__(timeout=None)
        if button_label != "Открыть тикет" or button_emoji != "📩":
            self.clear_items()
            btn = discord.ui.Button(
                label=button_label,
                emoji=button_emoji,
                style=discord.ButtonStyle.primary,
                custom_id="k0ye1_ticket_open_btn"
            )
            btn.callback = self.ticket_button_callback
            self.add_item(btn)

    @discord.ui.button(
        label="Открыть тикет",
        style=discord.ButtonStyle.primary,
        emoji="📩",
        custom_id="k0ye1_ticket_open_btn"
    )
    async def open_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.ticket_button_callback(interaction)

    async def ticket_button_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(K0ye1TicketModal())


class K0ye1TicketControlView(discord.ui.View):
    def __init__(self, creator_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.creator_id = creator_id

    @discord.ui.button(
        label="Отменить",
        style=discord.ButtonStyle.secondary,
        emoji="❌",
        custom_id="k0ye1_ticket_cancel_btn"
    )
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_admin = interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels
        author_id = self.creator_id
        if not author_id and interaction.channel.topic and "Автор: " in interaction.channel.topic:
            try:
                author_id = int(interaction.channel.topic.split("Автор: ")[1].split()[0])
            except Exception:
                pass

        if interaction.user.id != author_id and not is_admin:
            await interaction.response.send_message("❌ Только создатель тикета или администратор может отменить его.", ephemeral=True)
            return

        await interaction.response.send_message("⚠️ Тикет отменяется. Сохранение транскрипта и удаление канала...")

        # Сохранение транскрипта
        transcript_lines = [
            f"=== ТРАНСКРИПТ ТИКЕТА: {interaction.channel.name} ===",
            f"Сервер: {interaction.guild.name} (ID: {interaction.guild.id})",
            f"Дата закрытия: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
            f"Действие: Отмена создателем/модератором ({interaction.user.name})",
            "=" * 50,
            ""
        ]
        try:
            async for m in interaction.channel.history(limit=300, oldest_first=True):
                t_str = m.created_at.strftime("%d.%m.%Y %H:%M:%S")
                transcript_lines.append(f"[{t_str}] {m.author.name} (ID: {m.author.id}): {m.clean_content}")
        except Exception:
            pass

        log_c = interaction.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
        if log_c:
            t_bytes = "\n".join(transcript_lines).encode("utf-8")
            t_file = discord.File(io.BytesIO(t_bytes), filename=f"transcript-{interaction.channel.name}.txt")
            emb = discord.Embed(
                title="❌ Тикет отменён и закрыт",
                description=f"Канал **#{interaction.channel.name}** закрыт пользователем {interaction.user.mention}.\nТранскрипт беседы прикреплён к этому сообщению.",
                color=0x95A5A6,
                timestamp=datetime.now()
            )
            emb.set_footer(text="K0YE1 Ticket System")
            try:
                await log_c.send(embed=emb, file=t_file)
            except Exception:
                pass

        await asyncio.sleep(2)
        try:
            await interaction.channel.delete(reason=f"Тикет отменён пользователем {interaction.user.name}")
        except Exception:
            pass

    @discord.ui.button(
        label="Закрыть",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="k0ye1_ticket_close_btn"
    )
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_admin = interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels
        if not is_admin:
            await interaction.response.send_message("❌ Только администратор или модератор сервера может закрыть тикет!", ephemeral=True)
            return

        await interaction.response.send_message("🔒 Тикет закрывается администратором. Сохранение транскрипта и удаление канала...")

        # Сохранение транскрипта
        transcript_lines = [
            f"=== ТРАНСКРИПТ ТИКЕТА: {interaction.channel.name} ===",
            f"Сервер: {interaction.guild.name} (ID: {interaction.guild.id})",
            f"Дата закрытия: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
            f"Действие: Официальное закрытие администратором ({interaction.user.name})",
            "=" * 50,
            ""
        ]
        try:
            async for m in interaction.channel.history(limit=300, oldest_first=True):
                t_str = m.created_at.strftime("%d.%m.%Y %H:%M:%S")
                transcript_lines.append(f"[{t_str}] {m.author.name} (ID: {m.author.id}): {m.clean_content}")
        except Exception:
            pass

        log_c = interaction.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
        if log_c:
            t_bytes = "\n".join(transcript_lines).encode("utf-8")
            t_file = discord.File(io.BytesIO(t_bytes), filename=f"transcript-{interaction.channel.name}.txt")
            emb = discord.Embed(
                title="🔒 Тикет решён и закрыт администратором",
                description=f"Канал **#{interaction.channel.name}** успешно закрыт администратором {interaction.user.mention}.\nТранскрипт беседы сохранён в логах.",
                color=0xED4245,
                timestamp=datetime.now()
            )
            emb.set_footer(text="K0YE1 Ticket System")
            try:
                await log_c.send(embed=emb, file=t_file)
            except Exception:
                pass

        await asyncio.sleep(2)
        try:
            await interaction.channel.delete(reason=f"Тикет закрыт администратором {interaction.user.name}")
        except Exception:
            pass


class KoyelBot(commands.Bot):
    async def setup_hook(self):
        # Регистрация персистентных кнопок верификации и тикетов K0YE1
        self.add_view(K0ye1AuthView())
        self.add_view(K0ye1TicketLaunchView())
        self.add_view(K0ye1TicketControlView())
        print("🛡️ K0YE1 Controll: Persistent Views (Auth + Tickets) успешно зарегистрированы!")

        print("🔄 setup_hook: принудительная синхронизация команд...")
        try:
            synced = await self.tree.sync()
            print(f"✅ Успешно синхронизировано {len(synced)} команд: {[c.name for c in synced]}")
        except Exception as e:
            print(f"❌ Ошибка синхронизации: {e}")

def build_bot(enable_message_content: bool) -> KoyelBot:
    intents = discord.Intents.default()
    intents.members = True
    if enable_message_content:
        intents.message_content = True

    b = KoyelBot(command_prefix="!", intents=intents)

    @b.event
    async def on_ready():
        cfg = load_credentials()
        print("=" * 65)
        print(f"🤖 KoyelAI бот запущен: {b.user} (ID: {b.user.id})")
        print(f"📝 Текстовая модель: {TEXT_MODEL}")
        print(f"🎨 Модель фото: {IMAGE_MODEL} (1080P с водяным знаком)")
        print(f"📁 Папка истории чатов: {HISTORY_DIR.absolute()}")
        print("📱 Режим User App (ЛС, группы, любые серверы): ВКЛЮЧЕН!")
        if enable_message_content:
            print("🗣️ Команды без слэша (койл...) активны!")
        print("=" * 65)
        print("Бот готов к работе!")

    @b.tree.error
    async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        err_str = str(error)
        if "10062" in err_str or "Unknown interaction" in err_str or "already been acknowledged" in err_str:
            return
        if "not found" in err_str.lower():
            try:
                await b.tree.sync()
            except Exception:
                pass
            msg = "🔄 Команды обновлены! Пожалуйста, отправьте команду ещё раз."
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(msg, ephemeral=True)
                else:
                    await interaction.followup.send(msg, ephemeral=True)
            except Exception:
                pass
            return

        print(f"Ошибка слэш-команды: {error}")
        try:
            msg = f"❌ Ошибка: {error}"[:2000]
            if interaction.response.is_done():
                await interaction.followup.send(msg)
            else:
                await interaction.response.send_message(msg)
        except Exception:
            pass

    # ----------------- 1. КОМАНДА /photoai -----------------
    @b.tree.command(name="photoai", description="Сгенерировать фото 1080P с фирменным водяным знаком")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        format="Соотношение сторон: 16:9 (горизонтальное) или 9:16 (вертикальное)",
        prompt="Описание картинки для генерации",
        secret="Скрыть промпт в ответе? (True = не показывать промпт)",
        url="Опционально: ссылка на референс"
    )
    @app_commands.choices(format=[
        app_commands.Choice(name="16:9 (1920x1080 Горизонтальное 1080P)", value="16:9"),
        app_commands.Choice(name="9:16 (1080x1920 Вертикальное 1080P)", value="9:16"),
    ])
    async def photoai_slash(
        interaction: discord.Interaction,
        format: app_commands.Choice[str],
        prompt: str,
        secret: Optional[bool] = False,
        url: Optional[str] = None
    ):
        if is_bot_blocked():
            await interaction.response.send_message("🛑 **Бот заблокирован администратором.**", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        loop = asyncio.get_running_loop()

        record_usage(interaction.user.name, "images")
        chat_name = get_chat_name(interaction.channel, interaction.guild)
        log_chat_message(chat_name, interaction.user.name, f"[photoai {format.value}] {prompt}")

        full_prompt = f"{prompt} (ref: {url})" if url else prompt

        try:
            img_bytes, gen_model = await loop.run_in_executor(
                None, generate_image, full_prompt, format.value
            )
            f = discord.File(fp=io.BytesIO(img_bytes), filename="koyelai_art.png")
            caption = f"**KoyelAI**\n**Промпт:** {prompt}" if not secret else "**KoyelAI**"
            await interaction.followup.send(content=caption, file=f)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка генерации фото:\n```{e}```"[:2000])

    # ----------------- 2. КОМАНДА /textai -----------------
    @b.tree.command(name="textai", description="Задать вопрос текстовой модели GPT-6 Luna")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        prompt="Ваш вопрос или задача для GPT-6 Luna",
        secret="Скрыть промпт в ответе? (True = не показывать промпт)",
        url="Опционально: ссылка на изображение или документ"
    )
    async def textai_slash(
        interaction: discord.Interaction,
        prompt: str,
        secret: Optional[bool] = False,
        url: Optional[str] = None
    ):
        if is_bot_blocked():
            await interaction.response.send_message("🛑 **Бот заблокирован администратором.**", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        loop = asyncio.get_running_loop()

        chat_name = get_chat_name(interaction.channel, interaction.guild)
        record_usage(interaction.user.name, "luna")
        log_chat_message(chat_name, interaction.user.name, f"[textai] {prompt}")

        # Считываем реальную живую историю чата
        chat_context = await get_combined_chat_history(interaction.channel, interaction.guild, limit=50)
        file_bytes, mime_type = await download_url(url)

        if file_bytes and mime_type and mime_type.startswith("image/"):
            b64 = base64.b64encode(file_bytes).decode("utf-8")
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}}
                    ]
                }
            ]
        else:
            messages = [{"role": "user", "content": prompt}]

        try:
            answer = await loop.run_in_executor(None, call_gpt_luna, messages, chat_context, chat_name)
            await send_formatted_reply(interaction, prompt, answer, show_prompt=not secret)
            active_discord_dialogues[interaction.channel.id] = {"timestamp": time.time(), "channel_id": interaction.channel.id}
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка:\n```{e}```"[:2000])

    # ----------------- 3. КОМАНДА /voiceai -----------------
    @b.tree.command(name="voiceai", description="Ответить голосовым сообщением (озвучка ответа)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        prompt="Ваш вопрос или текст для голосового ответа",
        secret="Скрыть промпт в ответе? (True = не показывать промпт)"
    )
    async def voiceai_slash(
        interaction: discord.Interaction,
        prompt: str,
        secret: Optional[bool] = False
    ):
        if is_bot_blocked():
            await interaction.response.send_message("🛑 **Бот заблокирован администратором.**", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        loop = asyncio.get_running_loop()

        chat_name = get_chat_name(interaction.channel, interaction.guild)
        record_usage(interaction.user.name, "voice")
        log_chat_message(chat_name, interaction.user.name, f"[voiceai] {prompt}")

        chat_context = await get_combined_chat_history(interaction.channel, interaction.guild, limit=40)
        messages = [{"role": "user", "content": prompt}]

        try:
            answer = await loop.run_in_executor(None, call_gpt_luna, messages, chat_context, chat_name)
            audio_bytes, _ = await loop.run_in_executor(None, synthesize_voice, answer)

            audio_file = discord.File(fp=io.BytesIO(audio_bytes), filename="voice.ogg")
            header = "**KoyelAI Voice**\n"
            if not secret:
                header += f"**Промпт:** {prompt}\n"
            header += f"**Ответ (текст):** {answer[:300]}{'...' if len(answer) > 300 else ''}"

            await interaction.followup.send(content=header, file=audio_file)
            active_discord_dialogues[interaction.channel.id] = {"timestamp": time.time(), "channel_id": interaction.channel.id}
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка генерации голоса:\n```{e}```"[:2000])

    # ----------------- 4. КОМАНДА /block -----------------
    @b.tree.command(name="block", description="Заблокировать или разблокировать бота (только для администратора)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(password="Пароль администратора")
    async def block_slash(interaction: discord.Interaction, password: Optional[str] = None):
        if not password:
            await interaction.response.send_message("🔒 Укажите пароль для переключения блокировки:\n`/block password: <пароль>`", ephemeral=True)
            return

        if password not in VALID_BLOCK_PASSWORDS:
            await interaction.response.send_message("❌ **Неверный пароль доступа!**", ephemeral=True)
            return

        current_blocked = is_bot_blocked()
        new_status = not current_blocked
        set_bot_blocked(new_status)

        if new_status:
            await interaction.response.send_message("🛑 **Бот успешно ЗАБЛОКИРОВАН.**\nВсе функции отключены администратором.")
        else:
            await interaction.response.send_message("✅ **Бот успешно РАЗБЛОКИРОВАН.**\nВсе команды снова доступны для использования!")

    # ----------------- 5. КОМАНДЫ /uses, /token, /sync -----------------
    @b.tree.command(name="uses", description="Статистика использований моделей и лидеры")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def uses_slash(interaction: discord.Interaction):
        await interaction.response.send_message(format_uses_message())

    @b.tree.command(name="token", description="Статистика затраченных токенов")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def token_slash(interaction: discord.Interaction):
        await interaction.response.send_message(get_token_message())

    @b.tree.command(name="sponsor", description="Поддержать работу ИИ бота KoyelAI (баланс, цены генераций и помощь)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def sponsor_slash(interaction: discord.Interaction):
        embed = discord.Embed(
            title="💖 Поддержка и финансирование KoyelAI",
            description=(
                "**KoyelAI** работает на платных API нейросетей нового поколения.\n"
                "Чтобы бот оставался доступным для всех и работал без перебоев, проекту очень нужна ваша поддержка!\n\n"
                "💳 **Текущий баланс API:** `~90 ₽`"
            ),
            color=0xFF69B4,
            timestamp=datetime.now()
        )
        embed.add_field(
            name="📊 Себестоимость запросов к ИИ",
            value=(
                "• 📝 **Текст (GPT-6 Luna):** ~`0.3 ₽` / запрос\n"
                "• 🎙️ **Озвучка (Voice AI):** `5 ₽` / озвучка\n"
                "• 📱 **Фото 9:16 (Вертикальное 1080P):** `1 – 4 ₽` / генерация\n"
                "• 🖥️ **Фото 16:9 (Широкоформатное 1080P):** `3 – 10 ₽` / генерация"
            ),
            inline=False
        )
        embed.add_field(
            name="🎁 Как помочь боту?",
            value=(
                "Вы можете закинуть любую сумму (даже 10-50 рублей продлевают жизнь боту на десятки ответов и картинок!).\n"
                "Для перевода напишите создателю бота в личные сообщения или обратитесь к администрации сервера."
            ),
            inline=False
        )
        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_footer(text="Спасибо каждому за поддержку KoyelAI! ❤️")

        view = discord.ui.View()
        btn = discord.ui.Button(
            label="Поддержать KoyelAI",
            emoji="💖",
            style=discord.ButtonStyle.success,
            custom_id="koyel_sponsor_info_btn"
        )
        async def _sponsor_click(b_inter: discord.Interaction):
            await b_inter.response.send_message(
                "❤️ **Спасибо за желание поддержать KoyelAI!**\n"
                "Свяжитесь с создателем бота на сервере, чтобы пополнить баланс API (СБП/Тинькофф/Сбер/DonationAlerts).\n"
                "Все собранные средства на 100% идут на оплату токенов нейросети!",
                ephemeral=True
            )
        btn.callback = _sponsor_click
        view.add_item(btn)

        await interaction.response.send_message(embed=embed, view=view)

    @b.tree.command(name="sync", description="Принудительно пересинхронизировать команды")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def sync_slash(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            synced = await b.tree.sync()
            await interaction.followup.send(f"✅ Успешно синхронизировано {len(synced)} команд: {[c.name for c in synced]}")
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка синхронизации: {e}")

    @b.tree.command(name="commands", description="Показать список всех команд бота")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def commands_slash(interaction: discord.Interaction):
        await interaction.response.send_message(get_commands_help_message())

    @b.tree.command(name="help", description="Помощь и список команд бота")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def help_slash(interaction: discord.Interaction):
        await interaction.response.send_message(get_commands_help_message())

    @b.tree.command(name="start", description="Приветствие и список команд")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def start_slash(interaction: discord.Interaction):
        await interaction.response.send_message(get_commands_help_message())

    @b.tree.command(name="setup_auth", description="K0YE1 Controll: Очистить канал верификации и заново отправить меню аутентификации")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(administrator=True)
    async def setup_auth_slash(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Команда доступна только администраторам сервера!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, res = await deploy_k0ye1_auth(b)
        if ok:
            await interaction.followup.send(f"✅ **K0YE1 Controll:** {res}", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ **Ошибка:** {res}", ephemeral=True)

    # ----------------- КОМАНДЫ МОДЕРАЦИИ K0YE1 CONTROLL -----------------
    @b.tree.command(name="clear", description="Очистить указанное количество сообщений в канале")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(amount="Количество сообщений для удаления (1-100)")
    async def clear_slash(interaction: discord.Interaction, amount: int):
        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ Укажите количество от 1 до 100 сообщений.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(f"🧹 Успешно удалено сообщений: **{len(deleted)}**", ephemeral=True)
            log_c = interaction.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
            if log_c:
                emb = discord.Embed(
                    title="🧹 Модерация | Очистка сообщений",
                    description=f"Модератор {interaction.user.mention} очистил **{len(deleted)}** сообщений в канале {interaction.channel.mention}.",
                    color=0x3498DB,
                    timestamp=datetime.now()
                )
                emb.set_footer(text="K0YE1 Controll Moderation")
                await log_c.send(embed=emb)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка очистки: {e}", ephemeral=True)

    @b.tree.command(name="kick", description="Исключить участника с сервера")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(kick_members=True)
    @app_commands.describe(member="Участник сервера", reason="Причина исключения")
    async def kick_slash(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "Причина не указана"):
        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("❌ Вы не можете исключить участника с равной или более высокой ролью.", ephemeral=True)
            return
        try:
            await member.kick(reason=f"{reason} (Модератор: {interaction.user.name})")
            await interaction.response.send_message(f"👢 Участник {member.mention} был исключён. Причина: *{reason}*")
            log_c = interaction.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
            if log_c:
                emb = discord.Embed(
                    title="👢 Модерация | Исключение (Kick)",
                    description=f"Участник {member.mention} (`{member.name}`) исключён модератором {interaction.user.mention}.\n**Причина:** {reason}",
                    color=0xE67E22,
                    timestamp=datetime.now()
                )
                if member.display_avatar:
                    emb.set_thumbnail(url=member.display_avatar.url)
                emb.set_footer(text=f"ID: {member.id} • K0YE1 Controll")
                await log_c.send(embed=emb)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка исключения: {e}", ephemeral=True)

    @b.tree.command(name="ban", description="Заблокировать участника на сервере")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(member="Участник сервера", reason="Причина бана")
    async def ban_slash(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "Причина не указана"):
        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("❌ Вы не можете забанить участника с равной или более высокой ролью.", ephemeral=True)
            return
        try:
            await member.ban(reason=f"{reason} (Модератор: {interaction.user.name})")
            await interaction.response.send_message(f"🔨 Участник {member.mention} был забанен. Причина: *{reason}*")
            log_c = interaction.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
            if log_c:
                emb = discord.Embed(
                    title="🔨 Модерация | Блокировка (Ban)",
                    description=f"Участник {member.mention} (`{member.name}`) забанен модератором {interaction.user.mention}.\n**Причина:** {reason}",
                    color=0xED4245,
                    timestamp=datetime.now()
                )
                if member.display_avatar:
                    emb.set_thumbnail(url=member.display_avatar.url)
                emb.set_footer(text=f"ID: {member.id} • K0YE1 Controll")
                await log_c.send(embed=emb)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка бана: {e}", ephemeral=True)

    @b.tree.command(name="timeout", description="Выдать тайм-аут (мут) участнику на указанное количество минут")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(member="Участник сервера", minutes="Длительность в минутах (1-40320)", reason="Причина тайм-аута")
    async def timeout_slash(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: Optional[str] = "Нарушение правил"):
        if minutes < 1:
            await interaction.response.send_message("❌ Минимальная длительность: 1 минута.", ephemeral=True)
            return
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        try:
            await member.timeout(until, reason=f"{reason} (Модератор: {interaction.user.name})")
            await interaction.response.send_message(f"🔇 Участнику {member.mention} выдан тайм-аут на **{minutes} мин.** Причина: *{reason}*")
            log_c = interaction.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
            if log_c:
                emb = discord.Embed(
                    title="🔇 Модерация | Тайм-аут (Timeout)",
                    description=f"Участнику {member.mention} (`{member.name}`) выдан тайм-аут модератором {interaction.user.mention}.\n**Длительность:** {minutes} мин.\n**Причина:** {reason}",
                    color=0xF1C40F,
                    timestamp=datetime.now()
                )
                emb.set_footer(text=f"ID: {member.id} • K0YE1 Controll")
                await log_c.send(embed=emb)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка выдачи тайм-аута: {e}", ephemeral=True)

    @b.tree.command(name="warn", description="Выдать официальное предупреждение участнику")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(member="Участник", reason="Причина предупреждения")
    async def warn_slash(interaction: discord.Interaction, member: discord.Member, reason: str):
        warn_msg = f"⚠️ {member.mention}, вам вынесено официальное предупреждение от {interaction.user.mention}!\n**Причина:** {reason}"
        await interaction.response.send_message(warn_msg)
        log_c = interaction.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
        if log_c:
            emb = discord.Embed(
                title="⚠️ Модерация | Предупреждение (Warn)",
                description=f"Пользователь {member.mention} (`{member.name}`) получил варн от {interaction.user.mention}.\n**Причина:** {reason}",
                color=0xE67E22,
                timestamp=datetime.now()
            )
            emb.set_footer(text=f"ID: {member.id} • K0YE1 Controll")
            await log_c.send(embed=emb)

    @b.tree.command(name="say", description="Отправить сообщение в любой канал от имени бота KoyelAI")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(
        channel="Канал для отправки сообщения",
        message="Текст сообщения",
        embed_title="Опционально: отправить как красивую Embed-карточку с заголовком"
    )
    async def say_slash(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
        embed_title: Optional[str] = None
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            if embed_title:
                emb = discord.Embed(
                    title=embed_title,
                    description=message,
                    color=0x5865F2,
                    timestamp=datetime.now()
                )
                if interaction.guild and interaction.guild.icon:
                    emb.set_thumbnail(url=interaction.guild.icon.url)
                emb.set_footer(text=f"Опубликовано через KoyelAI • Автор: {interaction.user.name}")
                await channel.send(embed=emb)
            else:
                await channel.send(message)
            await interaction.followup.send(f"✅ Сообщение успешно отправлено в {channel.mention}!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Не удалось отправить сообщение: {e}", ephemeral=True)

    @b.tree.command(name="ticket_panel", description="Опубликовать панель создания тикетов в канале")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        channel="Канал, куда отправить панель тикетов",
        title="Заголовок карточки тикетов",
        description="Описание / инструкция",
        button_label="Текст на кнопке"
    )
    async def ticket_panel_slash(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        title: Optional[str] = "📩 Поддержка и обратная связь",
        description: Optional[str] = "Возник вопрос, предложение или нужна помощь администрации?\nНажмите кнопку ниже, чтобы заполнить анкету и создать приватный тикет.",
        button_label: Optional[str] = "📩 Открыть тикет"
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            emb = discord.Embed(
                title=title,
                description=description,
                color=0x3498DB
            )
            emb.add_field(
                name="📋 Как это работает?",
                value=f"1. Нажмите кнопку **«{button_label}»** ниже.\n2. Заполните короткую анкету.\n3. Бот создаст отдельный закрытый канал для общения с администрацией.",
                inline=False
            )
            if interaction.guild and interaction.guild.icon:
                emb.set_thumbnail(url=interaction.guild.icon.url)
            emb.set_footer(text="K0YE1 Ticket Tool • Поддержка 24/7")

            view = K0ye1TicketLaunchView(button_label=button_label or "Открыть тикет")
            await channel.send(embed=emb, view=view)
            await interaction.followup.send(f"✅ Панель тикетов успешно создана и опубликована в {channel.mention}!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка публикации панели тикетов: {e}", ephemeral=True)

    @b.tree.command(name="news", description="Koyel News: Сгенерировать и опубликовать новость через ИИ")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.choices(category=[
        app_commands.Choice(name="Roblox", value="Roblox"),
        app_commands.Choice(name="Minecraft", value="Minecraft"),
        app_commands.Choice(name="Steam / ПК Игры", value="Steam / ПК Игры"),
        app_commands.Choice(name="Сервер × K0YE1 ×", value="Сервер × K0YE1 ×"),
    ])
    @app_commands.describe(
        category="Категория новости",
        place_or_game="Плейс, игра или тематика (например: a Desert, Blox Fruits, Rust, Ивент)",
        comment="Детали новости (что описать, фиксы, обновления, дата)"
    )
    async def news_slash(
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
        place_or_game: str,
        comment: str
    ):
        await interaction.response.defer(ephemeral=True)
        loop = asyncio.get_running_loop()
        news_prompt = (
            f"Напиши качественную, захватывающую новость для игрового сообщества.\n"
            f"Категория: {category.value}\n"
            f"Плейс/Игра/Тема: {place_or_game}\n"
            f"Детали новости: {comment}\n\n"
            f"Сделай новость привлекательной, добавь эмодзи, выдели пункты жирным шрифтом и добавь хэштеги в конце."
        )
        try:
            news_text = await loop.run_in_executor(None, call_gpt_luna, [{"role": "user", "content": news_prompt}])
            news_chan = interaction.guild.get_channel(K0YE1_NEWS_CHANNEL_ID)
            if not news_chan:
                try:
                    news_chan = await b.fetch_channel(K0YE1_NEWS_CHANNEL_ID)
                except Exception:
                    news_chan = interaction.channel

            emb = discord.Embed(
                title=f"📰 Koyel News | {category.value} — {place_or_game}",
                description=news_text,
                color=0xE67E22,
                timestamp=datetime.now()
            )
            emb.set_footer(text=f"Koyel News • Опубликовал {interaction.user.name}")
            if interaction.guild.icon:
                emb.set_thumbnail(url=interaction.guild.icon.url)

            await news_chan.send(embed=emb)
            await interaction.followup.send(f"✅ Новость успешно опубликована в канале {news_chan.mention}!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка публикации новости: {e}", ephemeral=True)

    @b.tree.command(name="webhook_send", description="Отправить сообщение в любой канал через Webhook от имени бота")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(manage_webhooks=True)
    @app_commands.describe(
        webhook_url="URL вебхука Discord (https://discord.com/api/webhooks/...)",
        message="Текст сообщения для отправки",
        username="Кастомное имя отправителя (по умолчанию: KoyelAI)",
        embed_title="Опционально: заголовок Embed-карточки"
    )
    async def webhook_send_slash(
        interaction: discord.Interaction,
        webhook_url: str,
        message: str,
        username: Optional[str] = "KoyelAI",
        embed_title: Optional[str] = None
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            payload = {"username": username}
            if embed_title:
                payload["embeds"] = [{
                    "title": embed_title,
                    "description": message,
                    "color": 0x5865F2,
                    "footer": {"text": "K0YE1 Controll Webhook"}
                }]
            else:
                payload["content"] = message

            async with httpx.AsyncClient(timeout=10.0) as hclient:
                r = await hclient.post(webhook_url, json=payload)
                if r.status_code in (200, 204):
                    await interaction.followup.send("✅ Сообщение успешно отправлено через Webhook!", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Ошибка отправки вебхука ({r.status_code}): {r.text}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка вызова вебхука: {e}", ephemeral=True)

    # ----------------- ФУЛЛ ЛОГИ АУДИТА СЕРВЕРА (КАК AKEMI) -----------------
    @b.event
    async def on_member_join(member: discord.Member):
        if member.guild.id != K0YE1_GUILD_ID:
            return
        log_chan = member.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
        if not log_chan:
            return
        created_str = member.created_at.strftime("%d.%m.%Y %H:%M")
        age_days = (datetime.now(timezone.utc) - member.created_at).days
        embed = discord.Embed(
            title="📥 Новый участник присоединился",
            description=f"Добро пожаловать, {member.mention} (`{member.name}`)!",
            color=0x2ECC71,
            timestamp=datetime.now()
        )
        embed.add_field(name="🆔 ID", value=str(member.id), inline=True)
        embed.add_field(name="📅 Аккаунт создан", value=f"{created_str} ({age_days} дн. назад)", inline=True)
        embed.add_field(name="👥 Всего участников", value=str(member.guild.member_count), inline=True)
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="K0YE1 Controll • Аудит участников")
        try:
            await log_chan.send(embed=embed)
        except Exception:
            pass

    @b.event
    async def on_member_remove(member: discord.Member):
        if member.guild.id != K0YE1_GUILD_ID:
            return
        log_chan = member.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
        if not log_chan:
            return
        roles_list = [r.name for r in member.roles if r.name != "@everyone"]
        roles_str = ", ".join(roles_list) if roles_list else "Нет ролей"
        embed = discord.Embed(
            title="📤 Участник покинул сервер",
            description=f"Пользователь {member.mention} (`{member.name}`) покинул сервер (или был исключён).",
            color=0xED4245,
            timestamp=datetime.now()
        )
        embed.add_field(name="🆔 ID", value=str(member.id), inline=True)
        embed.add_field(name="🎖️ Роли", value=roles_str[:1024], inline=True)
        embed.add_field(name="👥 Осталось участников", value=str(member.guild.member_count), inline=True)
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="K0YE1 Controll • Аудит участников")
        try:
            await log_chan.send(embed=embed)
        except Exception:
            pass

    @b.event
    async def on_message_delete(message: discord.Message):
        if not message.guild or message.guild.id != K0YE1_GUILD_ID:
            return
        if message.author.bot or message.channel.id == K0YE1_REPORT_CHANNEL_ID:
            return
        log_chan = message.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
        if not log_chan:
            return
        embed = discord.Embed(
            title="🗑️ Сообщение удалено",
            description=f"Сообщение от {message.author.mention} в канале {message.channel.mention} было удалено.",
            color=0xE67E22,
            timestamp=datetime.now()
        )
        embed.add_field(name="📝 Содержимое", value=message.content[:1024] if message.content else "*[Без текста / Вложение]*", inline=False)
        if message.attachments:
            att_names = ", ".join([a.filename for a in message.attachments])
            embed.add_field(name="📎 Вложения", value=att_names[:1024], inline=False)
        if message.author.display_avatar:
            embed.set_thumbnail(url=message.author.display_avatar.url)
        embed.set_footer(text=f"ID сообщения: {message.id} • K0YE1 Controll")
        try:
            await log_chan.send(embed=embed)
        except Exception:
            pass

    @b.event
    async def on_message_edit(before: discord.Message, after: discord.Message):
        if not before.guild or before.guild.id != K0YE1_GUILD_ID:
            return
        if before.author.bot or before.content == after.content or before.channel.id == K0YE1_REPORT_CHANNEL_ID:
            return
        log_chan = before.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
        if not log_chan:
            return
        embed = discord.Embed(
            title="✏️ Сообщение отредактировано",
            description=f"Автор: {before.author.mention} в {before.channel.mention} • [Перейти к сообщению]({after.jump_url})",
            color=0x3498DB,
            timestamp=datetime.now()
        )
        embed.add_field(name="До:", value=before.content[:1024] if before.content else "*[Пусто]*", inline=False)
        embed.add_field(name="После:", value=after.content[:1024] if after.content else "*[Пусто]*", inline=False)
        if before.author.display_avatar:
            embed.set_thumbnail(url=before.author.display_avatar.url)
        embed.set_footer(text=f"ID: {before.id} • K0YE1 Controll")
        try:
            await log_chan.send(embed=embed)
        except Exception:
            pass

    @b.event
    async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.guild.id != K0YE1_GUILD_ID or member.bot:
            return
        log_chan = member.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
        if not log_chan:
            return
        if before.channel != after.channel:
            if before.channel is None and after.channel is not None:
                embed = discord.Embed(
                    title="🔊 Подключение к голосовому каналу",
                    description=f"{member.mention} зашёл в канал **{after.channel.name}**",
                    color=0x2ECC71,
                    timestamp=datetime.now()
                )
            elif before.channel is not None and after.channel is None:
                embed = discord.Embed(
                    title="🔇 Отключение от голосового канала",
                    description=f"{member.mention} вышел из канала **{before.channel.name}**",
                    color=0xED4245,
                    timestamp=datetime.now()
                )
            else:
                embed = discord.Embed(
                    title="🔄 Переход между голосовыми каналами",
                    description=f"{member.mention} перешёл из **{before.channel.name}** в **{after.channel.name}**",
                    color=0x9B59B6,
                    timestamp=datetime.now()
                )
            if member.display_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="K0YE1 Controll • Голосовой аудит")
            try:
                await log_chan.send(embed=embed)
            except Exception:
                pass

    # ----------------- СЛУШАТЕЛЬ ЧАТА (БЕЗ СЛЭША) -----------------
    if enable_message_content:
        @b.event
        async def on_message(message: discord.Message):
            if message.author.bot or message.author.id == b.user.id:
                return

            # Защита от дублирования сообщений
            if message.id in processed_message_ids:
                return
            processed_message_ids.add(message.id)
            if len(processed_message_ids) > 1000:
                processed_message_ids.clear()

            chat_name = get_chat_name(message.channel, message.guild)
            author_name = message.author.name
            raw_content = message.content

            if raw_content:
                log_chat_message(chat_name, author_name, raw_content, message.created_at)

            # --- Модерация триггеров K0YE1 Controll (Лимит матов) ---
            if message.guild and raw_content:
                prof_count, censored_text, found_words = check_and_censor_profanity(raw_content)
                if prof_count > MAX_PROFANITY_LIMIT:
                    try:
                        await message.delete()
                    except Exception as e:
                        print(f"Не удалось удалить сообщение с превышением матов: {e}")

                    warn_msg = (
                        f"{message.author.mention} Ваше сообщение превысило лимит матов ({prof_count}/{MAX_PROFANITY_LIMIT})\n"
                        f"Вот ваше сообщение без матов:\n"
                        f"{censored_text}"
                    )
                    try:
                        await message.channel.send(warn_msg)
                    except Exception as e:
                        print(f"Ошибка отправки предупреждения о матах: {e}")

                    # Отправка аудита в канал логов
                    log_chan = message.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
                    if not log_chan:
                        try:
                            log_chan = await b.fetch_channel(K0YE1_REPORT_CHANNEL_ID)
                        except Exception:
                            pass

                    if log_chan:
                        log_embed = discord.Embed(
                            title="🚨 Нарушение триггера | Превышение лимита матов",
                            description=(
                                f"• **Пользователь:** {message.author.mention} (`{message.author.name}`)\n"
                                f"• **Канал:** {message.channel.mention} (`#{message.channel.name}`)\n"
                                f"• **Количество матов:** `{prof_count} / {MAX_PROFANITY_LIMIT}`\n"
                                f"• **Действие:** Исходное сообщение удалено, в чат отправлена очищенная копия."
                            ),
                            color=0xED4245,
                            timestamp=datetime.now()
                        )
                        log_embed.add_field(
                            name="📝 Исходное сообщение",
                            value=raw_content[:1024] if raw_content else "—",
                            inline=False
                        )
                        log_embed.add_field(
                            name="✨ Очищенное сообщение",
                            value=censored_text[:1024] if censored_text else "—",
                            inline=False
                        )
                        if hasattr(message.author, "display_avatar") and message.author.display_avatar:
                            log_embed.set_thumbnail(url=message.author.display_avatar.url)
                        log_embed.set_footer(text="K0YE1 Controll • Модерация")
                        try:
                            await log_chan.send(embed=log_embed)
                        except Exception as e:
                            print(f"Ошибка отправки лога модерации: {e}")
                    return

            # --- Анти-Флуд / Анти-Спам (K0YE1 Controll) ---
            if message.guild:
                now_t = time.time()
                uid = message.author.id
                u_times = user_spam_tracker.get(uid, [])
                u_times = [t for t in u_times if (now_t - t) < 3.0]
                u_times.append(now_t)
                user_spam_tracker[uid] = u_times

                if len(u_times) > 5 and not (message.author.guild_permissions.manage_messages or message.author.guild_permissions.administrator):
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    if len(u_times) == 6:
                        try:
                            await message.channel.send(f"⚠️ {message.author.mention}, не отправляйте сообщения так быстро! Соблюдайте паузу между сообщениями.")
                        except Exception:
                            pass
                        log_chan = message.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
                        if log_chan:
                            emb = discord.Embed(
                                title="🚨 Анти-Флуд | Превышение скорости сообщений",
                                description=f"Пользователь {message.author.mention} (`{message.author.name}`) отправляет сообщения быстрее 5 сообщений / 3 сек в {message.channel.mention}.\nЛишние сообщения автоматически удалены.",
                                color=0xE67E22,
                                timestamp=datetime.now()
                            )
                            emb.set_footer(text="K0YE1 Controll • Анти-Флуд")
                            try:
                                await log_chan.send(embed=emb)
                            except Exception:
                                pass
                    return

            # --- Анти-Реклама инвайтов (K0YE1 Controll) ---
            if message.guild and raw_content:
                has_invite = bool(re.search(r'(?:https?://)?(?:www\.)?(?:discord\.(?:gg|io|me|li)|discord(?:app)?\.com/invite)/[a-zA-Z0-9_-]+', raw_content, re.IGNORECASE))
                is_mod = message.author.guild_permissions.manage_guild or message.author.guild_permissions.administrator
                if has_invite and not is_mod:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    try:
                        await message.channel.send(f"🚫 {message.author.mention}, публикация ссылок-приглашений на сторонние Discord-серверы запрещена правилами!")
                    except Exception:
                        pass

                    log_chan = message.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
                    if log_chan:
                        emb = discord.Embed(
                            title="🛡️ Защита от рекламы | Удалена ссылка-приглашение",
                            description=f"Пользователь {message.author.mention} (`{message.author.name}`) попытался отправить Discord-инвайт в {message.channel.mention}.\nСообщение автоматически удалено.",
                            color=0xE74C3C,
                            timestamp=datetime.now()
                        )
                        emb.add_field(name="📝 Содержимое", value=raw_content[:1024], inline=False)
                        emb.set_footer(text="K0YE1 Controll • Анти-Реклама")
                        try:
                            await log_chan.send(embed=emb)
                        except Exception:
                            pass
                    return

            clean_low = raw_content.strip().lower()

            if clean_low in [
                "/commands", "!commands", "койл команды", "koyel commands", "команды", "commands",
                "/help", "!help", "койл help", "помощь",
                "/start", "!start", "койл старт", "старт"
            ]:
                await message.reply(get_commands_help_message())
                return

            if clean_low in ["койл uses", "koyel uses", "!uses", "койл статистика"]:
                await message.reply(format_uses_message())
                return

            if clean_low in ["койл token", "koyel token", "койл токен", "!token", "!tokens"]:
                await message.reply(get_token_message())
                return

            if clean_low in ["койл setup auth", "койл настройка аутентификации", "!setup_auth", "койл верификация настроить"]:
                if message.guild and message.author.guild_permissions.administrator:
                    ok, res = await deploy_k0ye1_auth(b)
                    await message.reply(f"✅ **K0YE1 Controll:** {res}" if ok else f"❌ {res}")
                else:
                    await message.reply("❌ Доступно только администраторам на сервере!")
                return

            if clean_low.startswith("койл block") or clean_low.startswith("!block"):
                parts = clean_low.split()
                if len(parts) >= 2 and any(parts[-1] == p.lower() for p in VALID_BLOCK_PASSWORDS):
                    new_status = not is_bot_blocked()
                    set_bot_blocked(new_status)
                    st_msg = "🛑 Бот ЗАБЛОКИРОВАН." if new_status else "✅ Бот РАЗБЛОКИРОВАН."
                    await message.reply(st_msg)
                    return
                else:
                    await message.reply("❌ Неверный пароль для block!")
                    return

            if is_bot_blocked():
                return

            channel_id = message.channel.id
            author_id = message.author.id
            is_dm = (message.guild is None) or isinstance(message.channel, (discord.DMChannel, discord.GroupChannel))

            now_ts = time.time()
            in_active_dialogue = False
            if channel_id in active_discord_dialogues:
                conv = active_discord_dialogues[channel_id]
                if (now_ts - conv.get("timestamp", 0)) < DIALOGUE_TIMEOUT:
                    # В канале сервера любой участник может участвовать в активном диалоге (мульти-сообщения от нескольких людей)
                    in_active_dialogue = True
                else:
                    active_discord_dialogues.pop(channel_id, None)

            if in_active_dialogue and clean_low in ["хватит", "молчи", "стоп", "тихо", "отбой", "перестань"]:
                active_discord_dialogues.pop(channel_id, None)
                await message.reply("Хорошо, умолкаю. Если понадоблюсь — позови по имени «койл» или напиши команду!")
                return

            is_reply_to_bot = False
            context_messages = []

            if message.reference and message.reference.message_id:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                    if ref_msg and ref_msg.author.id == b.user.id:
                        is_reply_to_bot = True
                        prev_text = ref_msg.content
                        if "**Ответ:**" in prev_text:
                            prev_text = prev_text.split("**Ответ:**", 1)[-1].strip()
                        context_messages.append({"role": "assistant", "content": prev_text})
                except Exception as e:
                    pass

            is_mention = b.user in message.mentions
            trigger_prompt = extract_trigger_prompt(raw_content)

            should_respond = is_dm or is_reply_to_bot or is_mention or (trigger_prompt is not None) or in_active_dialogue

            if should_respond:
                if trigger_prompt is not None:
                    user_prompt = trigger_prompt
                elif is_mention:
                    user_prompt = message.clean_content.replace(f"@{b.user.name}", "").strip()
                else:
                    user_prompt = message.clean_content.strip()

                if not user_prompt:
                    user_prompt = "привет"

                user_prompt_low = user_prompt.strip().lower()
                if user_prompt_low in ["commands", "команды", "help", "помощь", "start", "старт"]:
                    await message.reply(get_commands_help_message())
                    return
                if user_prompt_low in ["uses", "статистика"]:
                    await message.reply(format_uses_message())
                    return
                if user_prompt_low in ["token", "tokens", "токен", "токены"]:
                    await message.reply(get_token_message())
                    return

                show_prompt = True
                for sec_prefix in ["секрет ", "secret ", "secretprompt "]:
                    if user_prompt_low.startswith(sec_prefix):
                        user_prompt = user_prompt[len(sec_prefix):].strip()
                        show_prompt = False
                        break

                is_photo = any(k in user_prompt_low for k in [
                    "сделай мне фото", "сделай фото", "нарисуй", "сгенерируй фото",
                    "сгенерируй картинку", "сделай картинку", "создай фото", "draw", "generate image"
                ])

                async with message.channel.typing():
                    loop = asyncio.get_running_loop()

                    if is_photo:
                        record_usage(author_name, "images")
                        fmt = "9:16" if any(k in user_prompt_low for k in ["9:16", "вертикальн", "shorts", "reels"]) else "16:9"
                        try:
                            img_bytes, _ = await loop.run_in_executor(None, generate_image, user_prompt, fmt)
                            f = discord.File(fp=io.BytesIO(img_bytes), filename="koyelai_art.png")
                            caption = f"**KoyelAI**\n**Промпт:** {user_prompt}" if show_prompt else "**KoyelAI**"
                            await message.reply(content=caption, file=f)
                            active_discord_dialogues[channel_id] = {"timestamp": time.time(), "channel_id": channel_id}
                            return
                        except Exception as e:
                            await message.reply(f"❌ Ошибка генерации фото:\n```{e}```"[:2000])
                            return

                    record_usage(author_name, "luna")
                    chat_context = await get_combined_chat_history(message.channel, message.guild, limit=40)
                    context_messages.append({"role": "user", "content": user_prompt})

                    try:
                        answer = await loop.run_in_executor(None, call_gpt_luna, context_messages, chat_context, chat_name)
                        await send_formatted_reply(message, user_prompt, answer, show_prompt=show_prompt)
                        active_discord_dialogues[channel_id] = {"timestamp": time.time(), "channel_id": channel_id}
                    except Exception as e:
                        await message.reply(f"❌ Ошибка:\n```{e}```"[:2000])
                return

            await b.process_commands(message)

    return b

# ========================================================
# ЗАПУСК DISCORD БОТА KOYELAI
# ========================================================

async def run_discord(token: str):
    print("🚀 Подключение к Discord...")
    try:
        full_bot = build_bot(enable_message_content=True)
        await full_bot.start(token)
    except discord.errors.PrivilegedIntRequired:
        print("\n" + "=" * 65)
        print("ℹ️ Галочка 'Message Content Intent' не включена в Discord.")
        print("🔄 Запуск Discord бота в режиме только слэш-команд...")
        print("=" * 65 + "\n")
        fallback_bot = build_bot(enable_message_content=False)
        await fallback_bot.start(token)
    except Exception as e:
        print(f"❌ Ошибка Discord бота: {e}")

async def async_main():
    creds = load_credentials()
    discord_token = creds.get("discord_token")

    if not creds.get("openai_key"):
        print("❌ Не указан OPENAI_API_KEY в файле Api_key.txt")
        sys.exit(1)

    if not discord_token:
        print("❌ Не указан DISCORD_BOT_TOKEN в файле Api_key.txt!")
        sys.exit(1)

    print("\n" + "=" * 65)
    print("🤖 KoyelAI Discord Bot")
    print(f"📝 Текстовая модель: {TEXT_MODEL}")
    print(f"🎨 Модель фото: {IMAGE_MODEL} (1080P с водяным знаком)")
    print(f"🔒 Команда блокировки: /block [пароль]")
    print(f"💖 Команда поддержки: /sponsor (Баланс ~90 ₽)")
    print(f"📁 Папка истории чатов: {HISTORY_DIR.absolute()}")
    print("=" * 65 + "\n")

    await run_discord(discord_token)


def main():
    if not acquire_instance_lock():
        print("⚠️ Бот KoyelAI уже запущен в другом процессе! Этот экземпляр безопасно завершает работу, предотвращая дублирование сообщений.")
        sys.exit(0)
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Бот остановлен.")

if __name__ == "__main__":
    main()
