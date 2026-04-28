import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# Как добавить новый преобразователь:
# 1. Положи новый *.json файл в папку data/
# 2. Внутри файла используй структуру {"drives": {"your_drive_key": {...}}}
# 3. Бот сам подхватит новый преобразователь и покажет его в меню
# 4. Примеры ввода кода для каждого преобразователя определяются автоматически
#    по реальным кодам в разделе Alarm/Fault.
# 5. При необходимости можно явно задать в category поля:
#    "example_full": "A01006"
#    "example_digits": "1006"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

router = Router()


class LookupStates(StatesGroup):
    waiting_for_code = State()


class ErrorCatalog:
    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.drives = data.get("drives", {})

    @classmethod
    def from_directory(cls, path: Path) -> "ErrorCatalog":
        merged: dict[str, Any] = {"drives": {}}

        if not path.exists():
            return cls(merged)

        for json_path in sorted(path.glob("*.json")):
            with json_path.open("r", encoding="utf-8") as f:
                chunk = json.load(f)

            for drive_key, drive_payload in chunk.get("drives", {}).items():
                if drive_key not in merged["drives"]:
                    merged["drives"][drive_key] = drive_payload
                    continue

                target_drive = merged["drives"][drive_key]
                if drive_payload.get("name"):
                    target_drive["name"] = drive_payload["name"]

                target_categories = target_drive.setdefault("categories", {})
                source_categories = drive_payload.get("categories", {})

                for category_key, category_payload in source_categories.items():
                    if category_key not in target_categories:
                        target_categories[category_key] = category_payload
                        continue

                    target_category = target_categories[category_key]
                    if category_payload.get("name"):
                        target_category["name"] = category_payload["name"]
                    if category_payload.get("prefix"):
                        target_category["prefix"] = category_payload["prefix"]
                    if category_payload.get("example_full"):
                        target_category["example_full"] = category_payload["example_full"]
                    if category_payload.get("example_digits"):
                        target_category["example_digits"] = category_payload["example_digits"]

                    target_items = target_category.setdefault("items", {})
                    target_items.update(category_payload.get("items", {}))

        return cls(merged)

    def drive_exists(self, drive_key: str) -> bool:
        return drive_key in self.drives

    def get_drive_name(self, drive_key: str) -> str:
        return self.drives[drive_key]["name"]

    def get_categories(self, drive_key: str) -> dict[str, Any]:
        return self.drives[drive_key].get("categories", {})

    def get_category(self, drive_key: str, category_key: str) -> dict[str, Any]:
        return self.get_categories(drive_key).get(category_key, {})

    def get_category_name(self, drive_key: str, category_key: str) -> str:
        return self.get_category(drive_key, category_key).get("name", category_key)

    def get_category_prefix(self, drive_key: str, category_key: str) -> str:
        category = self.get_category(drive_key, category_key)
        prefix = category.get("prefix")
        if prefix:
            return str(prefix).upper()
        return "A" if category_key == "alarm" else "F"

    def get_code_examples(self, drive_key: str, category_key: str) -> tuple[str, str]:
        category = self.get_category(drive_key, category_key)

        explicit_full = category.get("example_full")
        explicit_digits = category.get("example_digits")
        if explicit_full and explicit_digits:
            return str(explicit_full), str(explicit_digits)

        items = category.get("items", {})
        if items:
            first_code = next(iter(items.keys()))
            normalized_full = self.normalize_code(first_code)
            digits = self.normalize_digits(first_code)
            if normalized_full and digits:
                return normalized_full, digits

        prefix = self.get_category_prefix(drive_key, category_key)
        return f"{prefix}001", "1"

    def lookup(self, drive_key: str, category_key: str, code: str) -> dict[str, Any] | None:
        category = self.get_category(drive_key, category_key)
        items = category.get("items", {})
        expected_prefix = self.get_category_prefix(drive_key, category_key)

        normalized_input = self.normalize_code(code)
        digits_input = self.normalize_digits(code)
        prefixed_digits_input = f"{expected_prefix}{digits_input}" if digits_input else ""

        for raw_code, payload in items.items():
            normalized_raw = self.normalize_code(raw_code)
            digits_raw = self.normalize_digits(raw_code)

            is_match = any(
                candidate and candidate == normalized_raw
                for candidate in (normalized_input, prefixed_digits_input)
            ) or (digits_input and digits_input == digits_raw)

            if is_match:
                result = dict(payload)
                result["code"] = raw_code
                return result
        return None

    @staticmethod
    def normalize_code(code: str) -> str:
        return code.strip().upper().replace(" ", "").replace("-", "")

    @staticmethod
    def normalize_digits(code: str) -> str:
        digits = re.sub(r"\D", "", code)
        if not digits:
            return ""
        return digits.lstrip("0") or "0"


