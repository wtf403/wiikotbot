import os
import glob
import sqlite3
import tempfile
import asyncio
import logging
import numpy as np
import getpass
import aiohttp
from datetime import datetime

from dotenv import load_dotenv
from moviepy.editor import VideoFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji
from telethon import TelegramClient, errors
from telethon.tl.functions.messages import GetAvailableEffectsRequest

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineQuery,
    FSInputFile,
    InputMediaVideo,
    InlineQueryResultCachedMpeg4Gif,
    InputTextMessageContent,
    ReplyKeyboardRemove,
)

load_dotenv()
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE = os.getenv("DATABASE", "database.db")
VIDEOS_DIR = os.getenv("VIDEOS", "videos")
CHANNEL_ID = os.getenv("CHANNEL_ID")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
TWO_FA_PASSWORD = os.getenv("TWO_FA_PASSWORD")

TEXTS = {
    "welcome": "😸 Welcome to the Video Note Bot!",
    "create_new": "Send <b>video 📹</b>, <b>video link 🔗</b> or <b>video note 🪩</b>",
    "no_templates": "📭 No templates",
    "your_templates": "🎬 Your templates:",
    "no_recent_videos": "📭 No recent videos",
    "processing_video_note": "⏳ Processing video note... Please wait...",
    "changes_applied": "👍 Changes applied successfully!",
}

SUCCESS = {
    "text_updated": "✅ Text updated!",
    "caption_updated": "✅ Caption updated!",
    "video_note_created": "✅ Video note created!",
    "template_saved": "✅ Template saved!",
}
ERRORS = {
    "invalid_input": "☹️ Please use the buttons or enter valid content",
    "audio_not_supported": "❌ Sorry, audio files are not supported yet",
    "video_links_not_supported": "❌ Sorry, video links are not supported yet",
    "error_processing_video_note": "❌ Error processing video note",
    "error_updating_caption": "❌ Error updating caption",
    "no_video_in_state": "❌ No video data found in state",
    "error_applying_changes": "❌ Error applying changes",
}

BUTTONS = {
    "create": "🎥 Create New",
    "create:text": "#️⃣ Text",
    "create:caption": "💬 Caption",
    "create:effect": "✨ Effect",
    "create:cancel": "❌ Cancel",
    "create:done": "✅ Done",
    "create:template": "💾 Save as Template",
    "template": "🎬 Templates",
    "template:delete": "🗑 Delete Template",
    "recent": "🕑 Recent",
    "recent:delete": "🗑 Delete Recent",
    "create:apply": "🎉 Apply Changes",
}

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

DEFAULT_TEMPLATE_FILE_IDS = []
AVAILABLE_EFFECTS = {}
EMPTY_VALUE = "N/A"

# ----- FSM States -----
class CreateVideoNote(StatesGroup):
    idle = State() # Main state for modification options
    waiting_for_text = State()
    waiting_for_caption = State()
    waiting_for_effect = State()
    waiting_for_video = State()
    waiting_for_audio = State()

router = Router()

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
        """CREATE TABLE IF NOT EXISTS video_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            video_note_file_id TEXT NOT NULL,
            channel_message_id INTEGER NOT NULL,
            uploaded_video_file_id TEXT NOT NULL,
            text TEXT,
            caption TEXT,
            effect INTEGER,
            duration INTEGER,
            width INTEGER,
            height INTEGER,
            created_at TEXT
        )"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            video_file_id TEXT NOT NULL,
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
    effect: str,
    duration: int,
    width: int,
    height: int,
):
    created_at = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """INSERT INTO video_notes (user_id, video_note_file_id, channel_message_id, uploaded_video_file_id, text, caption, effect, duration, width, height, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                video_note_file_id,
                channel_message_id,
                uploaded_video_file_id,
                text,
                caption,
                effect,
                duration,
                width,
                height,
                created_at,
            ),
        )
        conn.commit()


