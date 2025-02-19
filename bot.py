import os
import glob
import sqlite3
import tempfile
import asyncio
import logging
import numpy as np
from datetime import datetime
import random

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultsButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineQuery,
    InlineQueryResultCachedVideo,
    FSInputFile,
    InputMediaVideo,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineQueryResultMpeg4Gif,
    InlineQueryResultCachedMpeg4Gif,
)
from aiogram.enums import ParseMode
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties

from moviepy.editor import VideoFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE = os.getenv("DATABASE", "database.db")
VIDEOS_DIR = os.getenv("VIDEOS", "videos")
CHANNEL_ID = os.getenv("CHANNEL_ID")

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
    # We add "channel_message_id" so we can later delete the message from the channel.
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS video_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            video_note_file_id TEXT NOT NULL,
            channel_message_id INTEGER NOT NULL,
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


def add_video_note(
    user_id: int,
    video_note_file_id: str,
    channel_message_id: int,
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
            """INSERT INTO video_notes (user_id, video_note_file_id, channel_message_id, uploaded_video_file_id, text, caption, duration, width, height, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                video_note_file_id,
                channel_message_id,
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
            """SELECT id, video_note_file_id, channel_message_id, uploaded_video_file_id, text, caption, duration, width, height, created_at 
               FROM video_notes WHERE user_id = ? ORDER BY created_at DESC""",
            (user_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "video_note_file_id": row[1],
            "channel_message_id": row[2],
            "uploaded_video_file_id": row[3],
            "text": row[4],
            "caption": row[5],
            "duration": row[6],
            "width": row[7],
            "height": row[8],
            "created_at": row[9],
        }
        for row in rows
    ]


def get_video_by_id(video_id: int):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """SELECT id, video_note_file_id, channel_message_id, uploaded_video_file_id, text, caption, duration, width, height, created_at 
               FROM video_notes WHERE id = ?""",
            (video_id,),
        )
        row = cursor.fetchone()
    if row:
        return {
            "id": row[0],
            "video_note_file_id": row[1],
            "channel_message_id": row[2],
            "uploaded_video_file_id": row[3],
            "text": row[4],
            "caption": row[5],
            "duration": row[6],
            "width": row[7],
            "height": row[8],
            "created_at": row[9],
        }
    return None


def update_video_text(
    video_id: int, text: str, video_note_file_id: str, channel_message_id: int
):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE video_notes SET text = ?, video_note_file_id = ?, channel_message_id = ? WHERE id = ?",
            (text, video_note_file_id, channel_message_id, video_id),
        )
        conn.commit()


def delete_video(video_id: int):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM video_notes WHERE id = ?", (video_id,))
        conn.commit()


def update_video_caption(video_id: int, caption: str):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE video_notes SET caption = ? WHERE id = ?",
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
            [KeyboardButton(text="🎥 Create New"), KeyboardButton(text="🎬 Templates")]
        ],
        resize_keyboard=True,
    )


# ----- VIDEO PROCESSING FUNCTIONS -----
async def process_video_file(bot: Bot, file_id: str) -> str:
    """
    Processes the video to crop it to a 1:1 ratio and returns the local path
    of the processed video.
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
    Uses Pilmoji for better emoji support.
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

    fontsize = size // 16
    font_path = "./SF-Pro.ttf"  # make sure this file is in your project
    try:
        font = ImageFont.truetype(font_path, fontsize)
    except Exception as e:
        logging.warning(f"Could not load custom font: {e}. Using default font.")
        font = ImageFont.load_default()

    max_text_width = int(size * 0.8)
    words = text.split()
    lines = []
    current_line = ""
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


async def send_video_note_to_channel(
    bot: Bot,
    video,
    duration: int,
    length: int,
    user: types.User,
    caption: str,
    text: str,
) -> (str, types.Message):
    """
    Sends a video note to the designated channel (CHANNEL_ID) to obtain its file_id.
    Also sends a reply message to the video note with user info and timestamp.
    Returns a tuple of (video_note_file_id, channel_message).
    """
    if not CHANNEL_ID:
        raise ValueError("CHANNEL_ID is not set in environment.")
    channel_message = await bot.send_video_note(
        chat_id=CHANNEL_ID,
        video_note=video,
        duration=duration,
        length=length,
        disable_notification=True,
    )
    if not channel_message.video_note:
        raise ValueError("Failed to send video note")
    user_info = format_user_info(user, caption, text)
    # Send reply message in the channel with user info and timestamp.
    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=user_info,
        reply_to_message_id=channel_message.message_id,
        disable_notification=True,
    )
    return channel_message.video_note.file_id, channel_message