catalog = ErrorCatalog.from_directory(DATA_DIR)


def drives_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for drive_key, drive in catalog.drives.items():
        builder.button(text=drive["name"], callback_data=f"drive:{drive_key}")
    builder.adjust(1)
    return builder.as_markup()



def categories_keyboard(drive_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category_key, category in catalog.get_categories(drive_key).items():
        builder.button(text=category["name"], callback_data=f"category:{drive_key}:{category_key}")
    builder.button(text="⬅️ Назад к выбору преобразователя", callback_data="menu:drives")
    builder.adjust(2, 1)
    return builder.as_markup()



def result_keyboard(drive_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔁 Ввести другой код", callback_data="repeat")
    builder.button(text="📁 Сменить Alarm/Fault", callback_data=f"change_category:{drive_key}")
    builder.button(text="🏭 Сменить преобразователь", callback_data="menu:drives")
    builder.adjust(1, 1, 1)
    return builder.as_markup()



def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")



def render_result(drive_name: str, category_name: str, item: dict[str, Any]) -> str:
    description = escape_html(item.get("description", "Нет описания"))
    cause = escape_html(item.get("cause", "Не указана"))
    action = escape_html(item.get("action", "Не указано"))
    note = item.get("note")
    note_block = f"\n<b>Примечание:</b> {escape_html(note)}" if note else ""

    return (
        f"<b>{escape_html(drive_name)}</b>\n"
        f"<b>Тип:</b> {escape_html(category_name)}\n"
        f"<b>Код:</b> {escape_html(item['code'])}\n\n"
        f"<b>Описание:</b> {description}\n"
        f"<b>Причина:</b> {cause}\n"
        f"<b>Что делать:</b> {action}"
        f"{note_block}"
    )



def render_not_found(
    drive_name: str,
    category_name: str,
    user_code: str,
    example_full: str,
    example_digits: str,
) -> str:
    return (
        f"Для <b>{escape_html(drive_name)}</b> в разделе <b>{escape_html(category_name)}</b> "
        f"код <b>{escape_html(user_code)}</b> не найден.\n\n"
        f"Можно вводить как полный код, так и только цифры.\n"
        f"Примеры: <code>{escape_html(example_full)}</code> или <code>{escape_html(example_digits)}</code>."
    )


async def show_main_menu(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "<b>Справочник ошибок преобразователей</b>\n\n"
        "1. Выбери преобразователь\n"
        "2. Выбери тип события: Alarm или Fault\n"
        "3. Введи код вручную\n\n"
    )

    if isinstance(target, Message):
        await target.answer(text, reply_markup=drives_keyboard())
    else:
        await target.message.edit_text(text, reply_markup=drives_keyboard())
        await target.answer()


@router.message(Command("start", "menu"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await show_main_menu(message, state)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Текущее действие отменено. Используй /start, чтобы начать заново.")


@router.callback_query(F.data == "menu:drives")
async def cb_menu_drives(callback: CallbackQuery, state: FSMContext) -> None:
    await show_main_menu(callback, state)


@router.callback_query(F.data.startswith("drive:"))
async def cb_select_drive(callback: CallbackQuery, state: FSMContext) -> None:
    drive_key = callback.data.split(":", maxsplit=1)[1]
    if not catalog.drive_exists(drive_key):
        await callback.answer("Такой преобразователь не найден", show_alert=True)
        return

    await state.update_data(drive_key=drive_key)
    await callback.message.edit_text(
        f"Выбран преобразователь: <b>{escape_html(catalog.get_drive_name(drive_key))}</b>\n\nТеперь выбери тип события:",
        reply_markup=categories_keyboard(drive_key),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("change_category:"))
async def cb_change_category(callback: CallbackQuery, state: FSMContext) -> None:
    drive_key = callback.data.split(":", maxsplit=1)[1]
    if not catalog.drive_exists(drive_key):
        await callback.answer("Не удалось открыть список категорий", show_alert=True)
        return

    await state.update_data(drive_key=drive_key)
    await state.set_state(None)
    await callback.message.edit_text(
        f"Преобразователь: <b>{escape_html(catalog.get_drive_name(drive_key))}</b>\n\nВыбери тип события:",
        reply_markup=categories_keyboard(drive_key),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("category:"))
async def cb_select_category(callback: CallbackQuery, state: FSMContext) -> None:
    _, drive_key, category_key = callback.data.split(":", maxsplit=2)

    if not catalog.drive_exists(drive_key):
        await callback.answer("Преобразователь не найден", show_alert=True)
        return

    categories = catalog.get_categories(drive_key)
    if category_key not in categories:
        await callback.answer("Категория не найдена", show_alert=True)
        return

    await state.update_data(drive_key=drive_key, category_key=category_key)
    await state.set_state(LookupStates.waiting_for_code)

    drive_name = catalog.get_drive_name(drive_key)
    category_name = catalog.get_category_name(drive_key, category_key)
    example_full, example_digits = catalog.get_code_examples(drive_key, category_key)
    await callback.message.edit_text(
        f"Преобразователь: <b>{escape_html(drive_name)}</b>\n"
        f"Тип: <b>{escape_html(category_name)}</b>\n\n"
        f"Теперь отправь код сообщением.\n"
        f"Можно ввести полный код: <code>{escape_html(example_full)}</code>\n"
        f"Или только цифры: <code>{escape_html(example_digits)}</code>",
        reply_markup=result_keyboard(drive_key),
    )
    await callback.answer()


@router.callback_query(F.data == "repeat")
async def cb_repeat(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    drive_key = data.get("drive_key")
    category_key = data.get("category_key")

    if not drive_key or not category_key:
        await callback.answer("Сначала выбери преобразователь и тип события", show_alert=True)
        return

    drive_name = catalog.get_drive_name(drive_key)
    category_name = catalog.get_category_name(drive_key, category_key)
    example_full, example_digits = catalog.get_code_examples(drive_key, category_key)
    await state.set_state(LookupStates.waiting_for_code)
    await callback.message.answer(
        f"Преобразователь: <b>{escape_html(drive_name)}</b>\n"
        f"Тип: <b>{escape_html(category_name)}</b>\n\n"
        f"Введи следующий код. Например: <code>{escape_html(example_full)}</code> или <code>{escape_html(example_digits)}</code>",
    )
    await callback.answer()


@router.message(LookupStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    drive_key = data.get("drive_key")
    category_key = data.get("category_key")

    if not drive_key or not category_key:
        await state.clear()
        await message.answer("Сессия сброшена. Нажми /start и начни заново.")
        return

    user_code = (message.text or "").strip()
    example_full, example_digits = catalog.get_code_examples(drive_key, category_key)
    if not user_code:
        await message.answer(
            f"Отправь текстовый код ошибки, например <code>{escape_html(example_full)}</code> или <code>{escape_html(example_digits)}</code>."
        )
        return

    drive_name = catalog.get_drive_name(drive_key)
    category_name = catalog.get_category_name(drive_key, category_key)
    result = catalog.lookup(drive_key, category_key, user_code)

    if result:
        await message.answer(
            render_result(drive_name, category_name, result),
            reply_markup=result_keyboard(drive_key),
        )
        return

    await message.answer(
        render_not_found(drive_name, category_name, user_code, example_full, example_digits),
        reply_markup=result_keyboard(drive_key),
    )


@router.message()
async def fallback_handler(message: Message) -> None:
    await message.answer("Используй /start, чтобы открыть меню справочника ошибок преобразователей.")


async def main() -> None:
    if not TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN. Создай .env файл и добавь токен бота.")

    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
