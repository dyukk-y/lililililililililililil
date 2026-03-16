import asyncio
import logging
import configparser
import os
from pathlib import Path
from typing import Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from dotenv import load_dotenv

from steam_manager import SteamManager

# Загрузка переменных окружения из .env файла (только для локальной разработки)
# На Bothost эта строка просто не найдет файл и не вызовет ошибку
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# ПОЛУЧЕНИЕ ТОКЕНА ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ============================================
# На Bothost токен будет передан через переменные окружения,
# которые вы настроили в панели управления
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    # Если токена нет, логируем критическую ошибку и останавливаем бота
    logger.critical("=" * 60)
    logger.critical("КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN не найден в переменных окружения!")
    logger.critical("Убедитесь, что вы добавили переменную BOT_TOKEN в панели управления Bothost.")
    logger.critical("=" * 60)
    # Выходим с ошибкой, чтобы бот не запустился без токена
    exit(1)

logger.info(f"✅ BOT_TOKEN успешно загружен из переменных окружения")

# ============================================
# ЗАГРУЗКА КОНФИГУРАЦИИ ИЗ INI ФАЙЛА
# ============================================
config = configparser.ConfigParser()
config_path = Path(__file__).parent / "config.ini"

# Проверяем наличие config.ini и создаем из примера если его нет
if not config_path.exists():
    example_path = Path(__file__).parent / "config.ini.example"
    if example_path.exists():
        import shutil
        shutil.copy(example_path, config_path)
        logger.info(f"✅ Создан файл {config_path} из примера. Пожалуйста, заполните его данными.")
        logger.info("⚠️ Бот остановлен до заполнения config.ini")
        exit(1)
    else:
        logger.critical(f"❌ Файл {config_path} не найден и нет примера config.ini.example!")
        logger.critical("Создайте файл config.ini вручную или добавьте config.ini.example в репозиторий.")
        exit(1)

# Читаем конфигурацию
try:
    config.read(config_path, encoding='utf-8')
    logger.info(f"✅ Файл {config_path} успешно загружен")
except Exception as e:
    logger.critical(f"❌ Ошибка чтения {config_path}: {e}")
    exit(1)

# Получение разрешенного ID пользователя
try:
    ALLOWED_USER_ID = int(config.get('telegram', 'allowed_user_id', fallback='0'))
    if ALLOWED_USER_ID == 0:
        logger.warning("⚠️ allowed_user_id не установлен в config.ini! Бот будет доступен всем!")
    else:
        logger.info(f"✅ Разрешенный пользователь: {ALLOWED_USER_ID}")
except ValueError:
    ALLOWED_USER_ID = 0
    logger.warning("⚠️ allowed_user_id должен быть числом! Бот будет доступен всем!")

# ============================================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ============================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Инициализация менеджера Steam
steam_manager = SteamManager()

# Загрузка аккаунтов из конфига
loaded_accounts = 0
for i in range(1, 4):
    section = f"account{i}"
    if config.has_section(section):
        games_str = config.get(section, 'games', fallback='')
        games_list = [game_id.strip() for game_id in games_str.split(',') if game_id.strip()]
        
        username = config.get(section, 'username', fallback='')
        password = config.get(section, 'password', fallback='')
        
        if username and password:
            steam_manager.add_account(
                name=section,
                username=username,
                password=password,
                games=games_list
            )
            loaded_accounts += 1
            logger.info(f"✅ Загружен аккаунт {section}: {username}")

logger.info(f"✅ Всего загружено аккаунтов: {loaded_accounts}")

# ============================================
# СОСТОЯНИЯ FSM
# ============================================
class AccountStates(StatesGroup):
    selecting_account = State()
    waiting_for_2fa = State()

# ============================================
# MIDDLEWARE ДЛЯ ПРОВЕРКИ ДОСТУПА
# ============================================
@dp.update.middleware()
async def access_middleware(handler, event, data):
    """Проверка, что пользователь имеет доступ к боту"""
    user_id = None
    
    if isinstance(event, types.Message):
        user_id = event.from_user.id
    elif isinstance(event, types.CallbackQuery):
        user_id = event.from_user.id
    
    if ALLOWED_USER_ID == 0:  # Если ID не указан, доступ открыт всем
        return await handler(event, data)
    elif user_id and user_id == ALLOWED_USER_ID:
        return await handler(event, data)
    elif user_id:
        await event.answer("⛔ У вас нет доступа к этому боту!")
        return
    else:
        return await handler(event, data)

