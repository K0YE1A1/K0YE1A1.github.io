import os
import sys
import re
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Tuple, List
import discord

BASE_DIR = Path(__file__).parent.resolve()
API_KEY_FILE = BASE_DIR / "Api_key.txt"

# Идентификаторы сервера и каналов
K0YE1_GUILD_ID = 1558046068741115967
K0YE1_AUTH_CHANNEL_ID = 1571136147533471844
K0YE1_REPORT_CHANNEL_ID = 1571137330037919844
K0YE1_AUTH_ROLE_ID = 1558288080467660830
K0YE1_NEWS_CHANNEL_ID = 1571129669481275514
MAX_PROFANITY_LIMIT = 10

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

def load_discord_token() -> str:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if API_KEY_FILE.exists():
        with open(API_KEY_FILE, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if line.startswith("#") or not line:
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip().upper()
                    v = v.strip().strip('"').strip("'")
                    if k in ("DISCORD_BOT_TOKEN", "DISCORD_TOKEN"):
                        token = v
                elif line.startswith("MTU") and len(line) > 50:
                    token = line
    return token

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
            await interaction.response.send_message("❌ Команда доступна только на сервере!", ephemeral=True)
            return

        role = guild.get_role(K0YE1_AUTH_ROLE_ID)
        if not role:
            await interaction.response.send_message("❌ Ошибка: роль верификации не найдена.", ephemeral=True)
            return

        member = interaction.user
        if role in member.roles:
            await interaction.response.send_message("ℹ️ **Вы уже прошли аутентификацию!**\nРоль **Верифицирован** уже у вас есть.", ephemeral=True)
            return

        try:
            await member.add_roles(role, reason="K0YE1 Controll: Успешная аутентификация пользователя")
            await interaction.response.send_message(
                f"✅ **Аутентификация успешно пройдена!**\nВам выдана роль **{role.name}**. Добро пожаловать на сервер **× K0YE1 ×**!",
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
            await interaction.response.send_message("❌ У бота недостаточно прав для выдачи роли.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка: {e}", ephemeral=True)

class K0ye1ControlBot(discord.Client):
    def __init__(self, one_shot: bool = False):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents)
        self.one_shot = one_shot

    async def setup_hook(self):
        self.add_view(K0ye1AuthView())
        print("🛡️ Persistent View K0ye1AuthView зарегистрирован.")

    async def on_ready(self):
        print("=" * 65)
        print(f"🤖 K0YE1 Controll бот запущен: {self.user} (ID: {self.user.id})")
        print(f"🎯 Целевой сервер: ID {K0YE1_GUILD_ID}")
        print(f"📁 Канал верификации: ID {K0YE1_AUTH_CHANNEL_ID}")
        print(f"📋 Отчетный канал: ID {K0YE1_REPORT_CHANNEL_ID}")
        print(f"🎖️ Выдаваемая роль: ID {K0YE1_AUTH_ROLE_ID}")
        print("=" * 65)

        guild = self.get_guild(K0YE1_GUILD_ID)
        if not guild:
            try:
                guild = await self.fetch_guild(K0YE1_GUILD_ID)
            except Exception as e:
                print(f"❌ Ошибка поиска сервера: {e}")
                if self.one_shot:
                    await self.close()
                return

        auth_channel = guild.get_channel(K0YE1_AUTH_CHANNEL_ID)
        if not auth_channel:
            try:
                auth_channel = await self.fetch_channel(K0YE1_AUTH_CHANNEL_ID)
            except Exception as e:
                print(f"❌ Ошибка поиска канала верификации: {e}")
                if self.one_shot:
                    await self.close()
                return

        report_channel = guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
        if not report_channel:
            try:
                report_channel = await self.fetch_channel(K0YE1_REPORT_CHANNEL_ID)
            except Exception:
                pass

        role = guild.get_role(K0YE1_AUTH_ROLE_ID)

        # 1. Очистка сообщений в канале верификации
        print(f"🧹 Очистка сообщений в канале #{auth_channel.name}...")
        try:
            deleted = await auth_channel.purge(limit=100)
            print(f"✅ Удалено {len(deleted)} старых сообщений.")
        except Exception as e:
            deleted = []
            print(f"⚠️ Ошибка очистки сообщений: {e}")

        # 2. Отправка официального embed с кнопкой аутентификации
        print("📤 Отправка сообщения верификации с кнопкой...")
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

        auth_msg = await auth_channel.send(embed=embed, view=K0ye1AuthView())
        print(f"✅ Сообщение аутентификации успешно опубликовано (ID: {auth_msg.id})")

        # 3. Отправка отчета в отчетный канал
        if report_channel:
            print(f"📢 Отправка отчета в канал #{report_channel.name}...")
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
                print("✅ Отчет успешно отправлен!")
            except Exception as e:
                print(f"⚠️ Ошибка отправки отчета: {e}")

        if self.one_shot:
            print("🚀 Разовая настройка завершена. Отключение бота...")
            await self.close()

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.author.id == self.user.id:
            return

        if not message.guild or not message.content:
            return

        prof_count, censored_text, found_words = check_and_censor_profanity(message.content)
        if prof_count > MAX_PROFANITY_LIMIT:
            try:
                await message.delete()
            except Exception as e:
                print(f"Не удалось удалить сообщение: {e}")

            warn_msg = (
                f"{message.author.mention} Ваше сообщение превысило лимит матов ({prof_count}/{MAX_PROFANITY_LIMIT})\n"
                f"Вот ваше сообщение без матов:\n"
                f"{censored_text}"
            )
            try:
                await message.channel.send(warn_msg)
            except Exception as e:
                print(f"Ошибка отправки предупреждения: {e}")

            log_chan = message.guild.get_channel(K0YE1_REPORT_CHANNEL_ID)
            if not log_chan:
                try:
                    log_chan = await self.fetch_channel(K0YE1_REPORT_CHANNEL_ID)
                except Exception:
                    pass

            if log_chan:
                log_embed = discord.Embed(
                    title="🚨 Нарушение триггера | Превышение лимита матов",
                    description=(
                        f"• **Пользователь:** {message.author.mention} (`{message.author.name}`)\n"
                        f"• **Канал:** {message.channel.mention} (`#{message.channel.name}`)\n"
                        f"• **Количество матов:** `{prof_count} / {MAX_PROFANITY_LIMIT}`\n"
                        f"• **Действие:** Исходное сообщение удалено, отправлена очищенная версия."
                    ),
                    color=0xED4245,
                    timestamp=datetime.now()
                )
                log_embed.add_field(
                    name="📝 Исходное сообщение",
                    value=message.content[:1024],
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

def main():
    token = load_discord_token()
    if not token:
        print("❌ Не найден DISCORD_BOT_TOKEN в файле Api_key.txt!")
        sys.exit(1)

    one_shot = "--once" in sys.argv
    bot = K0ye1ControlBot(one_shot=one_shot)
    bot.run(token)

if __name__ == "__main__":
    main()