def get_user_videos(user_id: int, limit: int = 10):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """SELECT id, video_note_file_id, channel_message_id, uploaded_video_file_id,
            text, caption, duration, width, height, created_at
            FROM video_notes WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
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
            """SELECT id, video_note_file_id, channel_message_id, uploaded_video_file_id,
            text, caption, duration, width, height, created_at
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


def delete_video(video_id: int):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM video_notes WHERE id = ?", (video_id,))
        conn.commit()


# ----- TEMPLATE FUNCTIONS -----
def add_template(user_id: int, video_file_id: str):
    created_at = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "INSERT INTO templates (user_id, video_file_id, created_at) VALUES (?, ?, ?)",
            (user_id, video_file_id, created_at),
        )
        conn.commit()


def get_user_templates(user_id: int):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT id, video_file_id, created_at FROM templates WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cursor.fetchall()
    return [
        {"id": row[0], "video_file_id": row[1], "created_at": row[2]} for row in rows
    ]


def delete_template_db(template_id: int):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()


def initialize_user_templates(user_id: int):
    if not get_user_templates(user_id):
        for file_id in DEFAULT_TEMPLATE_FILE_IDS:
            add_template(user_id, file_id)


async def load_default_templates(bot: Bot):
    global DEFAULT_TEMPLATE_FILE_IDS
    if not os.path.exists(VIDEOS_DIR):
        os.makedirs(VIDEOS_DIR)
    if not CHANNEL_ID:
        logging.error("CHANNEL_ID is not set in environment.")
        return

    video_files = glob.glob(os.path.join(VIDEOS_DIR, "*.mp4"))
    for video_file in video_files:
        try:
            with open(video_file, "rb") as f:
                msg = await bot.send_video_note(
                    chat_id=CHANNEL_ID,
                    video_note=FSInputFile(video_file),
                    disable_notification=True,
                )
                if msg.video_note:
                    DEFAULT_TEMPLATE_FILE_IDS.append(msg.video_note.file_id)
                    logging.info(f"Loaded template {video_file}")
                else:
                    logging.error(f"Failed to send video note for {video_file}")
        except Exception as e:
            logging.error(f"Error loading template {video_file}: {e}")


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


async def send_final(
    bot: Bot, chat, video_note_file_id: str, caption: str, caption_up: bool, effect_id: int
):
    return await bot.send_video(
        chat_id=chat,
        video=video_note_file_id,
        message_effect_id=effect_id,
        caption=caption,
        show_caption_above_media=caption_up,
    )


async def process_video_file_trim(
    bot: Bot, file_id: str, trim_duration: int = 60
) -> str:
    input_path = await download_temp_file(bot, file_id)
    temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    temp_output.close()
    clip = VideoFileClip(input_path)
    # Trim the clip if longer than trim_duration
    if clip.duration > trim_duration:
        clip = clip.subclip(0, trim_duration)
    width, height = clip.size
    # Crop to a square (center crop)
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


async def process_video_file(bot: Bot, file_id: str) -> str:
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
    font_path = "./SF-Pro.ttf"
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
    video: FSInputFile,
    duration: int,
    user: types.User,
    caption: str,
    caption_up: bool,
    effect_id: int,
):
    if not CHANNEL_ID:
        raise ValueError("CHANNEL_ID is not set in environment.")

    channel_message = await bot.send_video_note(
        chat_id=CHANNEL_ID,
        video_note=video,
        duration=duration,
        disable_notification=True,
        message_effect_id=effect_id,
    )

    # Return the full message object so the caller can get message_id and file_id
    return channel_message


def is_valid_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False


# ----- HANDLERS -----
@router.message(CommandStart())
async def start(message: Message):
    add_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or "",
    )
    initialize_user_templates(message.from_user.id)
    await message.answer(TEXTS["welcome"], reply_markup=main_kb())


@router.message(F.text == BUTTONS["create"])
async def create_new(message: Message, state: FSMContext):
    await message.answer(
        TEXTS["create_new"],
        parse_mode=ParseMode.HTML,
        reply_markup=main_kb(),
    )
    await state.set_state(CreateVideoNote.idle)


