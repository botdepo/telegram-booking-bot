"""Бот записи на приём: салон, мастер, репетитор, консультации.

Клиент выбирает день и время из свободных окон и оставляет имя и телефон,
владелец получает заявку с кнопками «Подтвердить» / «Отменить», клиенту
приходит подтверждение и напоминание за 2 часа до визита.

Настраивать ничего не нужно: кто первым напишет боту /start — тот
владелец. Рабочие дни и часы владелец меняет командами прямо в чате
(/days, /hours, /step); по умолчанию пн–пт, 10:00–19:00, по часу.

Токен бот получает из переменной окружения BOT_TOKEN — на botdepo её
подставляет панель, локально задайте сами (см. README).
"""
import asyncio
import logging
import os
from datetime import date, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import schedule
import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DAYS_AHEAD = 14  # на сколько дней вперёд можно записаться
REMIND_BEFORE = timedelta(hours=2)

dp = Dispatcher()


async def is_owner(event: Message | CallbackQuery) -> bool:
    # async, а не F.func: синхронные фильтры aiogram гоняет в другом потоке, а SQLite так не умеет
    return event.from_user.id == storage.owner_id()


# Кто выбрал время и сейчас пишет имя и телефон: user_id → slot.
# В памяти: после перезапуска бота клиенту просто придётся выбрать время заново.
waiting_contact: dict[int, str] = {}


def work_hours() -> tuple[int, int]:
    return schedule.parse_hours(storage.get("hours", "10-19"))


def work_days() -> set[int]:
    return schedule.parse_days(storage.get("days", "пн-пт"))


def step() -> int:
    return int(storage.get("step", "60"))


def settings_text() -> str:
    start, end = work_hours()
    return (
        f"Дни: {schedule.fmt_days(work_days())}\n"
        f"Часы: {schedule.fmt_minutes(start)}–{schedule.fmt_minutes(end)}, запись по {step()} мин."
    )


def free_slots(day: date) -> list[str]:
    if day.weekday() not in work_days():
        return []
    start, end = work_hours()
    taken = storage.taken_slots(day.isoformat())
    slots = schedule.day_slots(day, start, end, step(), schedule.now_local())
    return [t for t in slots if f"{day.isoformat()} {t}" not in taken]


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Записаться", callback_data="book")],
        [InlineKeyboardButton(text="Мои записи", callback_data="my")],
    ])


@dp.message(CommandStart())
async def start(message: Message) -> None:
    waiting_contact.pop(message.from_user.id, None)
    if storage.claim_owner(message.from_user.id):
        await message.answer(
            "Вы владелец этого бота. Заявки клиентов будут приходить сюда.\n\n"
            f"{settings_text()}\n\n"
            "Поменять расписание:\n"
            "/days пн-сб — рабочие дни (можно «пн,ср,пт»)\n"
            "/hours 9-18 — часы (можно «9:30-18:00»)\n"
            "/step 30 — длительность записи в минутах\n"
            "/welcome Текст — приветствие для клиентов\n\n"
            "/today и /week — записи на сегодня и на неделю.\n"
            "Попробуйте записаться сами кнопкой ниже.",
            reply_markup=main_menu(),
        )
        return
    welcome = storage.get("welcome", "Здравствуйте! Здесь можно записаться на приём.")
    await message.answer(welcome, reply_markup=main_menu())


# --- настройки владельца ---


@dp.message(Command("days"), is_owner)
async def set_days(message: Message, command: CommandObject) -> None:
    if not command.args or schedule.parse_days(command.args) is None:
        await message.answer("Например: /days пн-пт или /days пн,ср,пт")
        return
    storage.put("days", command.args)
    await message.answer("Готово.\n" + settings_text())


@dp.message(Command("hours"), is_owner)
async def set_hours(message: Message, command: CommandObject) -> None:
    if not command.args or schedule.parse_hours(command.args) is None:
        await message.answer("Например: /hours 10-19 или /hours 9:30-18:00")
        return
    storage.put("hours", command.args)
    await message.answer("Готово.\n" + settings_text())


@dp.message(Command("step"), is_owner)
async def set_step(message: Message, command: CommandObject) -> None:
    if not command.args or not command.args.strip().isdigit() or not 10 <= int(command.args) <= 480:
        await message.answer("Укажите длительность в минутах, от 10 до 480. Например: /step 30")
        return
    storage.put("step", command.args.strip())
    await message.answer("Готово.\n" + settings_text())