# ============================================
# КЛАВИАТУРЫ
# ============================================
def get_main_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура"""
    buttons = [
        [InlineKeyboardButton(text="📋 Выбрать аккаунт", callback_data="select_account")],
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="global_stats")],
        [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="refresh")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_accounts_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора аккаунта"""
    buttons = []
    for name, account in steam_manager.accounts.items():
        buttons.append([
            InlineKeyboardButton(
                text=f"👤 Аккаунт {name[-1]} ({account.username})",
                callback_data=f"account_{name}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_account_control_keyboard(account_name: str, stats: Dict) -> InlineKeyboardMarkup:
    """Клавиатура управления аккаунтом"""
    buttons = []
    
    if stats.get('awaiting_2fa', False):
        buttons.append([InlineKeyboardButton(
            text="🔑 Ввести код Steam Guard", 
            callback_data=f"enter_2fa_{account_name}"
        )])
    elif stats['is_running']:
        buttons.append([InlineKeyboardButton(
            text="⏹️ Остановить", 
            callback_data=f"stop_{account_name}"
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text="▶️ Запустить", 
            callback_data=f"start_{account_name}"
        )])
    
    buttons.append([InlineKeyboardButton(
        text="📊 Статистика", 
        callback_data=f"stats_{account_name}"
    )])
    buttons.append([InlineKeyboardButton(
        text="🔙 К списку", 
        callback_data="select_account"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ============================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    await message.answer(
        "👋 **Добро пожаловать в Steam Hour Booster Bot!**\n\n"
        "Я помогу вам накручивать часы в играх Steam для нескольких аккаунтов.\n"
        "🔐 **Поддерживается Steam Guard** (мобильный аутентификатор и email)\n\n"
        "Выберите действие:",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    help_text = (
        "📖 **Доступные команды:**\n\n"
        "/start - Запустить бота и показать главное меню\n"
        "/help - Показать это сообщение\n\n"
        "**Как пользоваться:**\n"
        "1. Выберите аккаунт из списка\n"
        "2. Если требуется код Steam Guard, введите его\n"
        "3. Нажмите '▶️ Запустить' для начала накрутки\n"
        "4. Используйте '📊 Статистика' для проверки состояния\n"
        "5. Нажмите '⏹️ Остановить' для завершения\n\n"
        "**Поддерживается до 3 аккаунтов одновременно!**\n\n"
        "🔐 **Безопасность:**\n"
        "• Сессии сохраняются, не нужно вводить 2FA при каждом входе\n"
        "• Все данные хранятся локально\n"
        "• Используйте отдельные аккаунты для накрутки"
    )
    await message.answer(help_text, parse_mode="Markdown")

# ============================================
# ОБРАБОТЧИКИ CALLBACK-ЗАПРОСОВ
# ============================================
@dp.callback_query(F.data == "select_account")
async def select_account(callback: CallbackQuery):
    """Выбор аккаунта"""
    await callback.message.edit_text(
        "Выберите аккаунт для управления:",
        reply_markup=get_accounts_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "global_stats")
async def global_stats(callback: CallbackQuery):
    """Показать общую статистику всех аккаунтов"""
    stats = await steam_manager.get_all_stats()
    
    message_text = "📊 **Общая статистика:**\n\n"
    
    for account_name, account_stats in stats.items():
        status = "✅ Работает" if account_stats['is_running'] else "⏸️ Остановлен"
        if account_stats.get('awaiting_2fa', False):
            status = "🔑 Ожидает код Steam Guard"
        
        games = ", ".join(account_stats['games']) if account_stats['games'] else "нет"
        login_status = "✅ В сети" if account_stats['logged_in'] else "❌ Не в сети"
        
        message_text += (
            f"**{account_name}**\n"
            f"👤 Логин: {account_stats['username']}\n"
            f"📌 Статус: {status}\n"
            f"🔐 {login_status}\n"
            f"🎮 Игры: {games}\n\n"
        )
    
    await callback.message.edit_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]]
        ),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "refresh")
async def refresh_status(callback: CallbackQuery):
    """Обновление статуса"""
    await callback.message.edit_text(
        "🔄 Статус обновлен!\n\nВозвращаюсь в главное меню...",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    """Возврат в главное меню"""
    await callback.message.edit_text(
        "Главное меню:",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("account_"))
async def account_selected(callback: CallbackQuery):
    """Выбран конкретный аккаунт"""
    account_name = callback.data.replace("account_", "")
    stats = await steam_manager.get_account_stats(account_name)
    
    if stats:
        status_text = "✅ Работает" if stats['is_running'] else "⏸️ Остановлен"
        if stats.get('awaiting_2fa', False):
            status_text = "🔑 Ожидает код Steam Guard"
        
        games_text = ", ".join(stats['games']) if stats['games'] else "нет"
        
        await callback.message.edit_text(
            f"**Управление аккаунтом:**\n"
            f"👤 Логин: {stats['username']}\n"
            f"📌 Статус: {status_text}\n"
            f"🎮 Игры: {games_text}\n"
            f"🔐 Steam: {'✅ В сети' if stats['logged_in'] else '❌ Не в сети'}\n\n"
            f"Выберите действие:",
            reply_markup=get_account_control_keyboard(account_name, stats),
            parse_mode="Markdown"
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("start_"))
async def start_account(callback: CallbackQuery, state: FSMContext):
    """Запуск накрутки для аккаунта"""
    account_name = callback.data.replace("start_", "")
    
    # Отправляем уведомление о начале процесса
    await callback.message.edit_text(
        f"🔄 Подключаюсь к Steam аккаунту {account_name}...\n"
        f"Это может занять несколько секунд.",
        reply_markup=None
    )
    
    # Пытаемся запустить
    result = await steam_manager.start_account(account_name)
    
    if result["success"]:
        stats = await steam_manager.get_account_stats(account_name)
        await callback.message.edit_text(
            f"✅ **Накрутка запущена!**\n\n"
            f"Аккаунт: {account_name}\n"
            f"👤 {stats['username']}\n"
            f"🎮 Игры: {', '.join(stats['games'])}",
            reply_markup=get_account_control_keyboard(account_name, stats),
            parse_mode="Markdown"
        )
    elif result.get("awaiting_code", False):
        # Требуется код 2FA
        await state.set_state(AccountStates.waiting_for_2fa)
        await state.update_data(account_name=account_name)
        
        await callback.message.edit_text(
            f"🔑 **Требуется код Steam Guard**\n\n"
            f"Аккаунт: {account_name}\n"
            f"👤 {result.get('username', '')}\n\n"
            f"Пожалуйста, введите код из приложения Steam Guard или email:\n"
            f"(отправьте его как обычное сообщение)",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"account_{account_name}")
                ]]
            ),
            parse_mode="Markdown"
        )
    else:
        # Ошибка
        await callback.message.edit_text(
            f"❌ **Ошибка запуска**\n\n"
            f"Аккаунт: {account_name}\n"
            f"Причина: {result.get('message', 'Неизвестная ошибка')}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="🔙 Назад", callback_data=f"account_{account_name}")
                ]]
            ),
            parse_mode="Markdown"
        )
    
    await callback.answer()