@router.message(F.video | F.video_note | F.text) # Handle video, note, or text URL
async def handle_video_input(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None and current_state.startswith("CreateVideoNote:"):
        await message.answer("❌ You are already editing a video note. Please Apply or Cancel first.")
        return

    processing_msg = None
    video_source_for_channel = None
    vid_path = None # Define vid_path here for broader scope in cleanup
    processed_vid_path = None # Define processed_vid_path for cleanup
    video_duration = 0
    original_input_file_id = None

    try:
        processing_msg = await message.answer(TEXTS["processing_video_note"])

        # --- 1. Process Input ---
        if message.video:
            original_input_file_id = message.video.file_id
            video_duration = message.video.duration
            if message.video.duration > 60:
                vid_path = await process_video_file_trim(message.bot, message.video.file_id, trim_duration=60)
                video_duration = 60
            else:
                vid_path = await process_video_file(message.bot, message.video.file_id)
            video_source_for_channel = FSInputFile(vid_path)

        elif message.video_note:
            original_input_file_id = message.video_note.file_id
            video_duration = message.video_note.duration
            video_source_for_channel = message.video_note.file_id

        elif message.text and is_valid_url(message.text):
            async with aiohttp.ClientSession() as session:
                # ... [Cobalt API and download logic - assumed correct for now]
                api_url = "http://cobalt:8000/api" # Replace with actual URL if different
                payload = {"url": message.text, "vQuality": "720"} # Adjust payload as needed
                async with session.post(api_url, json=payload) as response:
                    if response.status != 200: raise Exception(f"Cobalt API Error {response.status}")
                    data = await response.json()
                    if data.get('status') != 'stream': raise Exception(f"Cobalt status: {data.get('status')}")
                    video_url = data.get('url')
                    if not video_url: raise Exception("Cobalt did not return URL")

                # Download
                temp_dl_path_obj = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                async with session.get(video_url) as vid_response:
                    if vid_response.status != 200: raise Exception(f"Download failed: {vid_response.status}")
                    while True:
                        chunk = await vid_response.content.read(1024*1024) # Read in chunks
                        if not chunk: break
                        temp_dl_path_obj.write(chunk)
                temp_dl_path_obj.close()
                vid_path = temp_dl_path_obj.name # Original download path

                # Process downloaded file (crop/trim)
                clip = None
                processed_vid_path = None # Path after cropping/trimming
                try:
                    clip = VideoFileClip(vid_path)
                    video_duration = int(clip.duration)
                    original_input_file_id = vid_path # Store original path
                    if clip.duration > 60:
                        processed_vid_path = await process_video_file_trim(message.bot, vid_path, trim_duration=60)
                        video_duration = 60
                    else:
                        processed_vid_path = await process_video_file(message.bot, vid_path)
                finally:
                    if clip: clip.close()
                    # Cleanup original download ONLY if processing succeeded and created a new file
                    if processed_vid_path and vid_path != processed_vid_path:
                         cleanup_file(vid_path)
                    elif not processed_vid_path: # If processing failed, cleanup original download
                         cleanup_file(vid_path)

                if not processed_vid_path: raise Exception("Video processing failed after download.")
                video_source_for_channel = FSInputFile(processed_vid_path)
        else:
            await message.answer(ERRORS["invalid_input"], reply_markup=main_kb())
            if processing_msg: await processing_msg.delete()
            return

        # --- 2. Send to Channel ---
        if not video_source_for_channel:
             raise Exception("No video source determined for channel send.")

        channel_message = await send_video_note_to_channel(
            message.bot, video_source_for_channel, video_duration,
            message.from_user, caption=None, caption_up=False, effect_id=None,
        )

        # --- 3. Save Initial DB Record ---
        db_id = add_video_note(
            user_id=message.from_user.id,
            video_note_file_id=channel_message.video_note.file_id,
            channel_message_id=channel_message.message_id,
            uploaded_video_file_id=original_input_file_id if isinstance(original_input_file_id, str) else channel_message.video_note.file_id,
            text=None, caption=None, effect=None, duration=video_duration,
            width=channel_message.video_note.length, height=channel_message.video_note.length,
        )

        # --- 4. Send Preview to User ---
        preview_caption = format_preview_caption(None, None, None)
        preview_message = await message.answer_video(
             video=channel_message.video_note.file_id,
             caption=preview_caption,
             reply_markup=create_inline_kb()
        )

        # --- 5. Update State ---
        await state.set_state(CreateVideoNote.idle)
        await state.update_data(
            edit_video_id=db_id,
            preview_message_id=preview_message.message_id,
            text_overlay_source_id=channel_message.video_note.file_id,
            current_channel_msg_id=channel_message.message_id
        )

        # Send the Apply/Cancel reply keyboard
        await message.answer("Use the buttons below to apply or cancel.", reply_markup=create_apply_cancel_kb())

        # --- 6. Cleanup (vid_path or processed_vid_path) ---
        # Use the correct path variable depending on which one holds the final FSInputFile path
        path_to_cleanup = None
        if isinstance(video_source_for_channel, FSInputFile):
             path_to_cleanup = video_source_for_channel.path

        # Cleanup original vid_path if it exists and wasn't cleaned earlier (e.g., non-URL video input)
        if vid_path and vid_path != path_to_cleanup and os.path.exists(vid_path):
             cleanup_file(vid_path)
        # Cleanup the final processed path used for sending
        if path_to_cleanup:
             cleanup_file(path_to_cleanup)

        if processing_msg: await processing_msg.delete()

    except Exception as e:
        logging.error(f"Error processing video input: {e}", exc_info=True)
        await message.answer(ERRORS["error_processing_video_note"])
        # Enhanced Cleanup on error
        if vid_path and os.path.exists(vid_path): cleanup_file(vid_path)
        if processed_vid_path and os.path.exists(processed_vid_path): cleanup_file(processed_vid_path)
        if processing_msg: await processing_msg.delete()
        await state.clear()


@router.callback_query(F.data.startswith("create:text"))
async def modify_text(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("raw_video_file_id"):
        await callback.answer("No video found to update.", show_alert=True)
        await state.clear()
        return
    new_text = callback.message.text.strip()
    await state.update_data(overlay_text=new_text)
    await callback.answer(SUCCESS["text_updated"], show_alert=True)


@router.callback_query(F.data.startswith("create:caption"))
async def modify_caption(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    edit_video_id = data.get("edit_video_id")
    if not edit_video_id:
        await callback.answer("No video found to update.", show_alert=True)
        await state.clear()
        return
    new_caption = callback.message.text.strip()
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE video_notes SET caption = ? WHERE id = ?",
            (new_caption, edit_video_id),
        )
        conn.commit()
    await callback.answer(SUCCESS["caption_updated"], show_alert=True)


@router.callback_query(F.data.startswith("create:effect"))
async def modify_effect(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    message = await callback.message.answer(
        "Please send an emoji to apply the effect.",
        reply_markup=create_kd(placeholder="Send a message with effect you want"),
    )

    await state.update_data(data)

    await callback.answer(SUCCESS["effect_updated"], show_alert=True)


@router.callback_query(F.data.startswith("create:done"))
async def done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    raw_video_file_id = data.get("raw_video_file_id")
    caption = data.get("caption", "") # Note: Captions aren't directly supported by video notes
    overlay_text = data.get("overlay_text", "")
    effect = data.get("effect")
    video_duration = data.get("video_duration", 0)
    # original_input_id = data.get("original_input_id") # Retrieve if needed

    if not raw_video_file_id:
        await callback.answer("Error: No video data found in state.", show_alert=True)
        await state.clear()
        return

    progress_msg = await callback.message.answer(TEXTS["processing_video_note"])

    try:
        # 1. Apply text overlay if any
        video_to_send_id = raw_video_file_id
        processed_path = None
        if overlay_text:
            # Apply text overlay to the raw_video_file_id
            processed_path = await add_text_to_video_file(callback.bot, raw_video_file_id, overlay_text)
            video_to_send_id = FSInputFile(processed_path)
        else:
            # Use the existing file_id if no text overlay needed
            video_to_send_id = raw_video_file_id

        # 2. Send the final video note to the channel
        channel_message = await send_video_note_to_channel(
            callback.bot,
            video_to_send_id,
            video_duration,
            callback.from_user,
            caption, # Pass caption even if not directly used by video note itself
            False, # caption_up - irrelevant for video notes
            effect,
        )

        # 3. Save details to the database
        add_video_note(
            user_id=callback.from_user.id,
            video_note_file_id=channel_message.video_note.file_id, # Use file_id from channel msg
            channel_message_id=channel_message.message_id, # Use message_id from channel msg
            uploaded_video_file_id=raw_video_file_id, # Store the preview/processed ID
            text=overlay_text,
            caption=caption,
            effect=effect,
            duration=video_duration,
            width=channel_message.video_note.width, # Get dimensions from channel msg
            height=channel_message.video_note.height,
        )

        # 4. Send confirmation to the user with the template button
        # Note: Sending the video again to the user might be redundant if preview was shown
        # Sending just a confirmation message.
        inline_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=BUTTONS["create:template"],
                        # Use the file_id from the CHANNEL message for the template
                        callback_data=f"template_save|{channel_message.video_note.file_id}",
                    )
                ]
            ]
        )
        await callback.message.answer(
            text=SUCCESS["video_note_created"],
            reply_markup=inline_kb,
        )

    except Exception as e:
        logging.error(f"Error finalizing video note: {e}")
        await callback.answer(ERRORS["error_processing_video_note"], show_alert=True)
    finally:
        # Cleanup the temporary file if text overlay was applied
        if processed_path:
            cleanup_file(processed_path)
        await progress_msg.delete()
        await state.clear()


