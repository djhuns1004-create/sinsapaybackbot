import html
import logging
import os
import sqlite3
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "122190868"))
DB_PATH = os.getenv("DB_PATH", "/data/payback.db")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN 환경변수가 없습니다.")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

DEFAULT_GUIDE = (
    "♻️ 신사 주간 페이백 신청 ♻️\n\n"
    "매주 월요일 ~ 일요일 손실 페이백\n\n"
    "⚠️ 지급조건\n\n"
    "✅️ 소통방 주간 누적 채팅 500개 이상\n"
    "✅️ 신사 소통방 코드만 참여가능(그외 지급불가)\n"
    "✅️ 소통방 채널 입장(미입장시 지급불가)\n"
    "✅️ 최소 누적 손실 30만원 이상부터 신청가능\n\n"
    "📌 신청방법\n\n"
    "✅️ 아래 페이백 신청 버튼 클릭\n"
    "✅️ 충전내역 첨부하기\n"
    "✅️ 지급받을 성명/계좌 작성\n\n"
    "👀 관리자 확인후 지급됩니다"
)

DEFAULT_SETTINGS = {
    "guide_text": DEFAULT_GUIDE,
    "guide_text__html": "",
    "guide_backup": "",
    "guide_backup__html": "",
    "button_text": "🏛 신사 페이백 신청 🏛",
    "button_text__html": "",
    "photo_prompt": "📑 제휴사 충전내역을 첨부해주세요.",
    "photo_prompt__html": "",
    "account_prompt": (
        "✍🏻 페이백 받을 성명/계좌를 작성해주세요.\n\n"
        "✍🏻 예시: 홍길동 / 카카오뱅크 3333-00-0000000"
    ),
    "account_prompt__html": "",
    "complete_text": "👀 담당자 확인후 매주 월요일 페이백 지급.",
    "complete_text__html": "",
}