def format_user_info(user: types.User, caption: str = None, text: str = None) -> str:
    """Format user info string with optional caption and text."""
    parts = [f"From: @{user.username or user.first_name}"]

    if caption:
        parts.append(f"Caption: {caption}")

    if text:
        parts.append(f"Text: {text}")

    return "\n".join(parts)


# ----- HANDLERS -----
async def start_handler(message: Message):
    add_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or "",
    )
    await message.answer(
        "Welcome to Video note Bot!", reply_markup=main_menu_keyboard()
    )


async def create_new_handler(message: Message):
    await message.answer(
        "Please send me a video to create a video note",
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
        vid_path = await process_video_file(message.bot, message.video.file_id)
        raw_file_id = message.video.file_id
    else:
        vid_path = message.video.file_id
        raw_file_id = message.video.file_id

    await state.update_data(
        video_file_id=vid_path,
        raw_video_file_id=raw_file_id,
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
        "Send me the text to overlay on your video, or press Skip to continue without text",
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
            video = get_video_by_id(int(edit_id))
            if not video:
                await message.answer(
                    "❌ Video not found", reply_markup=main_menu_keyboard()
                )
                await state.clear()
                return
            # Remove overlay: update text to empty without modifying channel message.
            update_video_text(
                int(edit_id),
                "",
                video["video_note_file_id"],
                video["channel_message_id"],
            )
            await message.answer(
                "✅ Overlay text removed successfully!",
                reply_markup=main_menu_keyboard(),
            )
        else:
            overlay_text = text_input
            progress_msg = await message.answer(
                "Processing video... Please wait...",
                reply_markup=types.ReplyKeyboardRemove(),
            )
            try:
                video = get_video_by_id(int(edit_id))
                if not video:
                    await message.answer(
                        "❌ Video not found", reply_markup=main_menu_keyboard()
                    )
                    await state.clear()
                    return

                final_video_path = await add_text_to_video_file(
                    message.bot, video["uploaded_video_file_id"], overlay_text
                )
                final_media = FSInputFile(final_video_path)
                final_size = min(video["width"], video["height"])
                new_video_note_id, channel_message = await send_video_note_to_channel(
                    message.bot,
                    final_media,
                    video["duration"],
                    final_size,
                    message.from_user,
                    video["caption"] or "",
                    video["text"] or "",
                )
                media = InputMediaVideo(
                    media=new_video_note_id,
                    caption=video["caption"] or "",
                    parse_mode=ParseMode.HTML,
                )
                await message.answer_media_group([media])

                update_video_text(
                    int(edit_id),
                    overlay_text,
                    new_video_note_id,
                    channel_message.message_id,
                )
                cleanup_file(final_video_path)
                await message.answer(
                    "✅ Overlay text updated successfully!",
                    reply_markup=main_menu_keyboard(),
                )
            except Exception as e:
                logging.error(f"Error processing video: {e}")
                await message.answer(
                    "❌ Error processing video",
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
            temp_path = await download_temp_file(message.bot, vid_source)
            final_media = FSInputFile(temp_path)

        final_size = min(width, height)
        new_video_note_id, channel_message = await send_video_note_to_channel(
            message.bot,
            final_media,
            duration,
            final_size,
            message.from_user,
            caption,
            overlay_text,
        )
        media = InputMediaVideo(
            media=new_video_note_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        await message.answer_media_group([media])

        add_video_note(
            data.get("user_id"),
            new_video_note_id,
            channel_message.message_id,
            vid_source,
            overlay_text,
            caption,
            duration,
            width,
            height,
        )
        await state.update_data(video_sent=True)

        if overlay_text:
            cleanup_file(final_video_path)
        else:
            cleanup_file(temp_path)

        await message.answer(
            "✅ Video note created!", reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logging.error(f"Error processing video: {e}")
        await message.answer(
            "❌ Error processing video",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        await progress_msg.delete()
        await state.clear()


async def list_videos_handler(message: Message):
    vids = get_user_videos(message.from_user.id)
    if not vids:
        await message.answer("📭 No video notes", reply_markup=main_menu_keyboard())
        return

    header_msg = await message.answer(
        "🎬 Your video notes:", reply_markup=main_menu_keyboard()
    )

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
                        text="Delete",
                        callback_data=f"delete_{video['id']}_{header_msg.message_id}",
                    )
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
                "Send me the new overlay text to apply on your video, or press Skip to remove it",
                reply_markup=markup,
            )
            await state.set_state(VideoStates.ADD_TEXT)
            await callback.answer()
        else:
            await callback.answer("❌ Video not found", show_alert=True)
    except Exception as e:
        logging.error(f"Error in edit overlay callback: {e}")
        await callback.answer("❌ Error editing overlay", show_alert=True)


async def callback_delete(callback: CallbackQuery):
    try:
        parts = callback.data.split("_")
        video_id = int(parts[1])
        header_msg_id = int(parts[2]) if len(parts) > 2 else None

        # Get video info before deleting
        video = get_video_by_id(video_id)
        if video and video["channel_message_id"]:
            try:
                # Delete the video note and its reply message in the channel
                await callback.bot.delete_message(
                    CHANNEL_ID, video["channel_message_id"]
                )
                # Try to delete the reply message (it's the next message)
                await callback.bot.delete_message(
                    CHANNEL_ID, video["channel_message_id"] + 1
                )
            except Exception as e:
                logging.warning(f"Could not delete channel message: {e}")

        delete_video(video_id)

        try:
            await callback.message.delete()
        except Exception as e:
            logging.warning(f"Could not delete message: {e}")

        vids_remaining = get_user_videos(callback.from_user.id)

        if not vids_remaining and header_msg_id:
            await callback.bot.delete_message(
                message_id=header_msg_id,
                chat_id=callback.message.chat.id,
            )
            await callback.bot.send_message(
                chat_id=callback.message.chat.id,
                text="📭 No video notes",
                reply_markup=main_menu_keyboard(),
            )

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
                "Send me the new caption for your video, or press Skip to remove it",
                reply_markup=markup,
            )
            await state.set_state(VideoStates.EDIT_CAPTION)
            await callback.answer()
        else:
            await callback.answer("❌ Video not found", show_alert=True)
    except Exception as e:
        logging.error(f"Error in edit caption callback: {e}")
        await callback.answer("❌ Error editing caption", show_alert=True)


ephemeral_video_cache = {}

PREVIEW_CACHE = {}  # Cache for preview file_ids


async def convert_video_to_animation(video_path: str) -> str:
    """Convert video to animation format that Telegram can handle."""
    output_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    try:
        clip = VideoFileClip(video_path)
        # Ensure the video meets Telegram's requirements
        # Max size: 384x384, Max duration: 60s
        if clip.duration > 60:
            clip = clip.subclip(0, 60)

        if clip.size[0] > 384 or clip.size[1] > 384:
            # Resize while maintaining aspect ratio
            ratio = min(384 / clip.size[0], 384 / clip.size[1])
            new_size = (int(clip.size[0] * ratio), int(clip.size[1] * ratio))
            clip = clip.resize(new_size)

        # Write with settings suitable for Telegram animations
        clip.write_videofile(
            output_path,
            codec="libx264",
            audio=False,  # Animations don't need audio
            fps=30,  # Good frame rate for smooth playback
            preset="ultrafast",  # Faster encoding
            verbose=False,
            logger=None,
        )
        clip.close()
        return output_path
    except Exception as e:
        logging.error(f"Error converting video: {e}")
        if os.path.exists(output_path):
            os.unlink(output_path)
        raise


async def inline_query_handler(inline_query: InlineQuery):
    user_id = inline_query.from_user.id
    query_text = inline_query.query.strip()
    results = []

    if query_text:
        # Show all available default videos as previews with the query text
        default_files = glob.glob("videos/*.mp4")
        if not default_files:
            result = InlineQueryResultArticle(
                id="no_default",
                title="No default videos available",
                input_message_content=InputTextMessageContent(
                    message_text="No default videos available."
                ),
            )
            results.append(result)
        else:
            for idx, video_path in enumerate(default_files):
                if not os.path.exists(video_path):
                    logging.error(f"Video file not found: {video_path}")
                    continue

                preview_key = f"{video_path}_{query_text}"

                if preview_key in PREVIEW_CACHE:
                    # Use cached preview
                    preview_data = PREVIEW_CACHE[preview_key]
                    preview_id = preview_data["file_id"]
                    thumb_id = preview_data.get("thumb_id")
                else:
                    try:
                        # Convert video to animation format
                        animation_path = await convert_video_to_animation(video_path)
                        logging.info(
                            f"Successfully converted {video_path} to animation format"
                        )

                        # Send video as animation to get file_id
                        with open(animation_path, "rb") as video_file:
                            preview_msg = await inline_query.bot.send_animation(
                                chat_id=CHANNEL_ID,
                                animation=FSInputFile(animation_path),
                                disable_notification=True,
                            )

                            if not preview_msg:
                                logging.error(
                                    f"Failed to send animation: preview_msg is None for {video_path}"
                                )
                                continue

                            if not preview_msg.animation:
                                logging.error(
                                    f"Failed to get animation from message: animation is None for {video_path}"
                                )
                                continue

                            preview_id = preview_msg.animation.file_id
                            thumb_id = (
                                preview_msg.animation.thumbnail.file_id
                                if preview_msg.animation.thumbnail
                                else None
                            )

                            logging.info(
                                f"Successfully got file_id for {video_path}: {preview_id}"
                            )

                            PREVIEW_CACHE[preview_key] = {
                                "file_id": preview_id,
                                "thumb_id": thumb_id,
                            }

                            # Clean up
                            cleanup_file(animation_path)
                            try:
                                await inline_query.bot.delete_message(
                                    CHANNEL_ID, preview_msg.message_id
                                )
                            except Exception as ex:
                                logging.warning(
                                    f"Could not delete preview message: {ex}"
                                )
                    except Exception as e:
                        logging.error(
                            f"Error creating preview for {video_path}: {str(e)}"
                        )
                        continue

                try:
                    # Create preview result with autoplay gif
                    result = InlineQueryResultCachedMpeg4Gif(
                        id=f"preview_{idx}_{user_id}",
                        mpeg4_file_id=preview_id,
                        title=f"Video {idx + 1} with text overlay",
                        caption=query_text,
                    )
                    results.append(result)
                except Exception as e:
                    logging.error(
                        f"Error creating InlineQueryResultCachedMpeg4Gif: {str(e)}"
                    )
                    continue

    else:
        vids = get_user_videos(user_id)
        if vids:
            for video in vids:
                created_at = datetime.strptime(video["created_at"], "%d.%m.%Y %H:%M:%S")
                title = created_at.strftime("%H:%M:%S")
                result = InlineQueryResultCachedMpeg4Gif(
                    id=str(video["id"]),
                    mpeg4_file_id=video["video_note_file_id"],
                    title=title,
                    caption=video["caption"] if video["caption"] else "",
                    description=video["text"] if video["text"] else "",
                )
                results.append(result)
        else:
            button = InlineQueryResultsButton(
                text="📭 No video notes. Open bot?",
                start_parameter="create_video",
            )
            await inline_query.answer(
                results=[],
                button=button,
                cache_time=300,
                is_personal=True,
            )
            return

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

    video = get_video_by_id(int(edit_video_id))
    if not video:
        await message.answer("❌ Video not found", reply_markup=main_menu_keyboard())
        await state.clear()
        return

    if new_caption == "⏩ Skip":
        update_video_caption(int(edit_video_id), "")
        success_message = "✅ Caption removed successfully!"
        new_caption = ""
    else:
        update_video_caption(int(edit_video_id), new_caption)
        success_message = "✅ Caption updated successfully!"

    try:
        user_info = format_user_info(
            message.from_user, caption=new_caption, text=video["text"]
        )
        await message.bot.edit_message_text(
            text=user_info,
            chat_id=CHANNEL_ID,
            message_id=video["channel_message_id"] + 1,
        )
    except Exception as e:
        logging.warning(f"Could not update channel message: {e}")

    await message.answer(success_message, reply_markup=main_menu_keyboard())
    await state.clear()


# ----- MAIN FUNCTION -----
async def main():
    initialize_db()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.register(start_handler, CommandStart())
    dp.message.register(video_handler, F.video)
    dp.message.register(text_handler, VideoStates.ADD_TEXT)
    dp.message.register(list_videos_handler, F.text == "🎬 Templates")
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