@router.callback_query(F.data.startswith("create:cancel"))
async def cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer(TEXTS["cancelled"], show_alert=True)


@router.callback_query(F.data.startswith("template"))
async def list_templates(callback: CallbackQuery):
    templates = get_user_templates(callback.from_user.id)
    if not templates:
        await callback.answer(TEXTS["no_templates"], show_alert=True)
        return
    await callback.message.answer(
        TEXTS["your_templates"], reply_markup=main_kb()
    )
    for template in templates:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Delete", callback_data=f"delete_template_{template['id']}"
                    )
                ]
            ]
        )
        await callback.message.answer_video(
            video=template["video_file_id"],
            caption="",
            reply_markup=kb,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("template:delete"))
async def delete_template(callback: CallbackQuery):
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            await callback.answer("Invalid callback data", show_alert=True)
            return
        template_id = int(parts[2])
        delete_template_db(template_id)
        await callback.message.delete()
        await callback.answer(TEXTS["template_deleted"], show_alert=True)
    except Exception as e:
        logging.error(f"Error in delete template callback: {e}")
        await callback.answer("❌ Error deleting template", show_alert=True)


async def list_recent(callback: CallbackQuery):
    videos = get_user_videos(callback.from_user.id)
    if not videos:
        await callback.answer(TEXTS["no_recent_videos"], show_alert=True)
        return
    for video in videos:
        await callback.message.answer_video(
            video=video["video_note_file_id"],
            caption="",
            reply_markup=main_kb(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("recent:delete"))
async def delete_recent(callback: CallbackQuery):
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            await callback.answer("Invalid callback data", show_alert=True)
            return
        video_id = int(parts[2])
        header_msg_id = int(parts[3]) if len(parts) > 3 else None
        video = get_video_by_id(video_id)
        if not video:
            await callback.answer("❌ Video not found", show_alert=True)
            return
        if video["channel_message_id"]:
            channel_msg_id = video["channel_message_id"]
            try:
                # Attempt to delete the video note message
                await callback.bot.delete_message(CHANNEL_ID, channel_msg_id)
                # Check if the next message is a text message and delete it
                next_msg = await callback.bot.get_message(
                    CHANNEL_ID, channel_msg_id + 1
                )
                if "💬" in next_msg.text:
                    await callback.bot.delete_message(CHANNEL_ID, channel_msg_id + 1)
            except Exception as e:
                logging.warning(f"Could not delete channel messages: {e}")
        delete_video(video_id)
        try:
            await callback.message.delete()
        except Exception as e:
            logging.warning(f"Could not delete message: {e}")
        remaining_videos = get_user_videos(callback.from_user.id)
        if not remaining_videos and header_msg_id:
            try:
                await callback.bot.delete_message(
                    chat_id=callback.message.chat.id, message_id=header_msg_id
                )
                await callback.bot.send_message(
                    chat_id=callback.message.chat.id,
                    text=TEXTS["no_recent_videos"],
                    reply_markup=main_kb(),
                )
            except Exception as e:
                logging.warning(f"Could not delete header message: {e}")
        await callback.answer(TEXTS["video_deleted"], show_alert=True)
    except Exception as e:
        logging.error(f"Error in delete recent callback: {e}")
        await callback.answer("❌ Error deleting video", show_alert=True)


async def inline_query_handler(inline_query: InlineQuery):
    user_id = inline_query.from_user.id
    query_text = inline_query.query.strip()
    results = []
    recent_videos = get_user_videos(user_id, limit=5)
    for idx, video in enumerate(recent_videos):
        caption = format_preview_text(
            text=video["text"] or "",
            caption=video["caption"] or "",
            effect=video["effect"] or "",
        )
        result = InlineQueryResultCachedMpeg4Gif(
            id=f"recent_{video['id']}",
            mpeg4_file_id=video["video_note_file_id"],
            title=f"Recent Video {idx + 1}",
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        results.append(result)
    if query_text:
        templates = get_user_templates(user_id)
        for idx, template in enumerate(templates):
            result = InlineQueryResultCachedMpeg4Gif(
                id=f"template_{idx}_{user_id}",
                mpeg4_file_id=template["video_file_id"],
                title=f"Template {idx + 1}",
                caption=query_text,
                input_message_content=InputTextMessageContent(
                    message_text=query_text,
                    parse_mode=ParseMode.HTML,
                ),
            )
            results.append(result)
    await inline_query.answer(
        results,
        cache_time=300,
        is_personal=True,
        switch_pm_text="Open bot",
        switch_pm_parameter="start",
    )


async def get_available_effects() -> dict:
    global AVAILABLE_EFFECTS
    try:
        client = TelegramClient("wiikotbot", int(API_ID), API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(PHONE_NUMBER)
            logging.warning("Please check your Telegram app for the code.")
            code = getpass.getpass("Enter the code you received: ")
            try:
                await client.sign_in(PHONE_NUMBER, code)
            except errors.SessionPasswordNeededError:
                password = TWO_FA_PASSWORD or getpass.getpass(
                    "Enter your 2FA password: "
                )
                await client.sign_in(password=password)
        async with client:
            available_effects = await client(GetAvailableEffectsRequest(hash=123))
            AVAILABLE_EFFECTS.clear()
            for effect in available_effects.effects:
                if hasattr(effect, "emoticon") and hasattr(effect, "id"):
                    AVAILABLE_EFFECTS[effect.id] = {
                        "emoticon": effect.emoticon,
                        "static_icon_id": effect.static_icon_id,
                        "effect_sticker_id": effect.effect_sticker_id,
                        "effect_animation_id": effect.effect_animation_id,
                        "premium_required": effect.premium_required,
                    }
            return AVAILABLE_EFFECTS
    except Exception as e:
        logging.error(f"Error getting available effects: {e}")
        return {}


async def handle_invalid_input(message: Message):
    await message.answer(ERRORS["invalid_input"], reply_markup=main_kb())


def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BUTTONS["create"])],
            [
                KeyboardButton(text=BUTTONS["template"]),
                KeyboardButton(text=BUTTONS["recent"]),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def create_kd(placeholder=None):
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BUTTONS["create:cancel"]),
                KeyboardButton(text=BUTTONS["create:done"]),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder=placeholder,
    )


