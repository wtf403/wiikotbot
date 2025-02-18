import os
import glob
import uuid
import sqlite3
import tempfile
import asyncio
import logging
import numpy as np
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeDefault,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineQuery,
    InlineQueryResultCachedVideo,
    FSInputFile,
    InputMediaVideo,
)
from aiogram.enums import ParseMode
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties

from moviepy.editor import VideoFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE = os.getenv("DATABASE", "database.db")
VIDEOS_DIR = os.getenv("VIDEOS", "videos")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


# ----- FSM STATES -----
class VideoStates(StatesGroup):
    ADD_TEXT = State()
    EDIT_CAPTION = State()


# ----- DATABASE FUNCTIONS -----
def initialize_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            registration_date TEXT
        )"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS video_circles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            video_note_file_id TEXT NOT NULL,
            uploaded_video_file_id TEXT NOT NULL,
            text TEXT,
            caption TEXT,
            duration INTEGER,
            width INTEGER,
            height INTEGER,
            created_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


def add_user(user_id: int, username: str, first_name: str):
    reg_date = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, registration_date) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, reg_date),
        )
        conn.commit()


def add_video_circle(
    user_id: int,
    video_note_file_id: str,
    uploaded_video_file_id: str,
    text: str,
    caption: str,
    duration: int,
    width: int,
    height: int,
):
    created_at = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """INSERT INTO video_circles (user_id, video_note_file_id, uploaded_video_file_id, text, caption, duration, width, height, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                video_note_file_id,
                uploaded_video_file_id,
                text,
                caption,
                duration,
                width,
                height,
                created_at,
            ),
        )
        conn.commit()


def get_user_videos(user_id: int):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT id, video_note_file_id, uploaded_video_file_id, text, caption, duration, width, height, created_at FROM video_circles WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "video_note_file_id": row[1],
            "uploaded_video_file_id": row[2],
            "text": row[3],
            "caption": row[4],
            "duration": row[5],
            "width": row[6],
            "height": row[7],
            "created_at": row[8],
        }
        for row in rows
    ]


def get_video_by_id(video_id: int):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT id, video_note_file_id, uploaded_video_file_id, text, caption, duration, width, height, created_at FROM video_circles WHERE id = ?",
            (video_id,),
        )
        row = cursor.fetchone()
    if row:
        return {
            "id": row[0],
            "video_note_file_id": row[1],
            "uploaded_video_file_id": row[2],
            "text": row[3],
            "caption": row[4],
            "duration": row[5],
            "width": row[6],
            "height": row[7],
            "created_at": row[8],
        }
    return None


def update_video_text(video_id: int, text: str, video_note_file_id: str):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE video_circles SET text = ?, video_note_file_id = ? WHERE id = ?",
            (text, video_note_file_id, video_id),
        )
        conn.commit()


def delete_video(video_id: int):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM video_circles WHERE id = ?", (video_id,))
        conn.commit()


def update_video_caption(video_id: int, caption: str):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE video_circles SET caption = ? WHERE id = ?",
            (caption, video_id),
        )
        conn.commit()


# ----- HELPER FUNCTIONS FOR FILE HANDLING -----
async def download_temp_file(bot: Bot, file_id: str, suffix=".mp4") -> str:
    file = await bot.get_file(file_id)
    file_data = await bot.download_file(file.file_path)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(file_data.read())
    tmp.close()
    return tmp.name


def cleanup_file(path: str):
    if os.path.exists(path):
        os.unlink(path)


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎥 Create New"), KeyboardButton(text="🎬 My Videos")]
        ],
        resize_keyboard=True,
    )


# ----- VIDEO PROCESSING FUNCTIONS -----
async def process_video_file(bot: Bot, file_id: str) -> str:
    """
    Processes the video to crop it to a 1:1 ratio and returns the local path
    of the processed video. (No message is sent from here.)
    """
    input_path = await download_temp_file(bot, file_id)
    temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    temp_output.close()
    clip = VideoFileClip(input_path)
    width, height = clip.size
    if width > height:
        x = int((width - height) / 2)
        y = 0
        size = height
    else:
        x = 0
        y = int((height - width) / 2)
        size = width
    cropped = clip.crop(x1=x, y1=y, x2=x + size, y2=y + size)
    cropped.write_videofile(
        temp_output.name, codec="libx264", audio_codec="aac", verbose=False, logger=None
    )
    clip.close()
    cropped.close()
    cleanup_file(input_path)
    return temp_output.name


async def add_text_to_video_file(bot: Bot, file_id: str, text: str) -> str:
    """
    Adds text overlay to the video and returns the path of the final file.
    Now uses Pilmoji for better emoji support.
    """
    input_path = await download_temp_file(bot, file_id)
    temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    temp_output.close()
    clip = VideoFileClip(input_path)
    width, height = clip.size
    size = min(width, height)
    if width != height:
        x = int((width - size) / 2)
        y = int((height - size) / 2)
        clip = clip.crop(x1=x, y1=y, x2=x + size, y2=y + size)

    # Determine a suitable font size and use an emoji-compatible font.
    fontsize = size // 16
    font_path = "./SF-Pro.ttf"  # make sure this file is in your project
    try:
        font = ImageFont.truetype(font_path, fontsize)
    except Exception as e:
        logging.warning(f"Could not load custom font: {e}. Using default font.")
        font = ImageFont.load_default()

    # Prepare for text wrapping.
    max_text_width = int(size * 0.8)
    words = text.split()
    lines = []
    current_line = ""
    # dummy image to measure text dimensions
    dummy_img = Image.new("RGBA", (max_text_width, fontsize), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy_img)
    for word in words:
        test_line = f"{current_line} {word}".strip() if current_line else word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_text_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    total_height = len(lines) * (fontsize + 5)
    text_img = Image.new("RGBA", (max_text_width, total_height), (0, 0, 0, 0))
    # Render the text (with emoji support) using Pilmoji.
    with Pilmoji(text_img) as pilmoji:
        y_text = 0
        for line in lines:
            text_size = pilmoji.getsize(line, font=font)
            w = text_size[0]
            x_text = (max_text_width - w) // 2
            pilmoji.text(
                (x_text, y_text),
                line,
                font=font,
                fill="white",
                stroke_width=2,
                stroke_fill="black",
            )
            y_text += fontsize + 5

    text_clip = ImageClip(np.array(text_img)).set_duration(clip.duration)
    text_clip = text_clip.set_position("center")
    final_clip = CompositeVideoClip([clip, text_clip])
    if clip.audio:
        final_clip = final_clip.set_audio(clip.audio)
    final_clip.write_videofile(
        temp_output.name, codec="libx264", audio_codec="aac", verbose=False, logger=None
    )
    clip.close()
    final_clip.close()
    cleanup_file(input_path)
    return temp_output.name


async def send_temp_video_note(
    bot: Bot, chat_id: int, video, duration: int, length: int
) -> str:
    """
    Sends a video note temporarily to obtain its file_id.
    The message is immediately deleted after obtaining the file_id.
    """
    temp_msg = await bot.send_video_note(
        chat_id=chat_id,
        video_note=video,
        duration=duration,
        length=length,
        disable_notification=True,
    )
    if not temp_msg.video_note:
        raise ValueError("Failed to send video note")
    video_note_id = temp_msg.video_note.file_id
    try:
        await bot.delete_message(chat_id, temp_msg.message_id)
    except Exception as e:
        logging.warning(f"Could not delete temporary video note message: {e}")
    return video_note_id


# ----- HANDLERS -----
async def start_handler(message: Message):
    add_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or "",
    )
    await message.answer(
        "Welcome to Video Circle Bot!", reply_markup=main_menu_keyboard()
    )


async def create_new_handler(message: Message):
    await message.answer(
        "Please send me a video to create a video circle.",
        reply_markup=main_menu_keyboard(),
    )


async def video_handler(message: Message, state: FSMContext):
    if not message.video:
        return
    if message.video.duration > 60:
        await message.answer("Video too long (max 60 sec).")
        return

    add_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or "",
    )

    # Process the video if necessary.
    aspect = message.video.width / message.video.height
    if abs(aspect - 1.0) > 0.01:
        # Process and return a local file path.
        vid_path = await process_video_file(message.bot, message.video.file_id)
        # Use the original file id for downloading later.
        raw_file_id = message.video.file_id
    else:
        # The video is already square – no processing needed.
        vid_path = message.video.file_id
        raw_file_id = message.video.file_id

    await state.update_data(
        video_file_id=vid_path,  # This is used initially for sending.
        raw_video_file_id=raw_file_id,  # This remains valid for future downloads.
        caption=message.caption or "",
        user_id=message.from_user.id,
        video_duration=message.video.duration,
        video_width=message.video.width,
        video_height=message.video.height,
    )
    markup = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏩ Skip")]],
        resize_keyboard=True,
    )
    await message.answer(
        "Send me the text to overlay on your video, or press Skip to continue without text.",
        reply_markup=markup,
    )
    await state.set_state(VideoStates.ADD_TEXT)


async def text_handler(message: Message, state: FSMContext):
    data = await state.get_data()

    if data.get("video_sent"):
        await state.clear()
        return

    text_input = message.text.strip() if message.text else ""

    # --- Edit overlay text scenario ---
    if data.get("edit_video_id") and data.get("state") != VideoStates.EDIT_CAPTION:
        edit_id = data.get("edit_video_id")
        if text_input == "⏩ Skip":
            # Just update the text to empty, keep the existing video note ID
            video = get_video_by_id(int(edit_id))
            if not video:
                await message.answer(
                    "❌ Video not found.", reply_markup=main_menu_keyboard()
                )
                await state.clear()
                return
            update_video_text(int(edit_id), "", video["video_note_file_id"])
            await message.answer(
                "✅ Overlay text removed successfully!",
                reply_markup=main_menu_keyboard(),
            )
        else:
            overlay_text = text_input
            progress_msg = await message.answer("Processing video... Please wait...")
            try:
                # Get the video data from database
                video = get_video_by_id(int(edit_id))
                if not video:
                    await message.answer(
                        "❌ Video not found.", reply_markup=main_menu_keyboard()
                    )
                    await state.clear()
                    return

                # Process video with new text
                final_video_path = await add_text_to_video_file(
                    message.bot, video["uploaded_video_file_id"], overlay_text
                )
                final_media = FSInputFile(final_video_path)
                final_size = min(video["width"], video["height"])

                # First send as video note to get new video note ID
                new_video_note_id = await send_temp_video_note(
                    message.bot,
                    message.chat.id,
                    final_media,
                    video["duration"],
                    final_size,
                )

                # Then send as regular video using the video note ID
                media = InputMediaVideo(
                    media=new_video_note_id,
                    caption=video["caption"] or "",
                    parse_mode=ParseMode.HTML,
                )
                await message.answer_media_group([media])

                # Update database with new text and video note ID
                update_video_text(int(edit_id), overlay_text, new_video_note_id)
                cleanup_file(final_video_path)
                await message.answer(
                    "✅ Overlay text updated successfully!",
                    reply_markup=main_menu_keyboard(),
                )
            except Exception as e:
                logging.error(f"Error processing video: {e}")
                await message.answer(
                    "❌ Error processing video. Please try again.",
                    reply_markup=main_menu_keyboard(),
                )
            finally:
                await progress_msg.delete()
        await state.clear()
        return

    # --- New video creation scenario ---
    vid_source = data.get("raw_video_file_id")
    caption = data.get("caption") or ""
    duration = data.get("video_duration")
    width = data.get("video_width")
    height = data.get("video_height")

    overlay_text = text_input if text_input != "⏩ Skip" else ""
    progress_msg = await message.answer("Processing video... Please wait...")
    try:
        if overlay_text:
            final_video_path = await add_text_to_video_file(
                message.bot, vid_source, overlay_text
            )
            final_media = FSInputFile(final_video_path)
        else:
            # Download and prepare the original video
            temp_path = await download_temp_file(message.bot, vid_source)
            final_media = FSInputFile(temp_path)

        final_size = min(width, height)
        # First send as video note to get video note ID
        new_video_note_id = await send_temp_video_note(
            message.bot, message.chat.id, final_media, duration, final_size
        )

        # Then send as regular video using the video note ID
        media = InputMediaVideo(
            media=new_video_note_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        await message.answer_media_group([media])

        # Save both the final (video note) file id and the original raw video id in DB
        add_video_circle(
            data.get("user_id"),
            new_video_note_id,
            vid_source,
            overlay_text,
            caption,
            duration,
            width,
            height,
        )
        await state.update_data(video_sent=True)

        # Cleanup temporary files
        if overlay_text:
            cleanup_file(final_video_path)
        else:
            cleanup_file(temp_path)

        await message.answer(
            "✅ Video circle created!", reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logging.error(f"Error processing video: {e}")
        await message.answer(
            "❌ Error processing video. Please try again.",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        await progress_msg.delete()
        await state.clear()


async def list_videos_handler(message: Message):
    vids = get_user_videos(message.from_user.id)
    if not vids:
        await message.answer(
            "📭 No video circles created yet.", reply_markup=main_menu_keyboard()
        )
        return

    await message.answer("🎬 Your video circles:", reply_markup=main_menu_keyboard())
    for video in vids:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Edit Text", callback_data=f"edit_text_{video['id']}"
                    ),
                    InlineKeyboardButton(
                        text="Edit Caption", callback_data=f"edit_caption_{video['id']}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Delete", callback_data=f"delete_{video['id']}"
                    ),
                ],
            ]
        )
        await message.answer_video(
            video=video["video_note_file_id"],
            caption=video["caption"] if video["caption"] else "",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )


async def callback_edit(callback: CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            await callback.answer("Invalid callback data", show_alert=True)
            return
        video_id = parts[2]
        video = get_video_by_id(int(video_id))
        if video:
            await state.update_data(
                edit_video_id=str(video_id),
                video_file_id=video["video_note_file_id"],
                uploaded_video_file_id=video["uploaded_video_file_id"],
                caption=video["caption"] if video["caption"] else "",
                text=video["text"],
                video_duration=video["duration"],
                video_width=video["width"],
                video_height=video["height"],
                user_id=callback.from_user.id,
            )
            markup = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⏩ Skip")]],
                resize_keyboard=True,
            )
            await callback.message.answer(
                "Send me the new overlay text to apply on your video, or press Skip to remove it.",
                reply_markup=markup,
            )
            await state.set_state(VideoStates.ADD_TEXT)
            await callback.answer()
        else:
            await callback.answer("❌ Video not found", show_alert=True)
    except Exception as e:
        logging.error(f"Error in edit overlay callback: {e}")
        await callback.answer("❌ Error editing overlay.", show_alert=True)


async def callback_delete(callback: CallbackQuery):
    try:
        video_id = int(callback.data.split("_")[1])
        delete_video(video_id)
        # Try to delete both the video note and its text message
        try:
            await callback.message.delete()
        except Exception as e:
            logging.warning(f"Could not delete message: {e}")

        # Find and delete the text message if it exists (it should be the next message)
        try:
            next_message = await callback.message.bot.get_message(
                callback.message.chat.id, callback.message.message_id + 1
            )
            if "💬" in next_message.text:
                await next_message.delete()
        except Exception as e:
            logging.warning(f"Could not delete text message: {e}")

        await callback.answer("🗑 Deleted")
    except Exception as e:
        logging.error(f"Error in delete callback: {e}")
        await callback.answer("❌ Error deleting video", show_alert=True)


async def callback_edit_caption(callback: CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            await callback.answer("Invalid callback data", show_alert=True)
            return
        video_id = parts[2]
        video = get_video_by_id(int(video_id))
        if video:
            await state.update_data(
                edit_video_id=str(video_id),
                video_file_id=video["video_note_file_id"],
                caption=video["caption"] if video["caption"] else "",
                text=video["text"],
                video_duration=video["duration"],
                video_width=video["width"],
                video_height=video["height"],
                user_id=callback.from_user.id,
            )
            markup = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⏩ Skip")]],
                resize_keyboard=True,
            )
            await callback.message.answer(
                "Send me the new caption for your video, or press Skip to remove it.",
                reply_markup=markup,
            )
            await state.set_state(VideoStates.EDIT_CAPTION)
            await callback.answer()
        else:
            await callback.answer("❌ Video not found", show_alert=True)
    except Exception as e:
        logging.error(f"Error in edit caption callback: {e}")
        await callback.answer("❌ Error editing caption", show_alert=True)


async def inline_query_handler(inline_query: InlineQuery):
    user_id = inline_query.from_user.id
    vids = get_user_videos(user_id)
    results = []
    if vids:
        for video in vids:
            # Format the date for title
            created_at = datetime.strptime(video["created_at"], "%d.%m.%Y %H:%M:%S")
            title = created_at.strftime("%H:%M:%S")
            description = video["text"] if video["text"] else ""
            try:
                # Process video to ensure it is square if needed
                processed_video = await process_video_file(
                    inline_query.bot, video["uploaded_video_file_id"]
                )
                final_size = min(video["width"], video["height"])
                # First get video note ID
                video_note_id = await send_temp_video_note(
                    inline_query.bot,
                    user_id,
                    FSInputFile(processed_video),
                    video["duration"],
                    final_size,
                )
                cleanup_file(processed_video)
                results.append(
                    InlineQueryResultCachedVideo(
                        id=str(video["id"]),
                        video_file_id=video_note_id,
                        title=title,
                        description=description,
                        caption=video["caption"] if video["caption"] else "",
                    )
                )
            except Exception as e:
                logging.error(f"Error processing video {video['id']}: {e}")
                continue
    else:
        default_files = glob.glob("videos/*.mp4")
        for f in default_files:
            try:
                with open(f, "rb") as video_file:
                    temp_media = FSInputFile(f)
                    # Get video info
                    clip = VideoFileClip(f)
                    duration = int(clip.duration)
                    size = min(clip.size)
                    clip.close()
                    # First get video note ID
                    video_note_id = await send_temp_video_note(
                        inline_query.bot, user_id, temp_media, duration, size
                    )
                    results.append(
                        InlineQueryResultCachedVideo(
                            id=str(uuid.uuid4()),
                            video_file_id=video_note_id,
                            title="Default",
                            description=os.path.basename(f),
                            caption="",
                        )
                    )
            except Exception as e:
                logging.error(f"Error processing default video {f}: {e}")
                continue

    await inline_query.answer(results, cache_time=300, is_personal=True)


async def caption_edit_handler(message: Message, state: FSMContext):
    data = await state.get_data()

    if data.get("video_sent"):
        await state.clear()
        return

    new_caption = message.text.strip() if message.text else ""
    edit_video_id = data.get("edit_video_id")
    if not edit_video_id:
        await message.answer(
            "No video found to update.", reply_markup=main_menu_keyboard()
        )
        await state.clear()
        return

    if new_caption == "⏩ Skip":
        update_video_caption(int(edit_video_id), "")
        success_message = "✅ Caption removed successfully!"
    else:
        update_video_caption(int(edit_video_id), new_caption)
        success_message = "✅ Caption updated successfully!"

    await message.answer(success_message, reply_markup=main_menu_keyboard())
    await state.clear()


# ----- MAIN FUNCTION -----
async def main():
    initialize_db()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.register(start_handler, CommandStart())
    dp.message.register(create_new_handler, Command("create"))
    dp.message.register(video_handler, F.video)
    dp.message.register(text_handler, VideoStates.ADD_TEXT)
    dp.message.register(list_videos_handler, Command("list"))
    dp.message.register(list_videos_handler, F.text == "🎬 My Videos")
    dp.message.register(create_new_handler, F.text == "🎥 Create New")
    dp.callback_query.register(callback_edit, F.data.startswith("edit_text_"))
    dp.callback_query.register(callback_delete, F.data.startswith("delete_"))
    dp.callback_query.register(
        callback_edit_caption, F.data.startswith("edit_caption_")
    )
    dp.inline_query.register(inline_query_handler)
    dp.message.register(caption_edit_handler, VideoStates.EDIT_CAPTION)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