@dp.message(Command("welcome"), is_owner)
async def set_welcome(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Например: /welcome Здравствуйте! Это запись в студию маникюра «Лана».")
        return
    storage.put("welcome", command.args)
    await message.answer("Приветствие обновил.")


def bookings_text(rows: list[storage.Booking], empty: str) -> str:
    if not rows:
        return empty
    marks = {"new": "🕓", "confirmed": "✅"}
    return "\n".join(f"{marks.get(s, '')} {schedule.fmt_slot(slot)} — {name} {phone}".rstrip() for _, _, slot, name, phone, s in rows)


@dp.message(Command("today"), is_owner)
async def today(message: Message) -> None:
    now = schedule.now_local()
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    rows = storage.upcoming(now.strftime("%Y-%m-%d %H:%M"), until=tomorrow)
    await message.answer(bookings_text(rows, "На сегодня записей больше нет."))


@dp.message(Command("week"), is_owner)
async def week(message: Message) -> None:
    now = schedule.now_local()
    until = (now.date() + timedelta(days=7)).isoformat()
    rows = storage.upcoming(now.strftime("%Y-%m-%d %H:%M"), until=until)
    await message.answer(bookings_text(rows, "На ближайшую неделю записей нет."))


# --- запись клиента ---


@dp.callback_query(F.data == "book")
async def pick_day(callback: CallbackQuery) -> None:
    today_ = schedule.now_local().date()
    days = [today_ + timedelta(days=i) for i in range(DAYS_AHEAD)]
    buttons = [
        InlineKeyboardButton(text=schedule.fmt_day(d), callback_data=f"day:{d.isoformat()}")
        for d in days if free_slots(d)
    ]
    if not buttons:
        await callback.message.answer("Свободных окон на ближайшие две недели нет. Загляните позже.")
        await callback.answer()
        return
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    await callback.message.answer("Выберите день:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@dp.callback_query(F.data.startswith("day:"))
async def pick_time(callback: CallbackQuery) -> None:
    day = date.fromisoformat(callback.data.split(":", 1)[1])
    slots = free_slots(day)
    if not slots:
        await callback.answer("На этот день окна уже разобрали", show_alert=True)
        return
    buttons = [InlineKeyboardButton(text=t, callback_data=f"time:{day.isoformat()} {t}") for t in slots]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton(text="← Другой день", callback_data="book")])
    await callback.message.edit_text(
        f"{schedule.fmt_day(day)}. Выберите время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("time:"))
async def ask_contact(callback: CallbackQuery) -> None:
    slot = callback.data.split(":", 1)[1]
    waiting_contact[callback.from_user.id] = slot
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await callback.message.edit_text(f"Вы выбрали {schedule.fmt_slot(slot)}.")
    await callback.message.answer(
        "Как к вам обращаться и как связаться? Нажмите кнопку, чтобы отправить номер, "
        "или напишите имя и телефон одним сообщением.",
        reply_markup=keyboard,
    )
    await callback.answer()


@dp.message(F.chat.type == "private", F.from_user.id.in_(waiting_contact), F.contact | F.text)
async def take_contact(message: Message, bot: Bot) -> None:
    if message.text and message.text.startswith("/"):
        waiting_contact.pop(message.from_user.id, None)
        await message.answer("Запись отменена. /start — начать заново.", reply_markup=ReplyKeyboardRemove())
        return
    slot = waiting_contact.pop(message.from_user.id)
    if message.contact:
        name, phone = message.contact.first_name, message.contact.phone_number
    else:
        name, phone = message.text.strip()[:200], ""

    booking_id = storage.book(message.from_user.id, slot, name, phone)
    if booking_id is None:
        await message.answer("Это время только что заняли. Выберите другое.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Записаться:", reply_markup=main_menu())
        return
    await message.answer(
        f"Заявка на {schedule.fmt_slot(slot)} отправлена. Напишу, когда её подтвердят.",
        reply_markup=ReplyKeyboardRemove(),
    )
    owner = storage.owner_id()
    username = f" (@{message.from_user.username})" if message.from_user.username else ""
    await bot.send_message(
        owner,
        f"Новая запись: {schedule.fmt_slot(slot)}\n{name} {phone}{username}".rstrip(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"ok:{booking_id}"),
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"no:{booking_id}"),
        ]]),
    )