def create_inline_kb():
    keyboard = [
        [InlineKeyboardButton(text=BUTTONS["create:text"], callback_data="create:text")],
        [InlineKeyboardButton(text=BUTTONS["create:caption"], callback_data="create:caption")],
        [InlineKeyboardButton(text=BUTTONS["create:effect"], callback_data="create:effect")],
        [InlineKeyboardButton(text="🔄 Change Video", callback_data="create:video")],
        [InlineKeyboardButton(text="🎵 Change Audio", callback_data="create:audio")],
        # Apply and Cancel buttons removed from inline
        # [InlineKeyboardButton(text=BUTTONS[\"create:apply\"], callback_data=\"create:apply\")],
        # [InlineKeyboardButton(text=BUTTONS[\"create:cancel\"], callback_data=\"create:cancel\")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# New Reply Keyboard for Apply/Cancel
def create_apply_cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BUTTONS["create:apply"]),
                KeyboardButton(text=BUTTONS["create:cancel"])
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True # Optional: hide after one use
    )


# New callback for saving as a template using the final video_note_id passed in callback data.
@router.callback_query(F.data.startswith("template_save"))
async def save_template(callback: CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split("|")
        if len(parts) < 2:
            await callback.answer("Invalid data", show_alert=True)
            return
        video_note_id = parts[1]
        add_template(callback.from_user.id, video_note_id)
        await callback.answer(SUCCESS["template_saved"], show_alert=True)
    except Exception as e:
        logging.error(f"Error saving template: {e}")
        await callback.answer("Error saving template", show_alert=True)


# --- New Callbacks for Video/Audio Change ---
@router.callback_query(CreateVideoNote.idle, F.data == "create:video")
async def ask_for_video(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Send the new video or video note.\nTip: You can use @Clips bot to find videos.")
    await state.set_state(CreateVideoNote.waiting_for_video)
    await callback.answer()

@router.callback_query(CreateVideoNote.idle, F.data == "create:audio")
async def ask_for_audio(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Send the new audio file.\nTip: You can use @Sounds bot to find audio.")
    await state.set_state(CreateVideoNote.waiting_for_audio)
    await callback.answer()

# --- Apply Changes Handler ---
@router.message(CreateVideoNote.idle, F.text == BUTTONS["create:apply"])
async def apply_changes(message: Message, state: FSMContext):
    data = await state.get_data()
    edit_video_id = data.get("edit_video_id")
    current_channel_msg_id = data.get("current_channel_msg_id")
    text_overlay_source_id = data.get("text_overlay_source_id")
    preview_message_id = data.get("preview_message_id") # ID of the message with inline keyboard

    if not edit_video_id or not current_channel_msg_id or not text_overlay_source_id:
        await message.answer(ERRORS["no_video_in_state"])
        # Potentially remove reply keyboard here if needed
        await state.clear()
        return

    # Use message.answer for progress
    progress_msg = await message.answer(TEXTS["processing_video_note"])
    final_processed_path = None

    try:
        # ... [Steps 1-5: Fetch data, determine source, delete old, send new, update DB - remain the same] ...
        # 1. Fetch final data from DB
        final_video_data = get_video_by_id(edit_video_id)
        if not final_video_data:
            raise Exception(f"Could not retrieve final video data for DB ID {edit_video_id}")
        final_text = final_video_data.get("text")
        final_caption = final_video_data.get("caption")
        final_effect = final_video_data.get("effect")
        final_duration = final_video_data.get("duration")

        # 2. Determine final video source
        video_source_for_final_send = None
        if final_text:
            final_processed_path = await add_text_to_video_file(
                message.bot, text_overlay_source_id, final_text
            )
            video_source_for_final_send = FSInputFile(final_processed_path)
        else:
            video_source_for_final_send = final_video_data.get("video_note_file_id")
        if not video_source_for_final_send:
            raise Exception("Could not determine video source for final channel send.")

        # 3. Delete old channel message
        try:
            await message.bot.delete_message(CHANNEL_ID, current_channel_msg_id)
        except Exception as e:
            logging.warning(f"Could not delete old channel message {current_channel_msg_id}: {e}")

        # 4. Send new video note to channel
        new_channel_message = await send_video_note_to_channel(
            message.bot, video_source_for_final_send, final_duration,
            message.from_user, final_caption, False, final_effect,
        )

        # 5. Update DB
        update_success_vid_id = update_video_note_field(
            edit_video_id, "video_note_file_id", new_channel_message.video_note.file_id
        )
        update_success_msg_id = update_video_note_field(
            edit_video_id, "channel_message_id", new_channel_message.message_id
        )
        if not update_success_vid_id or not update_success_msg_id:
            logging.error(f"Failed to update channel message/file ID in DB for {edit_video_id}")

        # 6. Send confirmation to user & Remove Reply Keyboard
        template_button_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=BUTTONS["create:template"],
                        callback_data=f"template_save|{new_channel_message.video_note.file_id}",
                    )
                ]
            ]
        )
        # Send confirmation as a new message, remove reply keyboard
        await message.answer(
            TEXTS["changes_applied"],
            reply_markup=ReplyKeyboardRemove() # Remove the Apply/Cancel keyboard
        )
        # Send the template button separately
        await message.answer("Save as template?", reply_markup=template_button_kb)

        # Optionally delete the old preview message
        if preview_message_id:
            try:
                await message.bot.delete_message(message.chat.id, preview_message_id)
            except Exception as e:
                logging.warning(f"Could not delete preview message {preview_message_id}: {e}")

    except Exception as e:
        logging.error(f"Error applying changes: {e}", exc_info=True)
        await message.answer(ERRORS["error_applying_changes"])
        # Keep reply keyboard on error?
    finally:
        if final_processed_path:
            cleanup_file(final_processed_path)
        if progress_msg: await progress_msg.delete()
        await state.clear()

