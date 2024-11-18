from telegram.ext import Updater, MessageHandler, Filters, CallbackContext, CallbackQueryHandler, CommandHandler
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
import logging
from datetime import datetime, timedelta
import time
import os

# 从环境变量获取 Token 和用户 ID
TOKEN = os.getenv("TOKEN")
MY_USER_ID = int(os.getenv("MY_USER_ID"))

if not TOKEN or not MY_USER_ID:
    raise ValueError("请设置环境变量 TOKEN 和 MY_USER_ID")

# 配置日志记录（带轮换功能）
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=3)  # 最大5MB，保留3个备份
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO, handlers=[handler])
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

    # 若未验证过或上次验证日期不是今天，则需要验证
    if not last_verified or last_verified != today_date:
        return True
    return False

# 发送验证消息
def send_verification_message(update: Update, context: CallbackContext):
    if not context.user_data.get("awaiting_verification", False):
        context.user_data["awaiting_verification"] = True
        update.message.reply_text(
            "在线验证：你要问的是否为技术问题？\n每24小时仅有一次选择机会。",
            reply_markup=get_verification_keyboard(),
        )

# 消息处理函数
def handle_message(update: Update, context: CallbackContext):
    # 检查是否需要验证
    if needs_verification(context):
        send_verification_message(update, context)
        logger.info(f"Message from {update.effective_user.first_name} ({update.effective_chat.id}) discarded as user is not verified.")
        return  # 丢弃所有未验证用户的消息

    # 如果用户在 24 小时内选择了 "是"，丢弃消息
    if "tech_rejected_until" in context.user_data:
        reject_until = context.user_data["tech_rejected_until"]
        if datetime.now() < reject_until:
            logger.info(f"Message from {update.effective_user.first_name} ({update.effective_chat.id}) discarded due to 'yes' selection.")
            return  # 丢弃消息

    # 处理消息
    process_message(update, context)

# 处理用户点击的验证选项
def handle_verification_callback(update: Update, context: CallbackContext):
    query = update.callback_query

    if query.data == "tech_yes":
        # 选择是技术问题，自动回复并记录 24 小时内丢弃消息的时间
        query.edit_message_text("免费项目不解答问题，自行搜索解决。\n您今日的验证次数已用完，24小时后可重新发起对话。\n下次验证之前的消息将被自动丢弃")
        context.user_data["tech_rejected_until"] = datetime.now() + timedelta(hours=24)

    elif query.data == "tech_no":
        # 选择非技术问题
        query.edit_message_text("您已通过验证，请重新发送你的业务需求，我将转发给管理员。")

    # 验证完成后，解除等待验证状态，并更新最后验证时间
    context.user_data["awaiting_verification"] = False
    context.user_data["last_verified_date"] = datetime.now().date()

# 处理已验证的消息
def process_message(update: Update, context: CallbackContext):
    if update.message.photo or update.message.document:
        # 转发图片或文件
        forwarded_message = context.bot.forward_message(
            chat_id=MY_USER_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
        logger.info(f"Media from {update.effective_user.first_name} ({update.effective_chat.id}) forwarded to user {MY_USER_ID}")
    else:
        # 转发文本消息
        forwarded_message = context.bot.forward_message(
            chat_id=MY_USER_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
        logger.info(f"Message from {update.effective_user.first_name} ({update.effective_chat.id}) forwarded to user {MY_USER_ID}")

# 回复处理函数
def handle_reply(update: Update, context: CallbackContext):
    if update.effective_user.id == MY_USER_ID:
        # 获取原始消息的发送者ID
        forwarded_message = update.message.reply_to_message
        if forwarded_message:
            context.bot.send_message(chat_id=forwarded_message.chat.id, text=update.message.text)
            logger.info(f"Reply from {update.effective_user.first_name} sent to original user.")

# /start 命令处理
def handle_start(update: Update, context: CallbackContext):
    # 检查 24 小时内是否已响应过 /start
    last_start = context.user_data.get("last_start_date")
    if last_start and datetime.now() - last_start < timedelta(hours=24):
        logger.info(f"/start command from {update.effective_user.first_name} ({update.effective_chat.id}) discarded (already responded within 24 hours).")
        return  # 丢弃消息

    # 更新 /start 响应时间
    context.user_data["last_start_date"] = datetime.now()
    send_verification_message(update, context)

# 主函数
def main():
    updater = Updater(TOKEN, use_context=True)

    dp = updater.dispatcher

    # 添加命令处理器
    dp.add_handler(CommandHandler("start", handle_start))

    # 添加消息处理器
    dp.add_handler(MessageHandler(Filters.all & ~Filters.command & ~Filters.reply, handle_message))
    dp.add_handler(MessageHandler(Filters.text & Filters.reply & Filters.user(user_id=MY_USER_ID), handle_reply))

    # 添加回调查询处理器
    dp.add_handler(CallbackQueryHandler(handle_verification_callback))

    # 开始机器人
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
