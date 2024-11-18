import os
from telegram.ext import Updater, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
import logging
from datetime import datetime, timedelta

# 配置日志记录
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# 从环境变量获取 Token 和用户 ID
TOKEN = os.getenv("TOKEN")
MY_USER_ID = int(os.getenv("MY_USER_ID"))

if not TOKEN or not MY_USER_ID:
    raise ValueError("请设置环境变量 TOKEN 和 MY_USER_ID")

# 用于存储消息映射的字典和用户状态
message_map = {}
user_data = {}

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
def needs_verification(user_id):
    user_info = user_data.get(user_id, {})
    last_verified = user_info.get("last_verified_date")
    today_date = datetime.now().date()

    # 若未验证过或上次验证日期不是今天，则需要验证
    if not last_verified or last_verified != today_date:
        user_data[user_id] = {
            "last_verified_date": today_date,
            "auto_discard_until": None,
            "awaiting_verification": True
        }
        return True
    return False

# 消息处理函数
def handle_message(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    # 检查是否需要验证
    if needs_verification(user_id):
        update.message.reply_text(
            "这是一个验证问题，你要问的是否为技术问题？",
            reply_markup=get_verification_keyboard(),
        )
        # 不转发消息，因为我们在等待验证
        return

    # 检查是否需要自动丢弃消息
    auto_discard_until = user_data.get(user_id, {}).get("auto_discard_until")
    if auto_discard_until and datetime.now() < auto_discard_until:
        logger.info(f"Message from {user_id} discarded as per auto-discard rule.")
        return

    # 转发消息到管理员
    forwarded_message = context.bot.forward_message(chat_id=MY_USER_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
    message_map[forwarded_message.message_id] = user_id
    logger.info(f"Message from {update.effective_user.first_name} ({user_id}) forwarded to user {MY_USER_ID}")

# 处理验证选项
def handle_verification_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id

    if query.data == "tech_yes":
        # 选择是技术问题，自动回复并启用消息丢弃
        query.message.reply_text("免费项目无任何技术支持，自行解决。")
        user_data[user_id]["awaiting_verification"] = False
        user_data[user_id]["auto_discard_until"] = datetime.now() + timedelta(hours=24)
    elif query.data == "tech_no":
        # 选择非技术问题，通知消息已转发
        query.message.reply_text("您的消息已转发给管理员。")
        user_data[user_id]["awaiting_verification"] = False

# 回复处理函数
def handle_reply(update: Update, context: CallbackContext):
    if update.effective_user.id == MY_USER_ID:
        # 获取原始消息的发送者ID
        original_chat_id = message_map.get(update.message.reply_to_message.message_id)
        if original_chat_id:
            context.bot.send_message(chat_id=original_chat_id, text=update.message.text)
            logger.info(f"Reply from {update.effective_user.first_name} sent to original user ({original_chat_id})")
        else:
            logger.warning("Original message not found for reply.")

def main():
    updater = Updater(TOKEN, use_context=True)

    dp = updater.dispatcher

    # 添加消息处理器
    dp.add_handler(MessageHandler(Filters.all & ~Filters.command & ~Filters.reply, handle_message))
    dp.add_handler(MessageHandler(Filters.text & Filters.reply & Filters.user(user_id=MY_USER_ID), handle_reply))

    # 添加回调查询处理器
    dp.add_handler(CallbackQueryHandler(handle_verification_callback))

    # 开始机器人
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