# --- Cancel Handler ---
# Updated decorator to listen for message text and handle relevant states
@router.message(
    CreateVideoNote.idle,
    CreateVideoNote.waiting_for_text,
    CreateVideoNote.waiting_for_caption,
    CreateVideoNote.waiting_for_effect,
    CreateVideoNote.waiting_for_video,
    CreateVideoNote.waiting_for_audio,
    F.text == BUTTONS["create:cancel"]
)
async def cancel_creation(message: Message, state: FSMContext):
    data = await state.get_data()
    preview_msg_id = data.get("preview_message_id")

    # Optionally delete the preview message (inline keyboard one)
    if preview_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, preview_msg_id)
        except Exception as e:
            logging.warning(f"Could not delete preview message {preview_msg_id}: {e}")

    await state.clear()
    # Send confirmation and remove reply keyboard
    await message.answer(TEXTS["cancelled"], reply_markup=types.ReplyKeyboardRemove())


# Fallback handler for unexpected input during modification states
@router.message(CreateVideoNote.waiting_for_text)
@router.message(CreateVideoNote.waiting_for_caption)
@router.message(CreateVideoNote.waiting_for_effect)
async def invalid_modification_input(message: Message, state: FSMContext):
    await message.answer("Invalid input. Please provide the requested information or cancel.")

# ----- MAIN FUNCTION -----
async def main():
    initialize_db()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await get_available_effects()
    await load_default_templates(bot)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(router)

    dp.inline_query.register(inline_query_handler)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
