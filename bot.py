from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

TOKEN = "8717641557:AAGNCfILgepyCgvWsGS5YWRSa68RLtTM7Tc"
OWNER_ID = 122190868

user_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("신사 페이백 신청", callback_data="payback")]]
    await update.message.reply_text(
    """⚠️ 신사 페이백 신청

매주 월요일 ~ 일요일 주간 페이백

📌 지급조건

✅ 소통방 주간 누적 채팅 500개 이상
✅ 신사 소통방 채널 입장

📌 신청방법

1️⃣ 아래 신청 버튼 클릭
2️⃣ 충전내역 첨부
3️⃣ 성명 / 계좌 작성

관리자 확인 후 순차적으로 지급됩니다.""",
    reply_markup=InlineKeyboardMarkup(keyboard)
)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_data[user_id] = {"step": "waiting_photo"}

    await query.message.reply_text("충전내역을 첨부해주세요.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id not in user_data or user_data[user_id].get("step") != "waiting_photo":
        return

    photo = update.message.photo[-1]
    user_data[user_id]["photo"] = photo.file_id
    user_data[user_id]["step"] = "waiting_account"

    await update.message.reply_text(
        "페이백 받을 성명/계좌를 작성해주세요.\n\n"
        "예시: 홍길동 / 카카오뱅크 3333-00-0000000"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id not in user_data:
        return

    if user_data[user_id].get("step") != "waiting_account":
        return

    account_info = update.message.text
    user = update.message.from_user
    photo_file_id = user_data[user_id]["photo"]
    username = f"@{user.username}" if user.username else "없음"

    await update.message.reply_text("담당자 확인후 페이백 지급.")

    owner_text = f"""
📩 신사 페이백 신청 도착

👤 신청자: {user.first_name}
🔗 텔레그램 ID: {username}
🆔 유저 고유번호: {user.id}

💳 성명/계좌:
{account_info}
"""

    await context.bot.send_photo(
        chat_id=OWNER_ID,
        photo=photo_file_id,
        caption=owner_text
    )

    del user_data[user_id]

app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_click))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

print("봇 실행 중...")
app.run_polling()
