import html
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
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

KST = timezone(timedelta(hours=9))

def now_kst():
    return datetime.now(KST)

def week_key():
    return (now_kst() - timedelta(days=now_kst().weekday())).strftime("%Y-%m-%d")

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
    "reject_text": "❌ 페이백 신청이 거절되었습니다.",
    "reject_text__html": "",
    "applications_open": "1",
    "allow_reapply_rejected": "1",
}

RICH_KEYS = {
    "guide_text",
    "button_text",
    "photo_prompt",
    "account_prompt",
    "complete_text",
    "reject_text",
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

        columns = {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}
        for name, definition in {
            "week_key": "TEXT", "amount": "INTEGER NOT NULL DEFAULT 0",
            "processed_at": "TEXT", "processed_by": "INTEGER",
            "reject_reason": "TEXT"
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE applications ADD COLUMN {name} {definition}")
        conn.execute("""CREATE TABLE IF NOT EXISTS drafts (
            user_id INTEGER PRIMARY KEY, step TEXT NOT NULL,
            photo_file_id TEXT, updated_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER NOT NULL,
            action TEXT NOT NULL, actor_id INTEGER NOT NULL,
            details TEXT, created_at TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_app_user_week ON applications(user_id,week_key)")
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


def existing_application(user_id):
    with db_connect() as conn:
        return conn.execute("""SELECT * FROM applications WHERE user_id=? AND week_key=?
            AND (status != 'rejected' OR ?='0') ORDER BY id DESC LIMIT 1""",
            (user_id, week_key(), get_setting("allow_reapply_rejected"))).fetchone()

def save_draft(user_id, step, photo=None):
    with db_connect() as conn:
        conn.execute("""INSERT INTO drafts(user_id,step,photo_file_id,updated_at)
          VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
          step=excluded.step,photo_file_id=excluded.photo_file_id,updated_at=excluded.updated_at""",
          (user_id,step,photo,now_kst().isoformat()))

def load_draft(user_id):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM drafts WHERE user_id=?",(user_id,)).fetchone()

def clear_draft(user_id):
    with db_connect() as conn:
        conn.execute("DELETE FROM drafts WHERE user_id=?",(user_id,))

def status_label(status):
    return {"pending":"접수대기", "approved":"승인", "rejected":"거절", "paid":"지급완료"}.get(status,status)

def user_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                get_setting("button_text"),
                callback_data="payback:start",
            )
        ], [InlineKeyboardButton("📋 내 신청내역", callback_data="payback:mine")]]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 신청관리", callback_data="admin:list:pending:0"),
             InlineKeyboardButton("📊 지급통계", callback_data="admin:stats")],
            [InlineKeyboardButton("🔒 접수 열기/닫기", callback_data="admin:toggle")],
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
                    "❌ 거절문구 수정",
                    callback_data="admin:edit:reject_text",
                )
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


def application_review_keyboard(application_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 승인",callback_data=f"application:approve:{application_id}:{user_id}"),
         InlineKeyboardButton("❌ 거절",callback_data=f"application:reject:{application_id}:{user_id}")],
        [InlineKeyboardButton("💰 지급액 입력",callback_data=f"application:amount:{application_id}:{user_id}"),
         InlineKeyboardButton("💸 지급완료",callback_data=f"application:paid:{application_id}:{user_id}")]
    ])


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
    uid = query.from_user.id
    if get_setting("applications_open") != "1":
        await query.message.reply_text("현재 페이백 신청 접수가 마감되었습니다.")
        return
    if existing_application(uid):
        await query.message.reply_text("이번 주차에 이미 신청한 내역이 있습니다. '내 신청내역'에서 확인해주세요.")
        return
    context.user_data.clear()
    save_draft(uid, "waiting_photo")
    context.user_data["application_step"] = "waiting_photo"

    await query.message.reply_text(
        rich_setting("photo_prompt"),
        parse_mode="HTML",
    )


async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    draft = load_draft(update.effective_user.id)
    if not draft or draft["step"] != "waiting_photo":
        return
    save_draft(update.effective_user.id, "waiting_account", update.message.photo[-1].file_id)
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

    if user_id == OWNER_ID and context.user_data.get("admin_amount_id"):
        app_id=context.user_data["admin_amount_id"]
        if not text.strip().isdigit() or int(text.strip())<=0 or int(text.strip())>10000000000:
            await update.message.reply_text("1원 이상 금액을 숫자로 입력해주세요."); return
        with db_connect() as conn:
            cur=conn.execute("UPDATE applications SET amount=? WHERE id=? AND status='approved'",(int(text),app_id))
            if cur.rowcount: conn.execute("INSERT INTO audit_log(application_id,action,actor_id,details,created_at) VALUES (?,?,?,?,?)",(app_id,"amount",OWNER_ID,text,now_kst().isoformat()))
        context.user_data.pop("admin_amount_id",None)
        await update.message.reply_text(f"#{app_id} 지급금액 저장: {int(text):,}원" if cur.rowcount else "승인 상태가 아니어서 저장하지 않았습니다.")
        return
    if user_id == OWNER_ID and context.user_data.get("admin_reject_id"):
        app_id=context.user_data.pop("admin_reject_id")
        reason=text.strip()[:300]
        with db_connect() as conn:
            cur=conn.execute("""UPDATE applications SET status='rejected',reject_reason=?,processed_at=?,processed_by=?
                WHERE id=? AND status='pending'""",(reason,now_kst().isoformat(),OWNER_ID,app_id))
            if cur.rowcount:
                conn.execute("INSERT INTO audit_log(application_id,action,actor_id,details,created_at) VALUES (?,?,?,?,?)",(app_id,"rejected",OWNER_ID,reason,now_kst().isoformat()))
                row=conn.execute("SELECT user_id FROM applications WHERE id=?",(app_id,)).fetchone()
        if cur.rowcount:
            try: await context.bot.send_message(row['user_id'],rich_setting('reject_text')+"\n사유: "+html.escape(reason),parse_mode="HTML")
            except Exception: logger.exception("거절 알림 실패")
        await update.message.reply_text("거절 처리 완료" if cur.rowcount else "이미 처리된 신청입니다.")
        return
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

    draft = load_draft(user_id)
    if not draft or draft["step"] != "waiting_account":
        return
    photo_file_id = draft["photo_file_id"]
    if not photo_file_id:
        context.user_data.clear()
        await update.message.reply_text(
            "신청 상태가 만료되었습니다. /start부터 다시 진행해주세요."
        )
        return

    if get_setting("applications_open") != "1" or existing_application(user_id):
        clear_draft(user_id)
        await update.message.reply_text("접수가 마감되었거나 이미 신청한 기록이 있습니다.")
        return
    if len(text) > 500:
        await update.message.reply_text("계좌정보는 500자 이내로 입력해주세요.")
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
                status, week_key
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                user.id,
                user.username,
                full_name,
                text,
                photo_file_id, week_key(),
            ),
        )
        application_id = cursor.lastrowid
        conn.commit()

    clear_draft(user_id)
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

    try:
        await context.bot.send_photo(
        chat_id=OWNER_ID,
        photo=photo_file_id,
        caption=owner_text,
        reply_markup=application_review_keyboard(
            application_id,
            user.id,
        ),
        )
    except Exception:
        logger.exception("관리자 신청 알림 전송 실패: 신청번호 %s", application_id)

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

    if action == "admin:toggle":
        new = "0" if get_setting("applications_open") == "1" else "1"
        set_setting("applications_open", new)
        await query.message.reply_text("신청 접수: " + ("열림" if new == "1" else "마감"), reply_markup=admin_keyboard())
        return
    if action == "admin:stats":
        with db_connect() as conn:
            rows = conn.execute("""SELECT status,COUNT(*) n,COALESCE(SUM(amount),0) total
                FROM applications WHERE week_key=? GROUP BY status""",(week_key(),)).fetchall()
        summary = "📊 이번 주 페이백 통계 ("+week_key()+")\n\n"
        summary += "\n".join(f"{status_label(r['status'])}: {r['n']}건 / {r['total']:,}원" for r in rows) or "신청 없음"
        await query.message.reply_text(summary,reply_markup=admin_keyboard())
        return
    if action.startswith("admin:list:"):
        _,_,status,page = action.split(":")
        page = max(0,int(page))
        with db_connect() as conn:
            rows = conn.execute("SELECT * FROM applications WHERE status=? ORDER BY id DESC LIMIT 6 OFFSET ?",(status,page*6)).fetchall()
        buttons = [[InlineKeyboardButton(f"#{r['id']} {r['full_name'][:15]} · {r['week_key'] or '기존'}",callback_data=f"admin:view:{r['id']}")] for r in rows]
        nav=[]
        if page: nav.append(InlineKeyboardButton("◀ 이전",callback_data=f"admin:list:{status}:{page-1}"))
        if len(rows)==6: nav.append(InlineKeyboardButton("다음 ▶",callback_data=f"admin:list:{status}:{page+1}"))
        if nav: buttons.append(nav)
        buttons.append([InlineKeyboardButton(x,callback_data=f"admin:list:{v}:0") for x,v in [("대기","pending"),("승인","approved"),("거절","rejected"),("지급","paid")]])
        await query.message.reply_text(f"📋 {status_label(status)} 목록",reply_markup=InlineKeyboardMarkup(buttons))
        return
    if action.startswith("admin:view:"):
        app_id=int(action.split(":")[2])
        with db_connect() as conn:
            r=conn.execute("SELECT * FROM applications WHERE id=?",(app_id,)).fetchone()
        if not r: await query.message.reply_text("신청내역이 없습니다."); return
        caption=(f"신청 #{r['id']} / {status_label(r['status'])}\n"
            f"주차: {r['week_key'] or '기존 기록'}\n신청자: {r['full_name']} ({r['user_id']})\n"
            f"계좌: {r['account_info']}\n지급액: {r['amount']:,}원")
        await query.message.reply_photo(r['photo_file_id'],caption=caption[:1000],reply_markup=application_review_keyboard(r['id'],r['user_id']))
        return
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
            "reject_text": "거절문구",
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
    with db_connect() as conn:
        row=conn.execute("SELECT * FROM applications WHERE id=?",(application_id,)).fetchone()
    if not row:
        await query.answer("신청 기록이 없습니다.",show_alert=True); return
    if action == "amount":
        if row["status"] != "approved":
            await query.answer("승인된 신청만 지급액을 입력할 수 있습니다.",show_alert=True); return
        context.user_data["admin_amount_id"] = application_id
        await query.answer()
        await query.message.reply_text(f"#{application_id} 지급금액(원)을 숫자로 입력해주세요. /cancel 로 취소")
        return
    if action == "reject":
        if row["status"] != "pending":
            await query.answer("대기중인 신청만 거절할 수 있습니다.",show_alert=True); return
        context.user_data["admin_reject_id"] = application_id
        await query.answer()
        await query.message.reply_text(f"#{application_id} 거절 사유를 입력해주세요. /cancel 로 취소")
        return
    transitions={"approve":("pending","approved"),"paid":("approved","paid")}
    if action not in transitions:
        await query.answer("지원하지 않는 작업입니다.",show_alert=True); return
    old,new=transitions[action]
    if action=="paid" and row["amount"]<=0:
        await query.answer("지급금액을 먼저 입력해주세요.",show_alert=True); return
    with db_connect() as conn:
        cur=conn.execute("""UPDATE applications SET status=?,processed_at=?,processed_by=?
            WHERE id=? AND status=?""",(new,now_kst().isoformat(),OWNER_ID,application_id,old))
        if cur.rowcount:
            conn.execute("INSERT INTO audit_log(application_id,action,actor_id,created_at) VALUES (?,?,?,?)",(application_id,new,OWNER_ID,now_kst().isoformat()))
    if not cur.rowcount:
        await query.answer("이미 처리되었거나 현재 상태에서 처리할 수 없습니다.",show_alert=True); return
    await query.answer("처리 완료",show_alert=True)
    try:
        message="✅ 페이백 신청이 승인되었습니다." if action=="approve" else f"💸 페이백 {row['amount']:,}원 지급이 완료되었습니다."
        await context.bot.send_message(row["user_id"],message)
    except Exception: logger.exception("신청자 알림 실패")
    try: await query.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await query.message.reply_text(f"#{application_id} {status_label(new)} 처리 완료",reply_markup=application_review_keyboard(application_id,row['user_id']))


async def my_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    with db_connect() as conn:
        rows=conn.execute("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC LIMIT 5",(query.from_user.id,)).fetchall()
    if not rows:
        await query.message.reply_text("아직 신청내역이 없습니다."); return
    msg="📋 최근 신청내역\n\n"+"\n".join(
        f"#{r['id']} | {r['week_key'] or '기존 신청'} | {status_label(r['status'])}"+
        (f" | {r['amount']:,}원" if r['amount'] else "") for r in rows)
    await query.message.reply_text(msg)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    clear_draft(update.effective_user.id)
    await update.message.reply_text("진행 중인 입력을 취소했습니다.")


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

    application.add_handler(CallbackQueryHandler(my_applications,pattern=r"^payback:mine$"))
    application.add_handler(CommandHandler("cancel",cancel))
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