@dp.message(AccountStates.waiting_for_2fa)
async def process_2fa_code(message: types.Message, state: FSMContext):
    """Обработка введенного кода Steam Guard"""
    data = await state.get_data()
    account_name = data.get('account_name')
    
    if not account_name:
        await state.clear()
        await message.answer("❌ Ошибка: аккаунт не найден. Начните заново с /start")
        return
    
    code = message.text.strip()
    
    # Проверяем, что код состоит из цифр
    if not code.isdigit() or len(code) not in [5, 6]:
        await message.answer(
            "❌ Неверный формат кода. Код должен состоять из 5 или 6 цифр.\n"
            "Попробуйте еще раз:"
        )
        return
    
    await message.answer(f"🔄 Проверяю код для {account_name}...")
    
    # Отправляем код в менеджер
    result = await steam_manager.start_account(account_name, code)
    
    if result["success"]:
        stats = await steam_manager.get_account_stats(account_name)
        await message.answer(
            f"✅ **Код принят! Накрутка запущена!**\n\n"
            f"Аккаунт: {account_name}\n"
            f"👤 {stats['username']}\n"
            f"🎮 Игры: {', '.join(stats['games'])}",
            reply_markup=get_account_control_keyboard(account_name, stats),
            parse_mode="Markdown"
        )
        await state.clear()
    else:
        await message.answer(
            f"❌ **Ошибка: неверный код**\n\n"
            f"Попробуйте еще раз или нажмите кнопку отмены.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"account_{account_name}")
                ]]
            ),
            parse_mode="Markdown"
        )

