from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from flask import Flask, jsonify
import logging
from datetime import datetime, timedelta
import os
import threading
import time
import requests
from threading import Lock

# 从环境变量获取 Token 和用户 ID
TOKEN = os.getenv("TOKEN")
MY_USER_ID = int(os.getenv("MY_USER_ID"))

if not TOKEN or not MY_USER_ID:
    raise ValueError("请设置环境变量 TOKEN 和 MY_USER_ID")

# 配置日志记录（带轮换功能）
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=3)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[handler],
)
logger = logging.getLogger(__name__)

# 验证选项键盘
def get_verification_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("是", callback_data="tech_yes"),
            InlineKeyboardButton("否", callback_data="tech_no"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# 检查是否需要验证
def needs_verification(context):
    last_verified = context.user_data.get("last_verified_date")
    today_date = datetime.now().date()

    if not last_verified or last_verified != today_date:
        return True
    return False

# 发送验证消息
async def send_verification_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_verification", False):
        context.user_data["awaiting_verification"] = True
        await update.message.reply_text(
            "在线验证：你要问的是否为技术问题？\n每24小时仅有一次选择机会。",
            reply_markup=get_verification_keyboard(),
        )

# 消息映射存储（带线程锁）
message_mapping = {}
mapping_lock = Lock()

# 消息处理函数
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if needs_verification(context):
        await send_verification_message(update, context)
        logger.info(
            f"Message from {update.effective_user.first_name} ({update.effective_chat.id}) discarded as user is not verified."
        )
        return

    # 转发消息并记录映射关系
    await process_message(update, context)

# 处理用户点击的验证选项
async def handle_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "tech_yes":
        await query.edit_message_text(
            "免费项目不解答问题，自行搜索解决。\n您今日的验证次数已用完，24小时后可重新发起对话。\n下次验证之前的消息将被自动丢弃"
        )
        context.user_data["tech_rejected_until"] = datetime.now() + timedelta(hours=24)

    elif query.data == "tech_no":
        await query.edit_message_text("您已通过验证，请重新发送你的业务需求，我将转发给管理员。")

    context.user_data["awaiting_verification"] = False
    context.user_data["last_verified_date"] = datetime.now().date()

# 处理已验证的消息
async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    original_user_id = update.effective_chat.id

    # 转发消息并存储映射
    forwarded_message = await context.bot.forward_message(
        chat_id=MY_USER_ID,
        from_chat_id=original_user_id,
        message_id=update.message.message_id,
    )

    with mapping_lock:
        message_mapping[forwarded_message.message_id] = original_user_id

    logger.info(
        f"Message from {update.effective_user.first_name} ({original_user_id}) forwarded to user {MY_USER_ID}."
    )

# 回复处理函数
async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == MY_USER_ID:
        forwarded_message = update.message.reply_to_message
        if forwarded_message:
            with mapping_lock:
                original_user_id = message_mapping.get(forwarded_message.message_id)
            if original_user_id:
                await context.bot.send_message(
                    chat_id=original_user_id, text=update.message.text
                )
                logger.info(
                    f"Reply from {update.effective_user.first_name} sent to original user {original_user_id}."
                )
            else:
                logger.warning(
                    f"No mapping found for the forwarded message (ID: {forwarded_message.message_id})."
                )

# /start 命令处理
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    last_start = context.user_data.get("last_start_date")
    if last_start and datetime.now() - last_start < timedelta(hours=24):
        logger.info(
            f"/start command from {update.effective_user.first_name} ({update.effective_chat.id}) discarded (already responded within 24 hours)."
        )
        return

    context.user_data["last_start_date"] = datetime.now()
    await send_verification_message(update, context)

# Flask Web 应用
app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"status": "Bot is running", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

def run_flask():
    app.run(host="0.0.0.0", port=5000)

# 每分钟访问指定 URL
def ping_url():
    url = "https://tgbot-9x65.onrender.com"
    while True:
        try:
            response = requests.get(url)
            if response.status_code == 200:
                logger.info(f"Ping successful: {url}")
            else:
                logger.warning(f"Ping failed with status code {response.status_code}: {url}")
        except Exception as e:
            logger.error(f"Ping error: {e}")
        time.sleep(60)

# 主函数
def main():
    app_thread = threading.Thread(target=run_flask)
    app_thread.daemon = True
    app_thread.start()

    ping_thread = threading.Thread(target=ping_url)
    ping_thread.daemon = True
    ping_thread.start()

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.REPLY, handle_message)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY & filters.User(user_id=MY_USER_ID), handle_reply)
    )
    application.add_handler(CallbackQueryHandler(handle_verification_callback))

    application.run_polling()

if __name__ == "__main__":
    main()