async def tell(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError:
        pass  # клиент заблокировал бота — ничего страшного


@dp.callback_query(F.data.regexp(r"^(ok|no):\d+$"), is_owner)
async def owner_decides(callback: CallbackQuery, bot: Bot) -> None:
    action, raw_id = callback.data.split(":")
    booking = storage.get_booking(int(raw_id))
    if booking is None or booking[5] == "cancelled":
        await callback.answer("Эта запись уже отменена")
        return
    _, user_id, slot, _, _, _ = booking
    if action == "ok":
        storage.set_status(booking[0], "confirmed")
        # до визита меньше двух часов — отдельное напоминание будет лишним
        if slot <(schedule.now_local() + REMIND_BEFORE).strftime("%Y-%m-%d %H:%M"):
            storage.mark_reminded(booking[0])
        await tell(bot, user_id, f"Запись подтверждена: {schedule.fmt_slot(slot)}. Ждём вас!")
        verdict = "✅ Подтверждена"
    else:
        storage.set_status(booking[0], "cancelled")
        await tell(bot, user_id, f"К сожалению, запись на {schedule.fmt_slot(slot)} отменили. /start — выбрать другое время.")
        verdict = "❌ Отменена"
    await callback.message.edit_text(f"{callback.message.text}\n\n{verdict}")
    await callback.answer(verdict)


@dp.callback_query(F.data == "my")
async def my_bookings(callback: CallbackQuery) -> None:
    rows = storage.upcoming(schedule.now_local().strftime("%Y-%m-%d %H:%M"), user_id=callback.from_user.id)
    if not rows:
        await callback.message.answer("У вас нет предстоящих записей.", reply_markup=main_menu())
        await callback.answer()
        return
    buttons = [
        [InlineKeyboardButton(text=f"Отменить {schedule.fmt_slot(slot)}", callback_data=f"drop:{bid}")]
        for bid, _, slot, _, _, _ in rows
    ]
    await callback.message.answer(
        bookings_text(rows, ""), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^drop:\d+$"))
async def client_cancels(callback: CallbackQuery, bot: Bot) -> None:
    booking = storage.get_booking(int(callback.data.split(":")[1]))
    # user_id в проверке — чтобы нельзя было отменить чужую запись, подставив id
    if booking is None or booking[1] != callback.from_user.id or booking[5] == "cancelled":
        await callback.answer("Эта запись уже отменена")
        return
    storage.set_status(booking[0], "cancelled")
    await callback.message.edit_text(f"Запись на {schedule.fmt_slot(booking[2])} отменена.")
    await callback.answer()
    await tell(bot, storage.owner_id(), f"Клиент отменил запись: {schedule.fmt_slot(booking[2])}, {booking[3]} {booking[4]}".rstrip())


@dp.message(F.chat.type == "private")
async def fallback(message: Message) -> None:
    await message.answer("Записаться или посмотреть свои записи — кнопками ниже.", reply_markup=main_menu())


async def remind_loop(bot: Bot) -> None:
    fmt = "%Y-%m-%d %H:%M"
    while True:
        try:
            now = schedule.now_local()
            for bid, user_id, slot, _, _, _ in storage.due_reminders(now.strftime(fmt), (now + REMIND_BEFORE).strftime(fmt)):
                await tell(bot, user_id, f"Напоминаем: вы записаны на {schedule.fmt_slot(slot)}.")
                storage.mark_reminded(bid)
        except Exception:
            # сбой одной проверки (сеть, Telegram) не должен навсегда выключать напоминания
            logging.exception("напоминания: ошибка, попробую через минуту")
        await asyncio.sleep(60)


async def main() -> None:
    bot = Bot(token=os.environ["BOT_TOKEN"])
    me = await bot.get_me()
    logging.info("бот запущен: @%s", me.username)
    if storage.owner_id() is None:
        logging.info("владельца пока нет: напишите боту /start первым — станете владельцем")
    reminders = asyncio.create_task(remind_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        reminders.cancel()


if __name__ == "__main__":
    asyncio.run(main())