@dp.callback_query(F.data.startswith("enter_2fa_"))
async def enter_2fa_code(callback: CallbackQuery, state: FSMContext):
    """Запрос на ввод 2FA кода"""
    account_name = callback.data.replace("enter_2fa_", "")
    
    await state.set_state(AccountStates.waiting_for_2fa)
    await state.update_data(account_name=account_name)
    
    await callback.message.edit_text(
        f"🔑 **Введите код Steam Guard**\n\n"
        f"Аккаунт: {account_name}\n\n"
        f"Пожалуйста, введите код из приложения Steam Guard:\n"
        f"(отправьте его как обычное сообщение)",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"account_{account_name}")
            ]]
        ),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("stop_"))
async def stop_account(callback: CallbackQuery):
    """Остановка накрутки для аккаунта"""
    account_name = callback.data.replace("stop_", "")
    result = await steam_manager.stop_account(account_name)
    
    if result["success"]:
        stats = await steam_manager.get_account_stats(account_name)
        await callback.message.edit_text(
            f"⏹️ **Накрутка остановлена**\n\n"
            f"Аккаунт: {account_name}\n"
            f"👤 {stats['username']}",
            reply_markup=get_account_control_keyboard(account_name, stats),
            parse_mode="Markdown"
        )
    else:
        await callback.message.edit_text(
            f"❌ **Ошибка остановки**\n\n"
            f"Причина: {result.get('message', 'Неизвестная ошибка')}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="🔙 Назад", callback_data=f"account_{account_name}")
                ]]
            ),
            parse_mode="Markdown"
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("stats_"))
async def account_stats(callback: CallbackQuery):
    """Показать статистику конкретного аккаунта"""
    account_name = callback.data.replace("stats_", "")
    stats = await steam_manager.get_account_stats(account_name)
    
    if stats:
        status = "✅ Работает" if stats['is_running'] else "⏸️ Остановлен"
        if stats.get('awaiting_2fa', False):
            status = "🔑 Ожидает код Steam Guard"
        
        games = ", ".join(stats['games']) if stats['games'] else "нет"
        
        message_text = (
            f"**📊 Статистика {account_name}**\n\n"
            f"👤 **Логин:** {stats['username']}\n"
            f"📌 **Статус:** {status}\n"
            f"🎮 **Игры:** {games}\n"
            f"🔐 **Steam:** {'✅ В сети' if stats['logged_in'] else '❌ Не в сети'}\n"
        )
        
        if stats['steam_id']:
            message_text += f"🆔 **Steam ID:** {stats['steam_id']}\n"
        
        message_text += f"\n⚡ Время работы: в разработке..."
        
        await callback.message.edit_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data=f"account_{account_name}")]
                ]
            ),
            parse_mode="Markdown"
        )
    await callback.answer()

# ============================================
# ЗАПУСК БОТА
# ============================================
async def main():
    logger.info("=" * 60)
    logger.info("🚀 Запуск Steam Hour Booster Bot...")
    logger.info(f"✅ Загружено аккаунтов: {len(steam_manager.accounts)}")
    
    if ALLOWED_USER_ID == 0:
        logger.info("⚠️ Режим доступа: открыт для всех пользователей")
    else:
        logger.info(f"🔐 Режим доступа: только для пользователя {ALLOWED_USER_ID}")
    
    logger.info("=" * 60)
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
