import asyncio
import os
import os 
from dotenv import load_dotenv
import re
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
)

load_dotenv()

# Новый путь к файлу базы данных
db_path = os.path.abspath('asalo_steam.db.sqlite')

# Константы для состояний диалога
START, CHOICE, SUM, USERNAME, CONFIRM_USERNAME, CHANGE_USERNAME, CHECK, CONFIRMATION, REVIEW = range(9)

#называем фотки исполняемым для всех систем именем

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
photo_path = os.path.join(BASE_DIR, "instruction2.jpg")



# ID администратора
ADMIN_CHAT_ID = 8045426640

# Группа для уведомлений (укажите реальный чат-идентификатор вашей группы)
GROUP_CHAT_ID = '-5064645336'

# Создание новых таблиц в новой базе данных
with sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES) as conn:
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS new_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS new_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL NOT NULL,
        total REAL NOT NULL,
        fee REAL NOT NULL,
        is_confirmed BOOLEAN DEFAULT FALSE,
        is_completed BOOLEAN DEFAULT FALSE,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES new_users(id)
    );
    ''')
    conn.commit()

# Функция для автоматического добавления столбца, если он отсутствует
def ensure_column_exists(conn, table_name, column_name, column_type):
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    existing_columns = [column[1] for column in columns]
    if column_name not in existing_columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type};")
        conn.commit()

# Первоначальная миграция базы данных
with sqlite3.connect(db_path) as conn:
    ensure_column_exists(conn, 'new_transactions', 'is_completed', 'BOOLEAN DEFAULT FALSE')

async def insert_or_update_user(user_id, username):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM new_users WHERE telegram_id=?", (user_id,))
        result = cursor.fetchone()
        if result:
            user_id_in_db = result[0]
            cursor.execute("UPDATE new_users SET username=? WHERE id=?", (username, user_id_in_db))
        else:
            cursor.execute("INSERT INTO new_users (telegram_id, username) VALUES (?, ?)", (user_id, username))
            user_id_in_db = cursor.lastrowid
        conn.commit()
        return user_id_in_db

async def create_transaction(user_id, amount, total, fee):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO new_transactions (user_id, amount, total, fee) VALUES (?, ?, ?, ?)",
                       (user_id, amount, total, fee))
        conn.commit()
        return cursor.lastrowid  # Возвращаем идентификатор вставленной записи

async def mark_transaction_as_confirmed(transaction_id):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE new_transactions SET is_confirmed=TRUE WHERE id=?", (transaction_id,))
        conn.commit()

async def complete_transaction(user_id):
    # Завершение транзакции.
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, amount, total FROM new_transactions WHERE user_id=? AND is_confirmed=TRUE AND is_completed=FALSE ORDER BY timestamp DESC LIMIT 1", (user_id,))
        row = cursor.fetchone()
        if row:
            transaction_id, amount, total = row
            cursor.execute("UPDATE new_transactions SET is_completed=TRUE WHERE id=?", (transaction_id,))
            conn.commit()
            return transaction_id, amount, total
        else:
            return None, None, None

async def interrupt_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    if 'current_transaction' in user_data:
        del user_data['current_transaction']
        await update.message.reply_text("Текущая сделка прервана. Оставьте отзыв о нашем сервисе:",
                                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Оставить отзыв", callback_data="leave_review")]]))
        return REVIEW
    else:
        await update.message.reply_text("Оставьте отзыв о нашем сервисе:",
                                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Оставить отзыв", callback_data="leave_review")]]))
        return REVIEW

async def leave_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Напишите ваш отзыв о нашем сервисе:")
    return REVIEW

async def save_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username or update.message.from_user.first_name
    review_text = update.message.text
    
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"Отзыв от пользователя {username} (ID: {user_id}):\n{review_text}"
    )
    
    await update.message.reply_text("Спасибо за ваш отзыв! Мы ценим ваше мнение.\nВаш отзыв будет опубликован у нас в канале: @asalo_steam_rep")
    return ConversationHandler.END

# Создаем review_handler
review_handler = ConversationHandler(
    entry_points=[CommandHandler('review', interrupt_transaction), CallbackQueryHandler(leave_review, pattern="^leave_review$")],
    states={
        REVIEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_review)]
    },
    fallbacks=[]
)

# Функция для полной очистки базы данных путем удаления таблиц
async def clear_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Только администратор может выполнить эту команду.")
        return

    confirmation_message = "Внимание! Эта операция безвозвратно удалит ВСЕ данные из базы данных. Вы уверены?"
    buttons = [[InlineKeyboardButton("Да, удалить", callback_data="delete_tables")], 
               [InlineKeyboardButton("Нет, отменить", callback_data="cancel_delete")]]
    reply_markup = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(confirmation_message, reply_markup=reply_markup)

    return CONFIRMATION

async def delete_tables_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "delete_tables":
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DROP TABLE IF EXISTS new_users;")
                cursor.execute("DROP TABLE IF EXISTS new_transactions;")
                conn.commit()
            await query.edit_message_text("Все таблицы успешно очищены.")
        except Exception as e:
            await query.edit_message_text(f"Ошибка при удалении таблиц: {e}")
    elif query.data == "cancel_delete":
        await query.edit_message_text("Операция отмены выполнена.")

    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sticker_id = 'CAACAgIAAxkBAAIJDWlr6LbOwMR7PPH97ts2_SPesok8AAI-ngACGgVYS7WPQHSMTrGDOAQ'
    await update.message.reply_sticker(sticker_id)

    # Сохраняем user_id покупателя в контексте диалога
    context.user_data['buyer_user_id'] = update.message.from_user.id

    # Клавиатура с выбором действий
    keyboard = [
        [InlineKeyboardButton("Да, хочу пополнить баланс 💰", callback_data="yes")],
        [InlineKeyboardButton("Нет, пока не хочу пополнять 😌", callback_data="no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Здравствуйте, вас приветствует Asalo Steam!\nХотите начать пополнение своего стим-аккаунта?",
        reply_markup=reply_markup
    )

    return START

async def process_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "yes":  # Пользователь согласился пополнить баланс
        await query.edit_message_text("Введите сумму, на которую хотите пополнить ваш стим-аккаунт:")
        return SUM
    else:  # Пользователь отказался
        await query.edit_message_text("Хорошо, обращайтесь, когда захотите пополнить баланс. До встречи!")
        return ConversationHandler.END

async def get_sum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message_text = update.message.text
 
    try:
        sum_value = float(message_text)
        sum_value = round(sum_value)
        if sum_value < 100: 
            await update.message.reply_text("Нельзя вводить сумму меньше 100.\nПожалуйста, введите сумму от 100 и выше:")
            return SUM
        if sum_value <= 0:
            raise ValueError
        context.user_data['sum'] = sum_value
        
        # Расчёт комиссии и сохранение в контексте
        total_with_fee = round(sum_value * 1.10)
        commission = total_with_fee - sum_value
        context.user_data['commission'] = commission
        context.user_data['total'] = total_with_fee

        await update.message.reply_text("Теперь введите ваш юзернейм стим-аккаунта:")
        return USERNAME
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите корректную положительную числовую сумму.")
        return SUM
    

async def get_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.text.strip()
    pattern = r'^[A-Za-z0-9_\-]+$'
    if not re.match(pattern, username):
        await update.message.reply_text("Юзернейм должен быть на английском языке,\nПожалуйста, введите ваш юзернейм ещё раз:")
        return USERNAME
    context.user_data['steam_username'] = username  # Сохраняем никнейм Steam-аккаунта

    # Добавляем пользователя в новую базу данных
    user_id_in_db = await insert_or_update_user(user_id, username)
    context.user_data['user_id_in_db'] = user_id_in_db

    sum_value = context.user_data['sum']
    total_with_fee = context.user_data['total']
    commission = context.user_data['commission']
    transaction_id = await create_transaction(user_id_in_db, sum_value, total_with_fee, commission)
    context.user_data['transaction_id'] = transaction_id  # Сохраняем идентификатор транзакции

    # Проверяем, была ли уже отправлена инструкция
    if 'instruction_sent' not in context.user_data:
        # Отправляем инструкцию пользователю
        try:
            await update.message.reply_photo(
                photo=open(photo_path, 'rb'),
                caption=(
                    'Это инструкция, где найти юзернейм:\n'
                    '1. Сначала откройте своё приложение Steam на компьютере.\n'
                    '2. В правом верхнем углу нажмите на свой ник.\n'
                    '3. В поле "Об аккаунте" вы увидите свой юзернейм.\n'
                    'В случае, если вам нужна инструкция для телефона, напишите команду: /phone'
                )
            )
            context.user_data['instruction_sent'] = True
        except Exception as e:
            await update.message.reply_text("Не удалось отправить инструкцию. Пожалуйста, свяжитесь с поддержкой.")
            return ConversationHandler.END

    # Спрашиваем пользователя, хотят ли они изменить юзернейм
    await update.message.reply_text(
        f"Ваш юзернейм: {context.user_data['steam_username']}.\n"
        "Если всё верно, введите команду /continue для продолжения покупки.\n"
        "Если хотите изменить юзернейм, введите команду /change."
    )
    return CONFIRM_USERNAME

async def continue_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_id_in_db = context.user_data['user_id_in_db']

    sum_value = context.user_data['sum']
    total_with_fee = context.user_data['total']
    commission = context.user_data['commission']

    payment_instruction = (
        f"Пожалуйста, переведите {total_with_fee} рублей через ваше банковское приложение по реквизитам:\n"
        "2200702020451295  Т-Банк"
    )

    await update.message.reply_text(
        f"{payment_instruction}\nПосле оплаты отправьте фотографию чека перевода.\n"
        "Если хотите отменить операцию, напишите /cancel."
    )
    return CHECK

async def change_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите новый юзернейм:")
    return USERNAME

async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_id_in_db = context.user_data.get('user_id_in_db')
    total_with_fee = context.user_data.get('total')
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправьте фотографию чеков.")
        return CHECK

    photo = update.message.photo[-1]
    file = await photo.get_file()

    # Получаем последнюю транзакцию пользователя
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, amount, total 
            FROM new_transactions 
            WHERE user_id=? AND is_confirmed=FALSE 
            ORDER BY timestamp DESC 
            LIMIT 1""", (user_id_in_db,)
        )
        transaction = cursor.fetchone()
    
    if not transaction:
        await update.message.reply_text("Не найдена соответствующая транзакция.")
        return CHECK

    transaction_id, amount, total = transaction

    # Сохраняем текущий ID транзакции
    context.user_data['current_transaction_id'] = transaction_id

    # Получаем никнейм Telegram-аккаунта пользователя
    telegram_username = update.effective_user.username or update.effective_user.first_name

    # Отправляем чек менеджеру вместе с фотографией
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"Чек оплаты от @{telegram_username} (Telegram ID: {user_id}), Transaction ID: {transaction_id}\n"
            f"Для подтверждения введите /confirm, айди пользователя и айди транзакции",
        reply_to_message_id=None
    )

    # Присылаем само фото чека
    await context.bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=file.file_id
    )

    # Сообщаем пользователю, что ждём подтверждения
    await update.message.reply_text("Отправил чек менеджеру. Ожидайте подтверждения.")
    return ConversationHandler.END

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1 or not all(arg.isdigit() for arg in args[:1]):
        await update.message.reply_text("Формат команды: /confirm <user_id> [<transaction_id>]")
        return

    user_id = int(args[0])
    transaction_id = None
    if len(args) > 1:
        transaction_id = int(args[1])

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        if transaction_id:
            cursor.execute("SELECT id, amount, total FROM new_transactions WHERE id=? AND is_confirmed=FALSE", (transaction_id,))
        else:
            cursor.execute("""
                SELECT id, amount, total 
                FROM new_transactions 
                WHERE user_id=? AND is_confirmed=FALSE 
                ORDER BY timestamp DESC 
                LIMIT 1""", (user_id,)
            )

        transaction = cursor.fetchone()
        if not transaction:
            await update.message.reply_text("Нет неподтвержденных платежей для этого пользователя.")
            return

        transaction_id, amount, total = transaction

        # Обновляем статус транзакции в базе данных
        await mark_transaction_as_confirmed(transaction_id)

        # Получаем никнейм Steam-аккаунта пользователя из таблицы пользователей
        cursor.execute("SELECT username FROM new_users WHERE id=(SELECT user_id FROM new_transactions WHERE id=?)", (transaction_id,))
        result = cursor.fetchone()
        if result:
            steam_username = result[0]
        else:
            steam_username = "Неизвестный Steam-аккаунт"

        # Получаем никнейм Telegram-аккаунта пользователя (если сохранён в БД)
        cursor.execute("SELECT telegram_id FROM new_users WHERE id=(SELECT user_id FROM new_transactions WHERE id=?)", (transaction_id,))
        result = cursor.fetchone()
        if result:
            telegram_id = result[0]
            # Используем API Telegram для получения username или first_name пользователя
            try:
                telegram_user = await context.bot.get_chat_member(telegram_id, telegram_id)
                telegram_username = telegram_user.user.username or telegram_user.user.first_name
            except Exception as e:
                telegram_username = "Неизвестный пользователь"
        else:
            telegram_username = "Неизвестный пользователь"

        # Формируем подробное сообщение для администратора
        admin_message = (
            f"<b>Оплата подтверждена!</b>\n"
            f"\nЮзер Telegram: {telegram_username} ({user_id})\n"
            f"Steam-аккаунт: {steam_username}\n"
            f"Пополнение: {amount:.2f} ₽\n"
            f"Айди транзакции: №{transaction_id}\n"
            f"Ссылка на пополнение: https://igm.gg/steam/"
        )

        # Отправляем сообщение администратору
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_message, parse_mode='HTML')

        # Уведомляем пользователя о подтверждении
        await context.bot.send_message(
            chat_id=user_id,
            text="Платёж успешно подтверждён, в течение 5 минут ваш аккаунт будет пополнен!\n"
                "После того как вы получите деньги на баланс, введите команду /complete для завершения транзакции."
        )

async def complete_transaction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Завершение транзакции пользователем после проверки поступления средств.
    user_id = update.message.from_user.id

    # Извлекаем транзакционный идентификатор из контекста
    transaction_id = context.user_data.get('transaction_id')
    if not transaction_id:
        await update.message.reply_text("Не найден идентификатор транзакции.")
        return

    # Берём комиссию из контекста
    commission = context.user_data.get('commission')

    # Сообщаем пользователю о завершении транзакции
    await update.message.reply_text(
        "Вы подтвердили, что деньги зачислены на ваш аккаунт, "
        "транзакция завершена, "
        "Спасибо за доверие нашему сервису!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Оставить отзыв", callback_data="leave_review")]])
    )

    # Получаем сумму пополнения из контекста
    amount = context.user_data.get('sum')
    
    # Используем API Telegram для получения username или first_name пользователя
    try:
        telegram_user = await context.bot.get_chat_member(user_id, user_id)
        telegram_username = telegram_user.user.username or telegram_user.user.first_name
    except Exception as e:
        telegram_username = "Неизвестный пользователь"

    # Отправляем уведомление группе
    group_notification = (
        f"Транзакция завершена!\n"
        f"🔗 Айди транзакции: #{transaction_id}\n"
        f"💳 Сумма пополнения: {amount:.2f} ₽\n"
        f"💵 Заработали: {commission:.2f} ₽\n"
        f"👤 Telegram-пользователь: {telegram_username}\n"
        f"⚡️ Статус: Завершена"
    )
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_notification)

    # Отправляем уведомление администратору
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"Пользователь {user_id} подтвердил получение денег на баланс и завершил транзакцию."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

async def phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = [
        os.path.join(BASE_DIR, "instruction3.jpg"),
        os.path.join(BASE_DIR, "instruction4.jpg"),
        os.path.join(BASE_DIR, "instruction5.jpg"),
    ]

    for photo in photos:
        try:
            await context.bot.send_photo(
                chat_id=update.message.chat_id,
                photo=open(photo, 'rb')
            )
        except Exception as e:
            print(f"Ошибка при отправке {photo}: {e}")

    await update.message.reply_text("Инструкция для телефона")


# Основная логика запуска приложения

def main():
    TOKEN = os.getenv("TG_BOT_TOKEN")
    app = Application.builder().token(TOKEN).build()

    # Зарегистрируем команду очистки базы данных
    app.add_handler(CommandHandler('clear_db', clear_database))

    # Команды подтверждения оплаты
    app.add_handler(CommandHandler('confirm', confirm_payment))
    
    # Команда завершения транзакции пользователем
    app.add_handler(CommandHandler('complete', complete_transaction_command))

    # Обработчик отзыва
    review_handler = ConversationHandler(
        entry_points=[CommandHandler('review', interrupt_transaction), CallbackQueryHandler(leave_review, pattern="^leave_review$")],
        states={
            REVIEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_review)]
        },
        fallbacks=[]
    )
    app.add_handler(review_handler)

    # Команда для инструкции для телефона
    app.add_handler(CommandHandler('phone', phone))

    # Хэндлер для очистки базы данных
    app.add_handler(CallbackQueryHandler(delete_tables_callback, pattern=r'delete_tables|cancel_delete'))

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
        ],
        states={
            START: [CallbackQueryHandler(process_choice)],
            SUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sum)],
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_username)],
            CONFIRM_USERNAME: [
                CommandHandler('continue', continue_purchase),
                CommandHandler('change', change_username)
            ],
            CHECK: [MessageHandler(filters.PHOTO, handle_check)],
            CONFIRMATION: [CommandHandler('cancel', cancel)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler)

    print("Бот запущен")
    app.run_polling()

if __name__ == '__main__':
    main()