RICH_KEYS = {
    "guide_text",
    "button_text",
    "photo_prompt",
    "account_prompt",
    "complete_text",
}


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT NOT NULL,
                account_info TEXT NOT NULL,
                photo_file_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )

        # 기존 분할형 안내문이 있던 DB는 최초 1회 전체 안내문으로 자동 변환
        guide_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'guide_text'"
        ).fetchone()
        if not guide_row or not guide_row["value"]:
            def old_value(old_key: str, fallback: str = "") -> str:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key = ?",
                    (old_key,),
                ).fetchone()
                return row["value"] if row else fallback

            migrated_guide = (
                f"{old_value('title', '♻️ 신사 주간 페이백 신청 ♻️')}\n\n"
                f"{old_value('period', '매주 월요일 ~ 일요일 손실 페이백')}\n\n"
                "⚠️ 지급조건\n\n"
                f"{old_value('conditions')}\n\n"
                "📌 신청방법\n\n"
                f"{old_value('method')}\n\n"
                "👀 관리자 확인후 지급됩니다"
            )
            conn.execute(
                """
                INSERT INTO settings(key, value)
                VALUES ('guide_text', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (migrated_guide,),
            )

        conn.commit()


def get_setting(key: str) -> str:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(key: str, value: str) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()


def utf16_to_python_index(text: str, offset: int) -> int:
    used = 0
    for index, character in enumerate(text):
        units = len(character.encode("utf-16-le")) // 2
        if used >= offset:
            return index
        used += units
        if used >= offset:
            return index + 1
    return len(text)


def is_custom_emoji_entity(entity) -> bool:
    entity_type = getattr(entity.type, "value", entity.type)
    return (
        entity_type == MessageEntity.CUSTOM_EMOJI
        or entity_type == "custom_emoji"
        or str(entity.type).lower().endswith("custom_emoji")
    )


def message_to_html(message) -> str:
    text = message.text or ""
    entities = [
        entity
        for entity in (message.entities or [])
        if is_custom_emoji_entity(entity)
        and entity.custom_emoji_id
    ]

    if not entities:
        return html.escape(text)

    entities.sort(key=lambda entity: entity.offset)
    output = []
    cursor = 0

    for entity in entities:
        start = utf16_to_python_index(text, entity.offset)
        end = utf16_to_python_index(
            text,
            entity.offset + entity.length,
        )

        if start < cursor:
            continue

        output.append(html.escape(text[cursor:start]))
        fallback = html.escape(text[start:end] or "✨")
        output.append(
            f'<tg-emoji emoji-id="{entity.custom_emoji_id}">'
            f"{fallback}</tg-emoji>"
        )
        cursor = end

    output.append(html.escape(text[cursor:]))
    return "".join(output)


def rich_setting(key: str) -> str:
    rich = get_setting(f"{key}__html")
    if rich:
        return rich
    return html.escape(get_setting(key))


def build_guide_html() -> str:
    return rich_setting("guide_text")


def user_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                get_setting("button_text"),
                callback_data="payback:start",
            )
        ]]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📝 전체 안내문 수정",
                    callback_data="admin:edit:guide_text",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔘 신청버튼 문구",
                    callback_data="admin:edit:button_text",
                ),
                InlineKeyboardButton(
                    "📑 사진요청 문구",
                    callback_data="admin:edit:photo_prompt",
                ),
            ],
            [
                InlineKeyboardButton(
                    "✍️ 계좌요청 문구",
                    callback_data="admin:edit:account_prompt",
                ),
                InlineKeyboardButton(
                    "✅ 신청완료 문구",
                    callback_data="admin:edit:complete_text",
                ),
            ],
            [
                InlineKeyboardButton(
                    "👀 안내문 미리보기",
                    callback_data="admin:preview",
                )
            ],
            [
                InlineKeyboardButton(
                    "💾 현재 안내문 백업",
                    callback_data="admin:backup",
                ),
                InlineKeyboardButton(
                    "♻️ 백업 안내문 복원",
                    callback_data="admin:restore",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚠️ 기본 안내문 초기화",
                    callback_data="admin:reset_confirm",
                )
            ],
        ]
    )


def reset_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⚠️ 정말 초기화",
                    callback_data="admin:reset",
                ),
                InlineKeyboardButton(
                    "❌ 취소",
                    callback_data="admin:cancel",
                ),
            ]
        ]
    )


def application_review_keyboard(
    application_id: int,
    user_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ 승인",
                    callback_data=f"application:approve:{application_id}:{user_id}",
                ),
                InlineKeyboardButton(
                    "❌ 반려",
                    callback_data=f"application:reject:{application_id}:{user_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💸 지급완료",
                    callback_data=f"application:paid:{application_id}:{user_id}",
                )
            ],
        ]
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        build_guide_html(),
        reply_markup=user_start_keyboard(),
        parse_mode="HTML",
    )


async def admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.effective_user.id != OWNER_ID:
        return

    context.user_data.pop("admin_edit_key", None)
    await update.message.reply_text(
        "👑 신사 페이백 관리자센터\n\n"
        "전체 안내문을 한 번에 수정할 수 있습니다.\n"
        "줄바꿈과 프리미엄 이모지도 그대로 저장됩니다.",
        reply_markup=admin_keyboard(),
    )


async def payback_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    await query.answer()

    context.user_data.clear()
    context.user_data["application_step"] = "waiting_photo"

    await query.message.reply_text(
        rich_setting("photo_prompt"),
        parse_mode="HTML",
    )


async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if context.user_data.get("application_step") != "waiting_photo":
        return

    context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    context.user_data["application_step"] = "waiting_account"

    await update.message.reply_text(
        rich_setting("account_prompt"),
        parse_mode="HTML",
    )


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user_id = update.effective_user.id
    text = update.message.text or ""

    edit_key = context.user_data.get("admin_edit_key")
    if user_id == OWNER_ID and edit_key:
        set_setting(edit_key, text)

        if edit_key in RICH_KEYS:
            set_setting(
                f"{edit_key}__html",
                message_to_html(update.message),
            )

        context.user_data.pop("admin_edit_key", None)

        if edit_key in RICH_KEYS:
            preview = message_to_html(update.message)
            label = (
                "전체 안내문"
                if edit_key == "guide_text"
                else "문구"
            )
            await update.message.reply_text(
                f"✅ {label}이 저장되었습니다.\n\n"
                f"표시 미리보기:\n{preview}",
                reply_markup=admin_keyboard(),
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "✅ 문구가 저장되었습니다.",
                reply_markup=admin_keyboard(),
            )
        return

    if context.user_data.get("application_step") != "waiting_account":
        return

    photo_file_id = context.user_data.get("photo_file_id")
    if not photo_file_id:
        context.user_data.clear()
        await update.message.reply_text(
            "신청 상태가 만료되었습니다. /start부터 다시 진행해주세요."
        )
        return

    user = update.effective_user
    username = f"@{user.username}" if user.username else "없음"
    full_name = user.full_name or user.first_name or str(user.id)

    with db_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO applications(
                user_id,
                username,
                full_name,
                account_info,
                photo_file_id,
                status
            )
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (
                user.id,
                user.username,
                full_name,
                text,
                photo_file_id,
            ),
        )
        application_id = cursor.lastrowid
        conn.commit()

    await update.message.reply_text(
        rich_setting("complete_text"),
        parse_mode="HTML",
    )

    owner_text = (
        "📩 신사 페이백 신청 도착\n\n"
        f"👤 신청자: {full_name}\n"
        f"🔗 텔레그램 ID: {username}\n"
        f"🆔 유저 고유번호: {user.id}\n\n"
        "💳 성명/계좌:\n"
        f"{text}"
    )

    await context.bot.send_photo(
        chat_id=OWNER_ID,
        photo=photo_file_id,
        caption=owner_text,
        reply_markup=application_review_keyboard(
            application_id,
            user.id,
        ),
    )

    context.user_data.clear()


async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query.from_user.id != OWNER_ID:
        await query.answer(
            "관리자만 사용할 수 있습니다.",
            show_alert=True,
        )
        return

    await query.answer()
    action = query.data

    if action == "admin:preview":
        await query.message.reply_text(
            build_guide_html(),
            reply_markup=user_start_keyboard(),
            parse_mode="HTML",
        )
        return

    if action == "admin:backup":
        set_setting("guide_backup", get_setting("guide_text"))
        set_setting(
            "guide_backup__html",
            get_setting("guide_text__html"),
        )
        await query.message.reply_text(
            "💾 현재 전체 안내문을 백업했습니다.",
            reply_markup=admin_keyboard(),
        )
        return

    if action == "admin:restore":
        backup = get_setting("guide_backup")
        backup_html = get_setting("guide_backup__html")

        if not backup and not backup_html:
            await query.message.reply_text(
                "복원할 백업 안내문이 없습니다.",
                reply_markup=admin_keyboard(),
            )
            return

        set_setting("guide_text", backup)
        set_setting("guide_text__html", backup_html)

        await query.message.reply_text(
            "♻️ 백업 안내문을 복원했습니다.",
            reply_markup=admin_keyboard(),
        )
        return

    if action == "admin:reset_confirm":
        await query.message.reply_text(
            "⚠️ 전체 안내문을 기본 문구로 초기화합니다.\n"
            "현재 안내문은 사라지므로 먼저 백업하는 것을 권장합니다.",
            reply_markup=reset_confirm_keyboard(),
        )
        return

    if action == "admin:reset":
        set_setting("guide_text", DEFAULT_GUIDE)
        set_setting("guide_text__html", "")
        context.user_data.pop("admin_edit_key", None)

        await query.message.reply_text(
            "✅ 전체 안내문을 기본 문구로 초기화했습니다.",
            reply_markup=admin_keyboard(),
        )
        return

    if action == "admin:cancel":
        await query.message.reply_text(
            "초기화를 취소했습니다.",
            reply_markup=admin_keyboard(),
        )
        return

    if action.startswith("admin:edit:"):
        key = action.split(":", 2)[2]
        context.user_data["admin_edit_key"] = key

        labels = {
            "guide_text": "전체 안내문",
            "button_text": "신청버튼 문구",
            "photo_prompt": "사진요청 문구",
            "account_prompt": "계좌요청 문구",
            "complete_text": "신청완료 문구",
        }

        if key == "guide_text":
            await query.message.reply_text(
                "📝 전체 안내문 수정\n\n"
                "새 안내문 전체를 한 번에 보내주세요.\n"
                "제목, 지급조건, 신청방법, 마지막 안내까지 "
                "원하는 형태로 모두 작성하면 됩니다.\n\n"
                "줄바꿈과 프리미엄 이모지도 그대로 저장됩니다.\n\n"
                f"현재 안내문:\n{get_setting('guide_text')}"
            )
            return

        premium_notice = (
            "\n\n프리미엄 이모지를 함께 보내면 "
            "안내 메시지에 그대로 표시됩니다."
        )
        if key == "button_text":
            premium_notice += (
                "\n실제 버튼 안에서는 텔레그램 제한으로 "
                "프리미엄 이모지가 일반 이모지 형태로 표시됩니다."
            )

        await query.message.reply_text(
            f"✏️ {labels.get(key, key)} 수정\n\n"
            "새 문구를 입력해주세요."
            f"{premium_notice}\n\n"
            f"현재 내용:\n{get_setting(key)}"
        )


async def application_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query.from_user.id != OWNER_ID:
        await query.answer(
            "관리자만 사용할 수 있습니다.",
            show_alert=True,
        )
        return

    parts = query.data.split(":")
    action = parts[1]
    application_id = int(parts[2])
    user_id = int(parts[3])

    status_map = {
        "approve": "approved",
        "reject": "rejected",
        "paid": "paid",
    }
    message_map = {
        "approve": "✅ 페이백 신청이 승인되었습니다.",
        "reject": "❌ 페이백 신청이 반려되었습니다.",
        "paid": "💸 페이백 지급이 완료되었습니다.",
    }

    with db_connect() as conn:
        conn.execute(
            "UPDATE applications SET status = ? WHERE id = ?",
            (status_map[action], application_id),
        )
        conn.commit()

    await query.answer(
        message_map[action],
        show_alert=True,
    )

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=message_map[action],
        )
    except Exception:
        logger.exception("사용자 처리 알림 전송 실패")

    if action == "paid":
        await query.message.edit_reply_markup(reply_markup=None)


async def debug_emoji(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.effective_user.id != OWNER_ID:
        return

    entities = update.message.entities or []
    custom_ids = [
        entity.custom_emoji_id
        for entity in entities
        if is_custom_emoji_entity(entity)
        and entity.custom_emoji_id
    ]

    await update.message.reply_text(
        "✨ 프리미엄 이모지 진단\n\n"
        f"감지된 custom emoji: {len(custom_ids)}개\n"
        + (
            "\n".join(custom_ids)
            if custom_ids
            else "감지되지 않았습니다. 프리미엄 이모지를 명령어 뒤에 함께 보내주세요."
        )
    )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.exception(
        "업데이트 처리 중 오류",
        exc_info=context.error,
    )


def main() -> None:
    init_db()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("debugemoji", debug_emoji))

    application.add_handler(
        CallbackQueryHandler(
            payback_start,
            pattern=r"^payback:start$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^admin:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            application_callback,
            pattern=r"^application:",
        )
    )

    application.add_handler(
        MessageHandler(filters.PHOTO, handle_photo)
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("신사 페이백봇 실행 중")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
