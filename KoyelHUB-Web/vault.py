"""
========================================================
KOYEL HUB — VAULT & ENCRYPTION MODULE
========================================================
Шифрование и защита API ключей, токенов Discord и паролей.
Защищает конфиденциальные данные от утечки на GitHub с помощью AES/Fernet.
"""

import os
import sys
import base64
import hashlib
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    from cryptography.fernet import Fernet, InvalidToken
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

BASE_DIR = Path(__file__).resolve().parent
SALT = b"koyel_hub_master_secure_vault_2026"
ENC_FILE = BASE_DIR / "Api_key.enc"
TXT_FILE = BASE_DIR / "Api_key.txt"

CREATOR_PASSWORD = "Kimzar60"
ADMIN_PASSWORD = "Artem255"

def get_fernet_key(password: str) -> bytes:
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), SALT, 100000)
    return base64.urlsafe_b64encode(derived)

def encrypt_text(text: str, password: str = CREATOR_PASSWORD) -> bytes:
    if not HAS_CRYPTO:
        raise RuntimeError("Установите библиотеку cryptography: pip install cryptography")
    f = Fernet(get_fernet_key(password))
    return f.encrypt(text.encode("utf-8"))

def decrypt_bytes(ciphertext: bytes, password: str = CREATOR_PASSWORD) -> Optional[str]:
    if not HAS_CRYPTO:
        return None
    # Пробуем указанный пароль, затем создателя, затем админа
    candidates = [password, CREATOR_PASSWORD, ADMIN_PASSWORD]
    for cand in candidates:
        if not cand:
            continue
        try:
            f = Fernet(get_fernet_key(cand))
            return f.decrypt(ciphertext).decode("utf-8")
        except (InvalidToken, Exception):
            pass
    return None

def verify_credentials(password: str) -> Tuple[bool, str]:
    """Проверяет пароль и возвращает (True/False, 'creator'/'admin'/None)."""
    if password == CREATOR_PASSWORD:
        return True, "creator"
    elif password == ADMIN_PASSWORD:
        return True, "admin"
    return False, ""

def save_vault(config_dict: Dict[str, str], password: str = CREATOR_PASSWORD) -> bool:
    """Сохраняет конфигурацию в зашифрованный файл Api_key.enc."""
    lines = [
        "# ========================================================",
        "# KOYEL HUB ENCRYPTED VAULT (PROTECTED)",
        "# ========================================================",
        f"OPENAI_API_KEY={config_dict.get('OPENAI_API_KEY', '')}",
        f"OPENAI_BASE_URL={config_dict.get('OPENAI_BASE_URL', 'https://api.aitunnel.ru/v1')}",
        f"OPENAI_MODEL={config_dict.get('OPENAI_MODEL', 'gpt-6-luna')}",
        f"IMAGE_MODEL={config_dict.get('IMAGE_MODEL', 'gpt-image-2.6-sunburst')}",
        f"DISCORD_BOT_TOKEN={config_dict.get('DISCORD_BOT_TOKEN', '')}",
        f"CREATOR_PASSWORD={CREATOR_PASSWORD}",
        f"ADMIN_PASSWORD={ADMIN_PASSWORD}"
    ]
    raw = "\n".join(lines)
    cipher = encrypt_text(raw, password)
    ENC_FILE.write_bytes(cipher)
    return True

def load_vault(password: str = CREATOR_PASSWORD) -> Dict[str, str]:
    """Загружает конфигурацию либо из Api_key.txt, либо из зашифрованного Api_key.enc."""
    data = {
        "OPENAI_API_KEY": "",
        "OPENAI_BASE_URL": "https://api.aitunnel.ru/v1",
        "OPENAI_MODEL": "gpt-6-luna",
        "IMAGE_MODEL": "gpt-image-2.6-sunburst",
        "DISCORD_BOT_TOKEN": "",
        "CREATOR_PASSWORD": CREATOR_PASSWORD,
        "ADMIN_PASSWORD": ADMIN_PASSWORD
    }

    # 1. Если есть открытый Api_key.txt
    if TXT_FILE.exists():
        try:
            content = TXT_FILE.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip().strip('"').strip("'")
            # Автоматически создаем/обновляем зашифрованный файл
            if HAS_CRYPTO and data.get("DISCORD_BOT_TOKEN"):
                save_vault(data, CREATOR_PASSWORD)
            return data
        except Exception:
            pass

    # 2. Если есть зашифрованный Api_key.enc
    if ENC_FILE.exists() and HAS_CRYPTO:
        try:
            cipher = ENC_FILE.read_bytes()
            plain = decrypt_bytes(cipher, password)
            if plain:
                for line in plain.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        data[k.strip()] = v.strip().strip('"').strip("'")
                return data
        except Exception:
            pass

    return data

def cli_main():
    args = sys.argv[1:]
    if not args or args[0] in ["-h", "--help", "help"]:
        print("Использование vault.py:")
        print("  python vault.py encrypt [password]  -> Зашифровать Api_key.txt в Api_key.enc")
        print("  python vault.py decrypt [password]  -> Расшифровать Api_key.enc в Api_key.txt")
        print("  python vault.py status              -> Проверить статус шифрования")
        return

    cmd = args[0].lower()
    passw = args[1] if len(args) > 1 else CREATOR_PASSWORD

    if cmd == "encrypt":
        if not TXT_FILE.exists():
            print("❌ Файл Api_key.txt не найден для шифрования!")
            return
        data = load_vault(passw)
        save_vault(data, passw)
        print("🔒 [Успех] Файл Api_key.txt успешно зашифрован в Api_key.enc!")
        print(f"🔑 Пароль расшифровки: {passw}")
        print("💡 Теперь Api_key.enc можно безопасно хранить или отправлять, никто без пароля не увидит ключи.")

    elif cmd == "decrypt":
        if not ENC_FILE.exists():
            print("❌ Файл Api_key.enc не найден!")
            return
        cipher = ENC_FILE.read_bytes()
        plain = decrypt_bytes(cipher, passw)
        if not plain:
            print("❌ Неверный пароль расшифровки!")
            return
        TXT_FILE.write_text(plain, encoding="utf-8")
        print("🔓 [Успех] Файл Api_key.enc успешно расшифрован в Api_key.txt!")

    elif cmd == "status":
        print("Статус хранилища ключей Koyel HUB:")
        print(f"  • Api_key.txt (открытый): {'✅ Присутствует' if TXT_FILE.exists() else '❌ Отсутствует'}")
        print(f"  • Api_key.enc (зашифрованный): {'🔒 Зашифрован и защищен' if ENC_FILE.exists() else '❌ Не создан'}")
        print(f"  • Создатель (Owner): Kimzar (пароль установлен)")
        print(f"  • Администратор (Admin): Artem (пароль установлен)")

if __name__ == "__main__":
    cli_main()
