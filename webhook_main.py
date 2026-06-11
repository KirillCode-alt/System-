import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv

from bot import router


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
WEBHOOK_DROP_PENDING_UPDATES = os.getenv("WEBHOOK_DROP_PENDING_UPDATES", "true").lower() in {"1", "true", "yes", "y"}
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))


async def health_handler(request: web.Request) -> web.Response:
    return web.Response(text="OK")


def build_bot() -> Bot:
    if not BOT_TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN. Добавь переменную окружения BOT_TOKEN в Render.")
    return Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp


async def on_startup(bot: Bot) -> None:
    if not WEBHOOK_BASE_URL:
        raise RuntimeError("Не найден WEBHOOK_BASE_URL. В Render укажи URL сервиса, например https://system-bot.onrender.com")

    webhook_url = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(
        webhook_url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=WEBHOOK_DROP_PENDING_UPDATES,
    )
    logging.info("Webhook установлен: %s", webhook_url)


async def on_shutdown(bot: Bot) -> None:
    session = await bot.get_session()
    await session.close()


def run_webhook() -> None:
    bot = build_bot()
    dp = build_dispatcher()
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    web.run_app(app, host=HOST, port=PORT)


async def run_polling() -> None:
    bot = build_bot()
    dp = build_dispatcher()
    await bot.delete_webhook(drop_pending_updates=WEBHOOK_DROP_PENDING_UPDATES)
    await dp.start_polling(bot)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    if WEBHOOK_BASE_URL:
        run_webhook()
    else:
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
