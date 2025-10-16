import os
import logging
import sqlite3
import re
import asyncio
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ChatJoinRequest
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, CallbackQueryHandler, filters, ChatMemberHandler, ChatJoinRequestHandler

# Загрузка переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [6531897948,7540286215]
ARCHIVE_CHANNEL_ID = os.getenv('ARCHIVE_CHANNEL_ID', '')

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN must be set in environment variables")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# БАЗА ДАННЫХ
class Database:
    def __init__(self, db_path="/data/korean_doramas.db"):
        # Используем /data для Railway persistent storage
        os.makedirs('/data', exist_ok=True)
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Основная таблица для дорам
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS doramas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dorama_code TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                release_year INTEGER,
                genre TEXT,
                rating REAL DEFAULT 0,
                poster_file_id TEXT,
                created_date DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица для эпизодов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dorama_code TEXT NOT NULL,
                episode_number INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                caption TEXT,
                duration INTEGER DEFAULT 0,
                file_size INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (dorama_code) REFERENCES doramas (dorama_code),
                UNIQUE(dorama_code, episode_number)
            )
        ''')
        
        # Пользователи
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activity DATETIME DEFAULT CURRENT_TIMESTAMP,
                total_requests INTEGER DEFAULT 0
            )
        ''')
        
        # Каналы для подписки
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                channel_id INTEGER PRIMARY KEY,
                username TEXT,
                title TEXT,
                invite_link TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                is_private BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # Заявки на вступление в приватные каналы
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channel_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_id INTEGER,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (channel_id) REFERENCES channels (channel_id),
                UNIQUE(user_id, channel_id)
            )
        ''')
        
        # Настройки бота
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Добавляем начальные настройки
        cursor.execute('''
            INSERT OR IGNORE INTO bot_settings (key, value) VALUES 
            ('welcome_message', '🎬 Xush kelibsiz! Koreys doramalarini tomosha qilish uchun maxsus bot.'),
            ('help_message', '🤖 Botdan foydalanish uchun kerakli bolimni tanlang.'),
            ('archive_channel', ?)
        ''', (ARCHIVE_CHANNEL_ID,))
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных корейских дорам инициализирована")

    # МЕТОДЫ ДЛЯ РАБОТЫ С ДОРАМАМИ
    def add_dorama(self, dorama_code, title, description="", release_year=None, genre="", poster_file_id=None):
        """Добавляет новую дораму"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO doramas 
                (dorama_code, title, description, release_year, genre, poster_file_id) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (dorama_code, title, description, release_year, genre, poster_file_id))
            
            conn.commit()
            logger.info(f"✅ Добавлена дорама: {title} (Код: {dorama_code})")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка добавления дорамы: {e}")
            return False
        finally:
            conn.close()

    def get_dorama(self, dorama_code):
        """Получает информацию о дораме"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT dorama_code, title, description, release_year, genre, rating, poster_file_id
            FROM doramas WHERE dorama_code = ?
        ''', (dorama_code,))
        
        result = cursor.fetchone()
        conn.close()
        return result

    def get_all_doramas(self):
        """Получает все дорамы"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT d.dorama_code, d.title, d.release_year, d.genre, d.rating,
                   COUNT(e.id) as episode_count
            FROM doramas d
            LEFT JOIN episodes e ON d.dorama_code = e.dorama_code
            GROUP BY d.dorama_code
            ORDER BY d.title
        ''')
        
        result = cursor.fetchall()
        conn.close()
        return result

    def search_doramas(self, query):
        """Поиск дорам"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        search_pattern = f'%{query}%'
        cursor.execute('''
            SELECT d.dorama_code, d.title, d.release_year, d.genre,
                   COUNT(e.id) as episode_count
            FROM doramas d
            LEFT JOIN episodes e ON d.dorama_code = e.dorama_code
            WHERE d.title LIKE ? OR d.dorama_code LIKE ? OR d.genre LIKE ?
            GROUP BY d.dorama_code
            ORDER BY d.title
            LIMIT 20
        ''', (search_pattern, search_pattern, search_pattern))
        
        result = cursor.fetchall()
        conn.close()
        return result

    def delete_dorama(self, dorama_code):
        """Удаляет дораму и все её эпизоды"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Сначала удаляем эпизоды
            cursor.execute('DELETE FROM episodes WHERE dorama_code = ?', (dorama_code,))
            # Затем удаляем дораму
            cursor.execute('DELETE FROM doramas WHERE dorama_code = ?', (dorama_code,))
            
            conn.commit()
            logger.info(f"✅ Дорама {dorama_code} удалена")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка удаления дорамы: {e}")
            return False
        finally:
            conn.close()

    # МЕТОДЫ ДЛЯ РАБОТЫ С ЭПИЗОДАМИ
    def add_episode(self, dorama_code, episode_number, file_id, caption="", duration=0, file_size=0):
        """Добавляет эпизод к дораме"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO episodes 
                (dorama_code, episode_number, file_id, caption, duration, file_size) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (dorama_code, episode_number, file_id, caption, duration, file_size))
            
            conn.commit()
            logger.info(f"✅ Добавлен эпизод {episode_number} для дорамы {dorama_code}")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка добавления эпизода: {e}")
            return False
        finally:
            conn.close()

    def get_episode(self, dorama_code, episode_number):
        """Получает информацию об эпизоде"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT e.episode_number, e.file_id, e.caption, e.duration, e.file_size, e.views,
                   d.title, d.dorama_code
            FROM episodes e
            JOIN doramas d ON e.dorama_code = d.dorama_code
            WHERE e.dorama_code = ? AND e.episode_number = ?
        ''', (dorama_code, episode_number))
        
        result = cursor.fetchone()
        conn.close()
        return result

    def get_all_episodes(self, dorama_code):
        """Получает все эпизоды дорамы"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT episode_number, file_id, caption, duration, file_size, views
            FROM episodes 
            WHERE dorama_code = ?
            ORDER BY episode_number
        ''', (dorama_code,))
        
        result = cursor.fetchall()
        conn.close()
        return result

    def get_total_episodes(self, dorama_code):
        """Получает общее количество эпизодов"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM episodes WHERE dorama_code = ?', (dorama_code,))
        result = cursor.fetchone()[0]
        conn.close()
        return result

    def delete_episode(self, dorama_code, episode_number):
        """Удаляет эпизод"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM episodes WHERE dorama_code = ? AND episode_number = ?', 
                         (dorama_code, episode_number))
            conn.commit()
            logger.info(f"✅ Эпизод {episode_number} дорамы {dorama_code} удален")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка удаления эпизода: {e}")
            return False
        finally:
            conn.close()

    def increment_views(self, dorama_code, episode_number):
        """Увеличивает счетчик просмотров"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE episodes SET views = views + 1 
            WHERE dorama_code = ? AND episode_number = ?
        ''', (dorama_code, episode_number))
        conn.commit()
        conn.close()

    # ПОЛЬЗОВАТЕЛИ
    def add_user(self, user_id, username=None, first_name=None, last_name=None):
        """Добавляет пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
            (user_id, username, first_name, last_name)
        )
        conn.commit()
        conn.close()

    def update_user_activity(self, user_id):
        """Обновляет активность пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET last_activity = CURRENT_TIMESTAMP, total_requests = total_requests + 1 WHERE user_id = ?',
            (user_id,)
        )
        conn.commit()
        conn.close()

    def get_all_users(self):
        """Получает всех пользователей"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, username, first_name, last_name FROM users')
        result = cursor.fetchall()
        conn.close()
        return result

    def get_active_users_count(self):
        """Получает количество активных пользователей (за последние 30 дней)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users WHERE last_activity >= datetime("now", "-30 days")')
        result = cursor.fetchone()[0]
        conn.close()
        return result

    def get_admin_stats(self):
        """Получает статистику для админов"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Общее количество дорам
        cursor.execute('SELECT COUNT(*) FROM doramas')
        total_doramas = cursor.fetchone()[0]
        
        # Общее количество эпизодов
        cursor.execute('SELECT COUNT(*) FROM episodes')
        total_episodes = cursor.fetchone()[0]
        
        # Общее количество пользователей
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        # Самые популярные дорамы (по просмотрам)
        cursor.execute('''
            SELECT d.title, d.dorama_code, SUM(e.views) as total_views
            FROM doramas d
            JOIN episodes e ON d.dorama_code = e.dorama_code
            GROUP BY d.dorama_code
            ORDER BY total_views DESC
            LIMIT 5
        ''')
        popular_doramas = cursor.fetchall()
        
        # Количество активных пользователей за сегодня
        cursor.execute('''
            SELECT COUNT(DISTINCT user_id) FROM users 
            WHERE DATE(last_activity) = DATE("now")
        ''')
        daily_active = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_doramas': total_doramas,
            'total_episodes': total_episodes,
            'total_users': total_users,
            'popular_doramas': popular_doramas,
            'daily_active': daily_active
        }

    # МЕТОДЫ ДЛЯ РАБОТЫ С КАНАЛАМИ
    def get_all_channels(self):
        """Получает все каналы"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT channel_id, username, title, invite_link, is_private FROM channels WHERE is_active = TRUE')
        result = cursor.fetchall()
        conn.close()
        return result
    
    def add_channel(self, channel_id, username="", title=None, invite_link=None, is_private=False):
        """Добавляет канал в базу данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                'INSERT OR REPLACE INTO channels (channel_id, username, title, invite_link, is_private) VALUES (?, ?, ?, ?, ?)',
                (channel_id, username, title, invite_link, is_private)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Kanal qoshishda xato: {e}")
            return False
        finally:
            conn.close()
    
    def delete_channel(self, channel_id):
        """Удаляет канал из базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('DELETE FROM channels WHERE channel_id = ?', (channel_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Kanalni ochirishda xato: {e}")
            return False
        finally:
            conn.close()

    # МЕТОДЫ ДЛЯ РАБОТЫ С ЗАЯВКАМИ
    def add_channel_request(self, user_id, channel_id, status='pending'):
        """Добавляет или обновляет заявку на вступление в канал"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO channel_requests 
                (user_id, channel_id, status, updated_at) 
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (user_id, channel_id, status))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ So'rov qoshishda xato: {e}")
            return False
        finally:
            conn.close()
    
    def get_channel_request(self, user_id, channel_id):
        """Получает информацию о заявке пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT status, created_at FROM channel_requests WHERE user_id = ? AND channel_id = ?',
            (user_id, channel_id)
        )
        result = cursor.fetchone()
        conn.close()
        return result
    
    def get_pending_requests_count(self, channel_id=None):
        """Получает количество ожидающих заявок"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if channel_id:
            cursor.execute(
                'SELECT COUNT(*) FROM channel_requests WHERE status = "pending" AND channel_id = ?',
                (channel_id,)
            )
        else:
            cursor.execute('SELECT COUNT(*) FROM channel_requests WHERE status = "pending"')
        
        result = cursor.fetchone()[0]
        conn.close()
        return result
    
    def update_channel_request_status(self, user_id, channel_id, status):
        """Обновляет статус заявки"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                UPDATE channel_requests 
                SET status = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE user_id = ? AND channel_id = ?
            ''', (status, user_id, channel_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"❌ So'rov yangilashda xato: {e}")
            return False
        finally:
            conn.close()

    # МЕТОДЫ ДЛЯ НАСТРОЕК
    def get_setting(self, key):
        """Получает значение настройки"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM bot_settings WHERE key = ?', (key,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def update_setting(self, key, value):
        """Обновляет значение настройки"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)', (key, value))
        conn.commit()
        conn.close()
        return True

# СОЗДАЕМ ОБЪЕКТ БАЗЫ ДАННЫХ
db = Database()

# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ПРОВЕРКИ ПОДПИСКИ
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет подписку на все каналы - РАЗДЕЛЬНАЯ ПРОВЕРКА"""
    channels = db.get_all_channels()
    not_subscribed = []
    
    if not channels:
        return []
    
    for channel_id, username, title, invite_link, is_private in channels:
        try:
            if is_private:
                # ДЛЯ ПРИВАТНЫХ КАНАЛОВ - проверяем заявки
                request = db.get_channel_request(user_id, channel_id)
                if not request or request[0] not in ['pending', 'approved']:
                    # Нет активной заявки - добавляем в список
                    not_subscribed.append((channel_id, username, title, invite_link, is_private))
                    
            else:
                # ДЛЯ ПУБЛИЧНЫХ КАНАЛОВ - стандартная проверка подписки
                member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
                if member.status in ['left', 'kicked']:
                    not_subscribed.append((channel_id, username, title, invite_link, is_private))
                    
        except Exception as e:
            logger.warning(f"Kanal {channel_id} tekshirishda xato: {e}")
            not_subscribed.append((channel_id, username, title, invite_link, is_private))
    
    return not_subscribed

async def require_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет подписку перед выполнением действия"""
    user = update.effective_user
    if not user:
        return False
    
    if user.id in ADMIN_IDS:
        return True
    
    db.update_user_activity(user.id)
    
    not_subscribed = await check_subscription(user.id, context)
    
    if not_subscribed:
        await show_subscription_required(update, context, not_subscribed)
        return False
    
    return True

async def show_subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE, not_subscribed_channels):
    """Показывает требования подписки"""
    if not not_subscribed_channels:
        return True
    
    keyboard = []
    for channel_id, username, title, invite_link, is_private in not_subscribed_channels:
        channel_name = title or username or f"Kanal {channel_id}"
        
        if is_private and invite_link:
            url = invite_link
            button_text = f"🔒 {channel_name} (Maxfiy kanal - ariza qoldiring)"
        elif invite_link:
            url = invite_link
            button_text = f"📢 {channel_name}"
        else:
            clean_username = (username or '').lstrip('@')
            if clean_username:
                url = f"https://t.me/{clean_username}"
                button_text = f"📢 {channel_name}"
            else:
                continue
        
        keyboard.append([InlineKeyboardButton(button_text, url=url)])
    
    keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_subscription")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "📢 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:\n\n"
    for channel_id, username, title, invite_link, is_private in not_subscribed_channels:
        channel_name = title or username or f"Kanal {channel_id}"
        if is_private:
            text += f"• 🔒 {channel_name} (Maxfiy kanal - ariza qoldiring)\n"
        else:
            text += f"• 📢 {channel_name}\n"
    
    text += "\nObuna bo'lgachingiz yoki ariza qoldirgachingiz «✅ Tekshirish» tugmasini bosing."
    
    try:
        if update.callback_query:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)
        return False
    except Exception as e:
        logger.error(f"Obunani ko'rsatish xatosi: {e}")
        return False

async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    not_subscribed = await check_subscription(user.id, context)
    
    if not not_subscribed:
        await query.edit_message_text(
            "✅ Ajoyib! Endi siz botdan foydalanishingiz mumkin.",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await show_subscription_required(update, context, not_subscribed)

# НОВЫЕ ОБРАБОТЧИКИ ДЛЯ ЗАЯВОК
async def handle_chat_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает новые заявки на вступление в приватные каналы"""
    join_request = update.chat_join_request
    user = join_request.from_user
    chat = join_request.chat
    
    # Добавляем пользователя в базу если его нет
    db.add_user(user.id, user.username, user.first_name, user.last_name)
    
    # Сохраняем заявку в базу данных
    success = db.add_channel_request(user.id, chat.id, 'pending')
    
    if success:
        logger.info(f"Yangi so'rov: {user.id} -> {chat.id}")
        
        # Уведомляем админов о новой заявке
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🆕 Yangi so'rov!\n\n"
                         f"👤 Foydalanuvchi: {user.first_name} (@{user.username or 'Noma lum'})\n"
                         f"📢 Kanal: {chat.title}\n"
                         f"🆔 User ID: {user.id}\n"
                         f"🆔 Chat ID: {chat.id}"
                )
            except Exception as e:
                logger.error(f"Adminni xabarlashda xato {admin_id}: {e}")

async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает изменения статуса участников в каналах"""
    chat_member = update.chat_member
    user = chat_member.new_chat_member.user
    chat = update.chat_member.chat
    
    # Проверяем, является ли канал приватным в нашей базе
    channels = db.get_all_channels()
    channel_ids = [channel[0] for channel in channels]
    
    if chat.id not in channel_ids:
        return
    
    # Получаем информацию о канале
    channel_info = next((c for c in channels if c[0] == chat.id), None)
    if not channel_info:
        return
    
    channel_id, username, title, invite_link, is_private = channel_info
    
    if not is_private:
        return  # Только для приватных каналов
    
    # Обрабатываем изменения статуса
    new_status = chat_member.new_chat_member.status
    old_status = chat_member.old_chat_member.status
    
    # Пользователь принят в канал
    if new_status in ['member', 'administrator'] and old_status in ['left', 'kicked']:
        db.add_channel_request(user.id, chat.id, 'approved')
        logger.info(f"Foydalanuvchi qabul qilindi: {user.id} -> {chat.id}")
    
    # Пользователь вышел из канала
    elif new_status in ['left', 'kicked'] and old_status in ['member', 'administrator']:
        db.add_channel_request(user.id, chat.id, 'cancelled')
        logger.info(f"Foydalanuvchi chiqib ketdi: {user.id} -> {chat.id}")

# КЛАВИАТУРЫ
def get_main_keyboard():
    """Главная клавиатура"""
    keyboard = [
        [KeyboardButton("🔍 Qidirish"), KeyboardButton("📚 Barcha doramalar")],
        [KeyboardButton("🆕 Yangi qo'shilgan"), KeyboardButton("📊 Mashhurlar")],
        [KeyboardButton("⭐ Tasodifiy"), KeyboardButton("ℹ️ Yordam")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_main_menu_keyboard():
    """Inline клавиатура для главного меню"""
    keyboard = [
        [InlineKeyboardButton("🔍 Qidirish", callback_data="search")],
        [InlineKeyboardButton("📚 Barcha doramalar", callback_data="all_doramas_0")],
        [InlineKeyboardButton("🆕 Yangi qo'shilgan", callback_data="recent_doramas_0")],
        [InlineKeyboardButton("📊 Mashhurlar", callback_data="popular_doramas_0")],
        [InlineKeyboardButton("⭐ Tasodifiy dorama", callback_data="random_dorama")],
        [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    """Клавиатура для админов"""
    keyboard = [
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton("🎬 Doramalar", callback_data="admin_doramas_0")],
        [InlineKeyboardButton("🗑️ O'chirish", callback_data="admin_delete_0")],
        [InlineKeyboardButton("📢 Kanallar", callback_data="admin_channels")],
        [InlineKeyboardButton("⚙️ Sozlamalar", callback_data="admin_settings")],
        [InlineKeyboardButton("🆕 So'rovlar", callback_data="admin_requests_0")],
        [InlineKeyboardButton("📢 Xabar yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_settings_keyboard():
    """Клавиатура для настроек"""
    keyboard = [
        [InlineKeyboardButton("👋 Xush kelish xabarini o'zgartirish", callback_data="admin_set_welcome")],
        [InlineKeyboardButton("ℹ️ Yordam xabarini o'zgartirish", callback_data="admin_set_help")],
        [InlineKeyboardButton("📁 Arxiv kanali", callback_data="admin_set_archive")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_dorama_keyboard(dorama_code, total_episodes):
    """Клавиатура для выбора эпизодов"""
    keyboard = []
    
    # Создаем кнопки для первых 10 эпизодов или всех, если их меньше
    episodes_to_show = min(10, total_episodes)
    
    # Разбиваем на строки по 5 эпизодов
    for i in range(0, episodes_to_show, 5):
        row = []
        for j in range(i, min(i + 5, episodes_to_show)):
            ep_num = j + 1
            row.append(InlineKeyboardButton(f"{ep_num}", callback_data=f"watch_{dorama_code}_{ep_num}"))
        keyboard.append(row)
    
    # Если эпизодов больше 10, добавляем кнопку "Все эпизоды"
    if total_episodes > 10:
        keyboard.append([InlineKeyboardButton("📋 Barcha qismlar", callback_data=f"all_episodes_{dorama_code}")])
    
    keyboard.append([InlineKeyboardButton("🎬 Barcha qismlarni yuborish", callback_data=f"send_all_{dorama_code}")])
    keyboard.append([InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(keyboard)

def get_all_episodes_keyboard(dorama_code, page=0, episodes_per_page=15):
    """Клавиатура для всех эпизодов с пагинацией"""
    episodes = db.get_all_episodes(dorama_code)
    total_episodes = len(episodes)
    total_pages = (total_episodes + episodes_per_page - 1) // episodes_per_page
    
    keyboard = []
    
    # Эпизоды текущей страницы
    start_idx = page * episodes_per_page
    end_idx = min(start_idx + episodes_per_page, total_episodes)
    
    for i in range(start_idx, end_idx, 3):
        row = []
        for j in range(i, min(i + 3, end_idx)):
            ep_num = episodes[j][0]
            row.append(InlineKeyboardButton(f"{ep_num}", callback_data=f"watch_{dorama_code}_{ep_num}"))
        keyboard.append(row)
    
    # Навигация по страницам
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"episodes_{dorama_code}_{page-1}"))
        
        nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="current_page"))
        
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"episodes_{dorama_code}_{page+1}"))
        
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🎬 Barcha qismlarni yuborish", callback_data=f"send_all_{dorama_code}")])
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data=f"dorama_{dorama_code}")])
    
    return InlineKeyboardMarkup(keyboard)

def get_dorama_list_keyboard(doramas, prefix="dorama"):
    """Клавиатура списка дорам"""
    keyboard = []
    
    for dorama in doramas:
        # Безопасная распаковка с проверкой количества элементов
        if len(dorama) == 6:
            dorama_code, title, year, genre, rating, episode_count = dorama
        elif len(dorama) == 5:
            dorama_code, title, year, genre, episode_count = dorama
            rating = 0  # Добавляем рейтинг по умолчанию
        else:
            continue  # Пропускаем некорректные записи
        
        display_text = f"📺 {title}"
        if year:
            display_text += f" ({year})"
        if episode_count:
            display_text += f" - {episode_count}qism"
        
        keyboard.append([InlineKeyboardButton(display_text, callback_data=f"{prefix}_{dorama_code}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_admin_dorama_list_keyboard(doramas, page, total_pages, delete_mode=False):
    """Клавиатура списка дорам для админов"""
    keyboard = []
    
    for dorama_code, title, year, genre, rating, episode_count in doramas:
        display_text = f"📺 {title} ({episode_count}q)"
        if delete_mode:
            keyboard.append([
                InlineKeyboardButton(display_text, callback_data=f"admin_dorama_info_{dorama_code}"),
                InlineKeyboardButton("❌", callback_data=f"admin_delete_confirm_{dorama_code}")
            ])
        else:
            keyboard.append([InlineKeyboardButton(display_text, callback_data=f"admin_dorama_info_{dorama_code}")])
    
    # Пагинация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"admin_doramas_{page-1}" if not delete_mode else f"admin_delete_{page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="current_page"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"admin_doramas_{page+1}" if not delete_mode else f"admin_delete_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    # Кнопки действий
    action_buttons = []
    if not delete_mode:
        action_buttons.append(InlineKeyboardButton("🗑️ O'chirish", callback_data="admin_delete_0"))
    else:
        action_buttons.append(InlineKeyboardButton("📋 Ko'rish", callback_data="admin_doramas_0"))
    
    action_buttons.append(InlineKeyboardButton("🔙 Admin", callback_data="admin_menu"))
    keyboard.append(action_buttons)
    
    return InlineKeyboardMarkup(keyboard)

def get_admin_delete_confirmation_keyboard(dorama_code):
    """Клавиатура подтверждения удаления дорамы"""
    keyboard = [
        [
            InlineKeyboardButton("✅ HA", callback_data=f"admin_confirm_delete_{dorama_code}"),
            InlineKeyboardButton("❌ BEKOR", callback_data="admin_delete_0")
        ],
        [InlineKeyboardButton("🔙 Admin", callback_data="admin_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_requests_keyboard(requests, page, total_pages):
    """Клавиатура для управления заявками"""
    keyboard = []
    
    for user_id, channel_id, status, created_at, username, first_name, title in requests:
        user_display = f"@{username}" if username else first_name
        request_text = f"{user_display} - {title[:20]}..."
        keyboard.append([
            InlineKeyboardButton(request_text, callback_data=f"admin_request_info_{user_id}_{channel_id}"),
            InlineKeyboardButton("✅", callback_data=f"admin_approve_request_{user_id}_{channel_id}")
        ])
    
    # Пагинация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"admin_requests_{page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="current_page"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"admin_requests_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Admin", callback_data="admin_menu")])
    
    return InlineKeyboardMarkup(keyboard)

# ОСНОВНЫЕ ФУНКЦИИ
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    db.add_user(user.id, user.username, user.first_name, user.last_name)
    db.update_user_activity(user.id)
    
    if user.id in ADMIN_IDS:
        await update.message.reply_text(
            "👨‍💻 Admin paneliga xush kelibsiz!",
            reply_markup=get_admin_keyboard()
        )
        return
    
    if not await require_subscription(update, context):
        return
    
    welcome_message = db.get_setting('welcome_message') or "🎬 Xush kelibsiz! Koreys doramalarini tomosha qilish uchun maxsus bot."
    
    await update.message.reply_text(
        f"{welcome_message}\n\n"
        f"Salom, {user.first_name}! Kerakli bo'limni tanlang:",
        reply_markup=get_main_keyboard()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    text = update.message.text.strip()
    
    db.update_user_activity(user.id)
    
    if user.id not in ADMIN_IDS:
        if not await require_subscription(update, context):
            return
    
    if text == "🔍 Qidirish":
        await update.message.reply_text(
            "🔍 Dorama nomini yoki kodini kiriting:\n\n"
            "Misol: <code>Yulduzlar</code> yoki <code>YL2024</code>",
            parse_mode="HTML"
        )
    
    elif text == "📚 Barcha doramalar":
        await show_all_doramas(update, context)
    
    elif text == "🆕 Yangi qo'shilgan":
        await show_recent_doramas(update, context)
    
    elif text == "📊 Mashhurlar":
        await show_popular_doramas(update, context)
    
    elif text == "⭐ Tasodifiy":
        await send_random_dorama(update, context)
    
    elif text == "ℹ️ Yordam":
        await show_help(update, context)
    
    else:
        # Проверяем, не является ли это ответом на подтверждение рассылки
        if 'broadcast_message' in context.user_data:
            await handle_broadcast_confirmation(update, context)
        else:
            await search_doramas(update, context, text)

async def search_doramas(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    """Поиск дорам"""
    doramas = db.search_doramas(query)
    
    if not doramas:
        await update.message.reply_text(
            f"❌ '{query}' bo'yicha doramalar topilmadi\n\n"
            "Boshqa nom yoki kod bilan urunib ko'ring."
        )
        return
    
    if len(doramas) == 1:
        dorama_code = doramas[0][0]
        await send_all_episodes(update, context, dorama_code)
    else:
        text = f"🔍 '{query}' bo'yicha topilgan doramalar ({len(doramas)} ta):\n\n"
for i, dorama in enumerate(doramas, 1):
    if len(dorama) >= 5:
        code, title, year, genre, episode_count = dorama[:5]
    else:
        continue  # Пропускаем некорректные записи            text += f"{i}. {title}"
            if year:
                text += f" ({year})"
            if episode_count:
                text += f" - {episode_count} qism"
            text += "\n"
        
        await update.message.reply_text(text, reply_markup=get_dorama_list_keyboard(doramas))

async def send_all_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, dorama_code):
    """Отправляет все эпизоды дорамы подряд"""
    dorama = db.get_dorama(dorama_code)
    episodes = db.get_all_episodes(dorama_code)
    
    if not dorama or not episodes:
        # Проверяем тип обновления
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text("❌ Dorama yoki qismlar topilmadi")
        else:
            await update.message.reply_text("❌ Dorama yoki qismlar topilmadi")
        return
    
    code, title, description, release_year, genre, rating, poster = dorama
    
    # Определяем пользователя в зависимости от типа обновления
    if hasattr(update, 'callback_query') and update.callback_query:
        user = update.callback_query.from_user
        is_callback = True
    else:
        user = update.effective_user
        is_callback = False
    
    # Отправляем информацию о дораме
    info_text = f"📺 **{title}**\n\n"
    
    if description:
        info_text += f"📖 {description}\n\n"
    
    info_text += f"📊 **Ma'lumotlar:**\n"
    info_text += f"• 🎬 Kod: `{code}`\n"
    info_text += f"• 📋 Jami qismlar: {len(episodes)} ta\n"
    
    if release_year:
        info_text += f"• 🗓️ Yil: {release_year}\n"
    
    if genre:
        info_text += f"• 🎭 Janr: {genre}\n"
    
    if rating and rating > 0:
        info_text += f"• ⭐ Reyting: {rating}/10\n"
    
    info_text += f"\n🎬 **{len(episodes)} ta qism yuklanmoqda...**"
    
    if is_callback:
        await update.callback_query.edit_message_text(info_text)
        chat_id = user.id
    else:
        info_message = await update.message.reply_text(info_text)
        chat_id = update.message.chat_id
    
    # Отправляем все эпизоды подряд
    sent_count = 0
    for episode in episodes:
        episode_number, file_id, caption, duration, file_size, views = episode
        
        try:
            message_caption = caption or f"📺 {title}\n\nQism: {episode_number}"
            
            await context.bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=message_caption,
                protect_content=True
            )
            
            # Увеличиваем счетчик просмотров
            db.increment_views(dorama_code, episode_number)
            
            sent_count += 1
            await asyncio.sleep(1)  # Задержка между отправками
            
        except Exception as e:
            logger.error(f"Video yuborish xatosi (qism {episode_number}): {e}")
            continue
    
    # Отправляем сообщение о завершении
    completion_text = f"✅ **{title}**\n\n"
    completion_text += f"🎬 Barcha {sent_count} qism muvaffaqiyatli yuklandi!\n\n"
    completion_text += "Boshqa dorama qidirish uchun /start ni bosing"
    
    if is_callback:
        await context.bot.send_message(chat_id=chat_id, text=completion_text, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(completion_text, reply_markup=get_main_keyboard())

async def send_single_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, dorama_code, episode_number):
    """Отправляет один эпизод"""
    episode = db.get_episode(dorama_code, episode_number)
    
    if not episode:
        await update.callback_query.answer("❌ Qism topilmadi", show_alert=True)
        return
    
    ep_num, file_id, caption, duration, file_size, views, title, code = episode
    
    user = update.callback_query.from_user
    
    try:
        message_caption = caption or f"📺 {title}\n\nQism: {episode_number}"
        
        await context.bot.send_video(
            chat_id=user.id,
            video=file_id,
            caption=message_caption,
            protect_content=True
        )
        
        # Увеличиваем счетчик просмотров
        db.increment_views(dorama_code, episode_number)
        
        await update.callback_query.answer(f"✅ {episode_number}-qism yuklandi")
        
    except Exception as e:
        logger.error(f"Video yuborish xatosi: {e}")
        await update.callback_query.answer("❌ Video yuborishda xato", show_alert=True)

async def show_dorama_info(update: Update, context: ContextTypes.DEFAULT_TYPE, dorama_code):
    """Показывает информацию о дораме с выбором действия"""
    dorama = db.get_dorama(dorama_code)
    total_episodes = db.get_total_episodes(dorama_code)
    
    if not dorama:
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text("❌ Dorama topilmadi")
        else:
            await update.message.reply_text("❌ Dorama topilmadi")
        return
    
    code, title, description, release_year, genre, rating, poster = dorama
    
    text = f"📺 **{title}**\n\n"
    
    if description:
        text += f"📖 {description}\n\n"
    
    text += f"📊 **Ma'lumotlar:**\n"
    text += f"• 🎬 Kod: `{code}`\n"
    text += f"• 📋 Jami qismlar: {total_episodes} ta\n"
    
    if release_year:
        text += f"• 🗓️ Yil: {release_year}\n"
    
    if genre:
        text += f"• 🎭 Janr: {genre}\n"
    
    if rating and rating > 0:
        text += f"• ⭐ Reyting: {rating}/10\n"
    
    text += f"\n🎬 **Tanlang:**"
    
    keyboard = get_dorama_keyboard(dorama_code, total_episodes)
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)

async def show_all_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, dorama_code, page=0):
    """Показывает все эпизоды дорамы для выбора"""
    episodes = db.get_all_episodes(dorama_code)
    dorama = db.get_dorama(dorama_code)
    
    if not episodes or not dorama:
        await update.callback_query.edit_message_text("❌ Bu dorama uchun qismlar topilmadi")
        return
    
    title = dorama[1]
    total_episodes = len(episodes)
    
    text = f"📺 {title}\n\n"
    text += f"📋 Barcha qismlar ({total_episodes} ta):\n\n"
    text += "Kerakli qismni tanlang yoki barchasini yuborish tugmasini bosing:"
    
    keyboard = get_all_episodes_keyboard(dorama_code, page)
    await update.callback_query.edit_message_text(text, reply_markup=keyboard)

async def show_all_doramas(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    """Показывает все дорамы"""
    doramas = db.get_all_doramas()
    
    if not doramas:
        await update.message.reply_text("📚 Hozircha doramalar mavjud emas")
        return
    
    text = "📚 Barcha doramalar:\n\n"
    for i, (code, title, year, genre, rating, episode_count) in enumerate(doramas, 1):
        text += f"{i}. {title}"
        if year:
            text += f" ({year})"
        if episode_count:
            text += f" - {episode_count} qism"
        text += "\n"
    
    await update.message.reply_text(text, reply_markup=get_dorama_list_keyboard(doramas))

async def show_recent_doramas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает недавно добавленные дорамы"""
    doramas = db.get_all_doramas()
    
    if not doramas:
        await update.message.reply_text("🆕 Hozircha yangi doramalar yo'q")
        return
    
    # Берем последние 10 дорам
    recent_doramas = doramas[:10]
    
    text = "🆕 So'ngi qo'shilgan doramalar:\n\n"
    for i, (code, title, year, genre, rating, episode_count) in enumerate(recent_doramas, 1):
        text += f"{i}. {title}"
        if year:
            text += f" ({year})"
        if episode_count:
            text += f" - {episode_count} qism"
        text += "\n"
    
    await update.message.reply_text(text, reply_markup=get_dorama_list_keyboard(recent_doramas))

async def show_popular_doramas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает популярные дорамы"""
    stats = db.get_admin_stats()
    
    if not stats['popular_doramas']:
        await update.message.reply_text("📊 Hozircha mashhur doramalar yo'q")
        return
    
    text = "📊 Mashhur doramalar:\n\n"
    for i, (title, code, views) in enumerate(stats['popular_doramas'], 1):
        text += f"{i}. {title} - {views} ko'rish\n"
    
    await update.message.reply_text(text)

async def send_random_dorama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет случайную дораму"""
    doramas = db.get_all_doramas()
    
    if not doramas:
        if update.callback_query:
            await update.callback_query.message.reply_text("❌ Hozircha doramalar mavjud emas")
        else:
            await update.message.reply_text("❌ Hozircha doramalar mavjud emas")
        return
    
    import random
    random_dorama = random.choice(doramas)
    dorama_code = random_dorama[0]
    
    if update.callback_query:
        await send_all_episodes(update, context, dorama_code)
    else:
        await send_all_episodes(update, context, dorama_code)

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает помощь"""
    help_text = (
        "🤖 Koreys doramalari boti\n\n"
        "🎬 **Qanday ishlatish:**\n"
        "• 🔍 Qidirish - dorama nomi yoki kodini yozing\n"
        "• 📚 Barcha doramalar - ro'yxatni ko'ring\n"
        "• 🆕 Yangi qo'shilgan - so'ngi qo'shilganlar\n"
        "• 📊 Mashhur doramalar - eng ko'p ko'rilganlar\n\n"
        "📱 **Yangi imkoniyat:**\n"
        "• Endi barcha qismlar avtomatik ravishda ketma-ket yuboriladi!\n\n"
        "🍿 Tomosha qiling va zavqlaning!"
    )
    
    await update.message.reply_text(help_text)

# АДМИН ФУНКЦИИ
async def show_admin_stats(query):
    """Показывает статистику для админа"""
    stats = db.get_admin_stats()
    active_users = db.get_active_users_count()
    
    text = (
        f"📊 **Admin statistikasi:**\n\n"
        f"🎬 **Doramalar:** {stats['total_doramas']} ta\n"
        f"📺 **Qismlar:** {stats['total_episodes']} ta\n"
        f"👥 **Foydalanuvchilar:** {stats['total_users']} ta\n"
        f"📈 **Faol foydalanuvchilar (30 kun):** {active_users} ta\n"
        f"📈 **Kunlik aktiv:** {stats['daily_active']} ta\n"
        f"🆕 **Kutilayotgan so'rovlar:** {db.get_pending_requests_count()} ta\n\n"
        f"🔥 **Eng mashhur doramalar:**\n"
    )
    
    for i, (title, code, views) in enumerate(stats['popular_doramas'], 1):
        text += f"{i}. {title} - {views} ko'rish\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_admin_doramas(query, page=0, delete_mode=False):
    """Показывает список дорам в админ-панели"""
    limit = 10
    offset = page * limit
    
    doramas = db.get_all_doramas()
    total_count = len(doramas)
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    
    # Берем только нужную страницу
    page_doramas = doramas[offset:offset + limit]
    
    if not page_doramas:
        await query.edit_message_text(
            "📭 Hozircha doramalar mavjud emas",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="admin_menu")]])
        )
        return
    
    if delete_mode:
        text = f"🗑️ **Doramalarni o'chirish** (Sahifa {page+1}/{total_pages})\n\n"
        text += "Quyidagi doramalardan birini o'chirishingiz mumkin:\n\n"
    else:
        text = f"🎬 **Barcha doramalar** (Sahifa {page+1}/{total_pages})\n\n"
        text += f"Jami doramalar: {total_count} ta\n\n"
    
    for i, (code, title, year, genre, rating, episode_count) in enumerate(page_doramas, offset + 1):
        text += f"{i}. 🎬 {title}\n   🔗 Kod: {code}\n   📺 Qismlar: {episode_count} ta\n\n"
    
    await query.edit_message_text(text, reply_markup=get_admin_dorama_list_keyboard(page_doramas, page, total_pages, delete_mode))

async def show_delete_confirmation(query, dorama_code):
    """Показывает подтверждение удаления дорамы"""
    dorama = db.get_dorama(dorama_code)
    if not dorama:
        await query.answer("❌ Dorama topilmadi", show_alert=True)
        return
    
    code, title, description, release_year, genre, rating, poster = dorama
    total_episodes = db.get_total_episodes(dorama_code)
    
    text = (
        f"⚠️ **DORAMANI O'CHIRISH** ⚠️\n\n"
        f"🎬 **Dorama:** {title}\n"
        f"🔗 **Kod:** {code}\n"
        f"📺 **Qismlar:** {total_episodes} ta\n\n"
        f"❌ **Diqqat! Bu amalni ortga qaytarib bo'lmaydi!**\n"
        f"Dorama va barcha qismlari butunlay o'chib ketadi.\n\n"
        f"Rostan ham o'chirmoqchimisiz?"
    )
    
    await query.edit_message_text(text, reply_markup=get_admin_delete_confirmation_keyboard(dorama_code))

async def delete_dorama_confirmed(query, dorama_code):
    """Удаляет дораму после подтверждения"""
    success = db.delete_dorama(dorama_code)
    
    if success:
        await query.edit_message_text(
            f"✅ Dorama #{dorama_code} o'chirildi!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Doramalar ro'yxati", callback_data="admin_delete_0")]])
        )
    else:
        await query.edit_message_text(
            f"❌ Doramani o'chirishda xato!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Doramalar ro'yxati", callback_data="admin_delete_0")]])
        )

async def show_admin_dorama_info(query, dorama_code):
    """Показывает детальную информацию о дораме для админа"""
    dorama = db.get_dorama(dorama_code)
    if not dorama:
        await query.answer("❌ Dorama topilmadi", show_alert=True)
        return
    
    code, title, description, release_year, genre, rating, poster = dorama
    total_episodes = db.get_total_episodes(dorama_code)
    
    text = f"🎬 **Dorama ma'lumotlari**\n\n"
    text += f"📝 **Nomi:** {title}\n"
    text += f"🔗 **Kodi:** {code}\n"
    text += f"📺 **Qismlar:** {total_episodes} ta\n"
    
    if release_year:
        text += f"🗓️ **Yil:** {release_year}\n"
    
    if genre:
        text += f"🎭 **Janr:** {genre}\n"
    
    if rating and rating > 0:
        text += f"⭐ **Reyting:** {rating}/10\n"
    
    if description:
        text += f"\n📄 **Tavsif:**\n{description[:200]}..."
    
    keyboard = [
        [InlineKeyboardButton("🗑️ O'chirish", callback_data=f"admin_delete_confirm_{dorama_code}")],
        [InlineKeyboardButton("🔙 Doramalar ro'yxati", callback_data="admin_doramas_0")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_admin_channels(query):
    """Показывает каналы для админа"""
    channels = db.get_all_channels()
    
    text = "📢 **Kanallar ro'yxati:**\n\n"
    if channels:
        for channel_id, username, title, invite_link, is_private in channels:
            channel_type = "🔒 Maxfiy" if is_private else "📢 Ochiq"
            text += f"• {channel_type} {title or username or f'Kanal {channel_id}'}\n"
            if invite_link:
                text += f"  🔗 Link: {invite_link}\n"
            text += f"  🆔 ID: {channel_id}\n\n"
    else:
        text += "📭 Hozircha kanallar yo'q\n"
    
    text += "\n**Kanal qo'shish:** /addchannel <id> <@username> [nomi] [invite_link] [private]"
    text += "\n**Maxfiy kanal qo'shish:** /addprivatechannel <id> <invite_link> [nomi]"
    text += "\n**Kanal o'chirish:** /deletechannel <id>"
    
    keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_admin_requests(query, page=0):
    """Показывает список заявок для админа"""
    # В реальной реализации здесь будет запрос к базе данных
    # Для примера покажем заглушку
    text = "🆕 **Kutilayotgan so'rovlar:**\n\n"
    text += f"📊 Jami so'rovlar: {db.get_pending_requests_count()} ta\n\n"
    text += "Bu yerda foydalanuvchilarning maxfiy kanallarga so'rovlari ko'rsatiladi."
    
    # Заглушка для демонстрации
    requests = []  # В реальности: db.get_pending_requests()
    
    keyboard = get_admin_requests_keyboard(requests, page, 1)
    await query.edit_message_text(text, reply_markup=keyboard)

async def show_admin_settings(query):
    """Показывает настройки бота"""
    welcome_message = db.get_setting('welcome_message')
    help_message = db.get_setting('help_message')
    archive_channel = db.get_setting('archive_channel')
    
    text = (
        f"⚙️ **Bot sozlamalari:**\n\n"
        f"👋 **Xush kelish xabari:**\n{welcome_message[:100]}...\n\n"
        f"ℹ️ **Yordam xabari:**\n{help_message[:100]}...\n\n"
        f"📁 **Arxiv kanali:** {archive_channel or 'Oʻrnatilmagan'}\n\n"
        f"👨‍💻 **Adminlar soni:** {len(ADMIN_IDS)} ta\n\n"
        f"Quyidagi sozlamalarni o'zgartirishingiz mumkin:"
    )
    
    await query.edit_message_text(text, reply_markup=get_admin_settings_keyboard())

async def admin_set_welcome_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для изменения приветственного сообщения"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "👋 **Yangi xush kelish xabarini kiriting:**\n\n"
        "Bu xabar har bir foydalanuvchi /start ni bosganda ko'radi.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_settings")]
        ])
    )
    
    # Сохраняем состояние для обработки следующего сообщения
    context.user_data['awaiting_welcome_message'] = True

async def admin_set_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для изменения сообщения помощи"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "ℹ️ **Yangi yordam xabarini kiriting:**\n\n"
        "Bu xabar foydalanuvchi Yordam bo'limini tanlaganda ko'radi.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_settings")]
        ])
    )
    
    # Сохраняем состояние для обработки следующего сообщения
    context.user_data['awaiting_help_message'] = True

async def admin_set_archive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для изменения архива канала"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📁 **Yangi arxiv kanal ID sini kiriting:**\n\n"
        "Bu kanalga barcha yangi qo'shilgan videolar saqlanadi.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_settings")]
        ])
    )
    
    # Сохраняем состояние для обработки следующего сообщения
    context.user_data['awaiting_archive_channel'] = True

async def admin_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для рассылки сообщений"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📢 **Xabar yuborish (Broadcast)**\n\n"
        "Barcha foydalanuvchilarga xabar yuborish uchun:\n\n"
        "1. Xabaringizni yuboring (text, rasm, video)\n"
        "2. Shu xabarga javoban /broadcast buyrug'ini yozing\n\n"
        "Yoki shunchaki /broadcast buyrug'iga javoban xabar yuboring.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_menu")]
        ])
    )

# ОБРАБОТЧИК СООБЩЕНИЙ ДЛЯ НАСТРОЕК
async def handle_settings_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает сообщения для настроек"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    text = update.message.text
    
    if context.user_data.get('awaiting_welcome_message'):
        # Сохраняем новое приветственное сообщение
        db.update_setting('welcome_message', text)
        await update.message.reply_text(
            "✅ Xush kelish xabari muvaffaqiyatli o'zgartirildi!",
            reply_markup=get_admin_keyboard()
        )
        context.user_data.pop('awaiting_welcome_message', None)
        
    elif context.user_data.get('awaiting_help_message'):
        # Сохраняем новое сообщение помощи
        db.update_setting('help_message', text)
        await update.message.reply_text(
            "✅ Yordam xabari muvaffaqiyatli o'zgartirildi!",
            reply_markup=get_admin_keyboard()
        )
        context.user_data.pop('awaiting_help_message', None)
        
    elif context.user_data.get('awaiting_archive_channel'):
        # Сохраняем новый ID архива канала
        db.update_setting('archive_channel', text)
        await update.message.reply_text(
            "✅ Arxiv kanali muvaffaqiyatli o'zgartirildi!",
            reply_markup=get_admin_keyboard()
        )
        context.user_data.pop('awaiting_archive_channel', None)

# ПРОСТАЯ ФУНКЦИЯ РАССЫЛКИ
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для рассылки сообщения всем пользователям"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Bu komanda faqat adminlar uchun!")
        return
    
    # Проверяем, является ли сообщение ответом на другое сообщение
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "📢 **Xabar yuborish (Broadcast)**\n\n"
            "Barcha foydalanuvchilarga xabar yuborish uchun:\n\n"
            "1. Xabaringizni yuboring (text, rasm, video)\n"
            "2. Shu xabarga javoban /broadcast buyrug'ini yozing\n\n"
            "Yoki shunchaki /broadcast buyrug'iga javoban xabar yuboring."
        )
        return
    
    # Получаем сообщение для рассылки
    message_to_forward = update.message.reply_to_message
    users = db.get_all_users()
    total_users = len(users)
    
    if total_users == 0:
        await update.message.reply_text("❌ Hozircha foydalanuvchilar mavjud emas")
        return
    
    # Начинаем рассылку сразу без подтверждения
    progress_message = await update.message.reply_text(
        f"📤 Xabar yuborilmoqda...\n\n"
        f"📊 Progress: 0/{total_users}\n"
        f"✅ Muvaffaqiyatli: 0\n"
        f"❌ Xatolar: 0"
    )
    
    successful = 0
    failed = 0
    
    for i, (user_id, username, first_name, last_name) in enumerate(users, 1):
        try:
            # Пересылаем сообщение
            await message_to_forward.forward(chat_id=user_id)
            successful += 1
            
            # Обновляем прогресс каждые 10 сообщений
            if i % 10 == 0 or i == total_users:
                await context.bot.edit_message_text(
                    chat_id=update.message.chat_id,
                    message_id=progress_message.message_id,
                    text=f"📤 Xabar yuborilmoqda...\n\n"
                         f"📊 Progress: {i}/{total_users}\n"
                         f"✅ Muvaffaqiyatli: {successful}\n"
                         f"❌ Xatolar: {failed}"
                )
            
            # Задержка чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.1)
            
        except Exception as e:
            failed += 1
            logger.error(f"Xabar yuborishda xato {user_id}: {e}")
    
    # Финальный результат
    result_text = (
        f"✅ **Xabar yuborish yakunlandi!**\n\n"
        f"📊 **Natijalar:**\n"
        f"• 👥 Jami: {total_users} ta\n"
        f"• ✅ Muvaffaqiyatli: {successful} ta\n"
        f"• ❌ Xatolar: {failed} ta\n"
        f"• 📈 Muvaffaqiyat darajasi: {(successful/total_users)*100:.1f}%"
    )
    
    await context.bot.edit_message_text(
        chat_id=update.message.chat_id,
        message_id=progress_message.message_id,
        text=result_text
    )

async def handle_broadcast_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает подтверждение рассылки (теперь не используется)"""
    # Эта функция больше не нужна, но оставлю для совместимости
    pass

# ОБРАБОТЧИК ВИДЕО ДЛЯ АДМИНОВ
async def handle_admin_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик видео для админов - добавление серий"""
    if not update.message or not update.effective_user:
        return
    
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    message = update.message
    caption = message.caption or ""
    
    # Ищем код дорамы и номер серии в хештегах
    dorama_code_match = re.search(r'#(\w+)', caption)
    episode_match = re.search(r'#seria[_:]?(\d+)', caption, re.IGNORECASE)
    
    if not dorama_code_match:
        await message.reply_text("❌ Izohda #KOD formatida dorama kodini ko'rsating")
        return
    
    if not episode_match:
        await message.reply_text("❌ Izohda #seria_1 formatida seriya raqamini ko'rsating")
        return
    
    dorama_code = dorama_code_match.group(1)
    episode_number = int(episode_match.group(1))
    
    file_id = None
    duration = 0
    file_size = 0
    
    if message.video:
        file_id = message.video.file_id
        duration = message.video.duration or 0
        file_size = message.video.file_size or 0
    
    if not file_id:
        await message.reply_text("❌ Xabar video faylni o'z ichiga olmaydi")
        return
    
    try:
        # Проверяем, существует ли дорама
        dorama = db.get_dorama(dorama_code)
        if not dorama:
            # Создаем новую дораму с названием из caption
            title_match = re.search(r'#nomi[_:]?([^#\n]+)', caption, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else f"Dorama {dorama_code}"
            
            db.add_dorama(dorama_code, title)
            logger.info(f"✅ Yangi dorama yaratildi: {title} ({dorama_code})")
        
        # Добавляем эпизод
        if db.add_episode(dorama_code, episode_number, file_id, caption, duration, file_size):
            total_episodes = db.get_total_episodes(dorama_code)
            await message.reply_text(
                f"✅ #{dorama_code} doramasiga {episode_number}-qism qo'shildi!\n\n"
                f"📊 Jami qismlar: {total_episodes} ta\n\n"
                f"Endi foydalanuvchilar ushbu qismni tomosha qilishlari mumkin."
            )
        else:
            await message.reply_text("❌ Bazaga qo'shishda xato")
            
    except Exception as e:
        await message.reply_text(f"❌ Xato: {e}")

# КОМАНДЫ ДЛЯ АДМИНОВ
async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет канал в базу данных"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    if context.args and len(context.args) >= 2:
        try:
            channel_id = int(context.args[0])
            username = context.args[1]
            title = context.args[2] if len(context.args) > 2 else None
            invite_link = context.args[3] if len(context.args) > 3 else None
            is_private = context.args[4].lower() == 'true' if len(context.args) > 4 else False
            
            success = db.add_channel(channel_id, username, title, invite_link, is_private)
            
            if success:
                await update.message.reply_text(f"✅ Kanal {username} qo'shildi!")
            else:
                await update.message.reply_text("❌ Kanal qo'shishda xato")
        except ValueError:
            await update.message.reply_text("❌ Kanal ID raqam bo'lishi kerak")
    else:
        await update.message.reply_text(
            "❌ Foydalanish: /addchannel <id> <@username> [nomi] [invite_link] [private]"
        )

async def add_private_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет приватный канал"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    if context.args and len(context.args) >= 2:
        try:
            channel_id = int(context.args[0])
            invite_link = context.args[1]
            title = context.args[2] if len(context.args) > 2 else f"Maxfiy kanal {channel_id}"
            
            success = db.add_channel(channel_id, "", title, invite_link, True)
            
            if success:
                await update.message.reply_text(f"✅ Maxfiy kanal {title} qo'shildi!")
            else:
                await update.message.reply_text("❌ Kanal qo'shishda xato")
        except ValueError:
            await update.message.reply_text("❌ Kanal ID raqam bo'lishi kerak")
    else:
        await update.message.reply_text(
            "❌ Foydalanish: /addprivatechannel <id> <invite_link> [nomi]"
        )

async def delete_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет канал из базы данных"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    if context.args:
        try:
            channel_id = int(context.args[0])
            success = db.delete_channel(channel_id)
            
            if success:
                await update.message.reply_text("✅ Kanal o'chirildi!")
            else:
                await update.message.reply_text("❌ Kanalni o'chirishda xato")
        except ValueError:
            await update.message.reply_text("❌ Kanal ID raqam bo'lishi kerak")
    else:
        await update.message.reply_text("❌ Kanal ID sini ko'rsating: /deletechannel <id>")

async def delete_dorama_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для удаления дорамы по коду"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    if context.args:
        dorama_code = context.args[0]
        success = db.delete_dorama(dorama_code)
        
        if success:
            await update.message.reply_text(f"✅ Dorama #{dorama_code} o'chirildi!")
        else:
            await update.message.reply_text(f"❌ Doramani o'chirishda xato!")
    else:
        await update.message.reply_text(
            "❌ Foydalanish: /deletedorama <kod>"
        )

# ОБРАБОТЧИК CALLBACK
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    db.update_user_activity(user.id)
    
    if user.id not in ADMIN_IDS:
        if not await require_subscription(update, context):
            return
    
    # Основные обработчики
    if data == "main_menu":
        if user.id in ADMIN_IDS:
            await query.edit_message_text("👨‍💻 Admin paneli:", reply_markup=get_admin_keyboard())
        else:
            await query.edit_message_text("Bosh menyu:", reply_markup=get_main_menu_keyboard())
    
    elif data == "search":
        await query.edit_message_text(
            "🔍 Dorama nomi yoki kodini kiriting:\n\n"
            "Misol: <code>Yulduzlar</code> yoki <code>YL2024</code>",
            parse_mode="HTML"
        )
    
    elif data.startswith("all_doramas_"):
        page = int(data.split("_")[2])
        await show_all_doramas(update, context, page)
    
    elif data.startswith("recent_doramas_"):
        page = int(data.split("_")[2])
        await show_recent_doramas(update, context)
    
    elif data.startswith("popular_doramas_"):
        page = int(data.split("_")[2])
        await show_popular_doramas(update, context)
    
    elif data == "random_dorama":
        await send_random_dorama(update, context)
    
    elif data == "help":
        await show_help(update, context)
    
    elif data.startswith("dorama_"):
        dorama_code = data.split("_")[1]
        await show_dorama_info(update, context, dorama_code)
    
    elif data.startswith("send_all_"):
        dorama_code = data.split("_")[2]
        await send_all_episodes(update, context, dorama_code)
    
    elif data.startswith("watch_"):
        parts = data.split("_")
        dorama_code = parts[1]
        episode_number = int(parts[2])
        await send_single_episode(update, context, dorama_code, episode_number)
    
    elif data.startswith("all_episodes_"):
        dorama_code = data.split("_")[2]
        await show_all_episodes(update, context, dorama_code)
    
    elif data.startswith("episodes_"):
        parts = data.split("_")
        dorama_code = parts[1]
        page = int(parts[2])
        await show_all_episodes(update, context, dorama_code, page)
    
    elif data == "check_subscription":
        await check_subscription_callback(update, context)

    # АДМИН ОБРАБОТЧИКИ
    elif data == "admin_menu":
        await query.edit_message_text("👨‍💻 Admin paneli:", reply_markup=get_admin_keyboard())
    elif data == "admin_stats":
        await show_admin_stats(query)
    elif data.startswith("admin_doramas_"):
        page = int(data.split("_")[2])
        await show_admin_doramas(query, page)
    elif data.startswith("admin_delete_"):
        if data == "admin_delete_0":
            await show_admin_doramas(query, 0, delete_mode=True)
        elif data.startswith("admin_delete_confirm_"):
            dorama_code = data.split("_")[3]
            await show_delete_confirmation(query, dorama_code)
        elif data.startswith("admin_delete_"):
            page = int(data.split("_")[2])
            await show_admin_doramas(query, page, delete_mode=True)
    elif data.startswith("admin_confirm_delete_"):
        dorama_code = data.split("_")[3]
        await delete_dorama_confirmed(query, dorama_code)
    elif data.startswith("admin_dorama_info_"):
        dorama_code = data.split("_")[3]
        await show_admin_dorama_info(query, dorama_code)
    elif data == "admin_channels":
        await show_admin_channels(query)
    elif data.startswith("admin_requests_"):
        page = int(data.split("_")[2])
        await show_admin_requests(query, page)
    elif data == "admin_settings":
        await show_admin_settings(query)
    elif data == "admin_broadcast":
        await admin_broadcast_callback(update, context)
    elif data == "admin_set_welcome":
        await admin_set_welcome_callback(update, context)
    elif data == "admin_set_help":
        await admin_set_help_callback(update, context)
    elif data == "admin_set_archive":
        await admin_set_archive_callback(update, context)
    
    elif data == "current_page":
        await query.answer()

def main():
    """Главная функция"""
    try:
        # Проверяем переменные окружения
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN not set in environment variables")
            return
        
        logger.info("🚀 Starting Korean Doramas Bot...")
        logger.info(f"👑 Admin IDs: {ADMIN_IDS}")
        
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Обработчики команд
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("broadcast", broadcast_command))
        application.add_handler(CommandHandler("addchannel", add_channel_command))
        application.add_handler(CommandHandler("addprivatechannel", add_private_channel_command))
        application.add_handler(CommandHandler("deletechannel", delete_channel_command))
        application.add_handler(CommandHandler("deletedorama", delete_dorama_command))
        
        # Обработчики для заявок
        application.add_handler(ChatJoinRequestHandler(handle_chat_join_request))
        application.add_handler(ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
        
        # Обработчики сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(MessageHandler(
            filters.VIDEO & filters.CAPTION,
            handle_admin_video
        ))
        
        # Обработчик сообщений для настроек
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_settings_message
        ))
        
        # Обработчики callback-кнопок
        application.add_handler(CallbackQueryHandler(handle_callback, pattern="^.*$"))
        
        logger.info("🎬 Koreys doramalari boti ishga tushdi!")
        logger.info("✅ Bot successfully configured and ready")
        
        # Запуск бота
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        
    except Exception as e:
        logger.error(f"❌ Xato: {e}")
    except KeyboardInterrupt:
        logger.info("\n📴 Bot to'xtatildi")

if __name__ == "__main__":
    main()
