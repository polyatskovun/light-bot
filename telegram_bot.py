import asyncio
import logging
from datetime import datetime, time as dt_time, timedelta
from telegram import Bot
from telegram.error import TelegramError
import sys
import os
import json
from dotenv import load_dotenv

# Завантажуємо змінні з .env файлу
load_dotenv()

# Додаємо поточну директорію до шляху
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_api import fetch_schedule_from_api

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# НАЛАШТУВАННЯ БОТА
# ============================================
# Читаємо приватні дані з .env файлу
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# Публічні налаштування
REGION = 'kyiv'
QUEUE = '2.2'
UPDATE_INTERVAL_MINUTES = 15
MORNING_NOTIFICATION_HOUR = 8  # Ранкове повідомлення (графік на сьогодні)
EVENING_NOTIFICATION_HOUR = 20  # Вечірнє повідомлення (графік на завтра)
WARNING_MINUTES_BEFORE = 15  

def format_schedule_for_telegram(data, queue, target_date=None, is_tomorrow=False):
    """Форматує графік для Telegram повідомлення
    
    Args:
        data: дані з API
        queue: номер групи
        target_date: конкретна дата для відображення (YYYY-MM-DD), якщо None - всі дати
        is_tomorrow: чи це графік на завтра
    """
    
    if not isinstance(data, dict) or 'regions' not in data:
        return '⚠️ Помилка: дані не в очікуваному форматі'
    
    kyiv_data = None
    for region in data['regions']:
        if region.get('cpu') == 'kyiv':
            kyiv_data = region
            break
    
    if not kyiv_data or not kyiv_data.get('schedule'):
        return '⚠️ Дані для Києва не знайдено'
    
    schedule = kyiv_data['schedule']
    
    if queue not in schedule:
        return f'⚠️ Група {queue} не знайдена'
    
    group_schedule = schedule[queue]
    
    # Якщо вказана конкретна дата - фільтруємо
    if target_date:
        if target_date not in group_schedule:
            return f'⚠️ Графік на {target_date} не знайдено'
        group_schedule = {target_date: group_schedule[target_date]}
    
    # Формуємо повідомлення
    if is_tomorrow:
        message = f'🌙 *ГРАФІК НА ЗАВТРА*\n'
    else:
        message = f'⚡ *ГРАФІК ВІДКЛЮЧЕНЬ*\n'
    
    message += f'📍 Київ, Група {queue}\n'
    message += f'🕐 Оновлено: {datetime.now().strftime("%d.%m.%Y %H:%M")}\n'
    message += f'{"─" * 30}\n\n'
    
    for date, times in group_schedule.items():
        message += f'📅 *{date}*\n'
        
        # Групуємо інтервали
        current_status = None
        start_time = None
        
        sorted_times = sorted(times.items())
        
        for time, status in sorted_times:
            status_emoji = {
                0: '❓',
                1: '💡',
                2: '⚠️'
            }.get(status, '❔')
            
            status_text = {
                0: 'Дані недоступні',
                1: 'Світло Є',
                2: 'Можливе відключення'
            }.get(status, 'Невідомо')
            
            if status != current_status:
                if current_status is not None and start_time is not None:
                    message += f'{prev_emoji} `{start_time} - {time}` {prev_text}\n'
                start_time = time
                current_status = status
                prev_emoji = status_emoji
                prev_text = status_text
        
        # Додаємо останній інтервал
        if start_time:
            message += f'{prev_emoji} `{start_time} - 24:00` {prev_text}\n'
        
        message += '\n'
    
    return message


def get_today_schedule_data(data, queue):
    """Витягує тільки сьогоднішній графік для порівняння"""
    today = datetime.now().strftime('%Y-%m-%d')
    
    if not isinstance(data, dict) or 'regions' not in data:
        return None
    
    for region in data['regions']:
        if region.get('cpu') == 'kyiv':
            schedule = region.get('schedule', {})
            if queue in schedule:
                return schedule[queue].get(today, {})
    
    return None


def get_upcoming_outages(data, queue, minutes_ahead):
    """Знаходить майбутні відключення протягом наступних N хвилин"""
    today = datetime.now().strftime('%Y-%m-%d')
    current_time = datetime.now()
    future_time = current_time + timedelta(minutes=minutes_ahead)
    
    if not isinstance(data, dict) or 'regions' not in data:
        return []
    
    for region in data['regions']:
        if region.get('cpu') == 'kyiv':
            schedule = region.get('schedule', {})
            if queue in schedule:
                today_schedule = schedule[queue].get(today, {})
                
                outages = []
                for time_str, status in today_schedule.items():
                    # Статус 2 = можливе відключення
                    if status == 2:
                        try:
                            # Парсимо час (формат "HH:MM")
                            hour, minute = map(int, time_str.split(':'))
                            schedule_time = current_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
                            
                            # Перевіряємо чи відключення в потрібному діапазоні
                            if current_time < schedule_time <= future_time:
                                minutes_until = int((schedule_time - current_time).total_seconds() / 60)
                                outages.append({
                                    'time': time_str,
                                    'minutes_until': minutes_until,
                                    'datetime': schedule_time
                                })
                        except:
                            pass
                
                return outages
    
    return []


async def check_and_send_warnings(bot, chat_id, region, queue, warning_minutes):
    """Перевіряє та відправляє попередження про майбутні відключення"""
    
    try:
        # Отримуємо дані з API
        data = fetch_schedule_from_api(region, queue)
        
        if not data:
            return
        
        # Шукаємо відключення в найближчі warning_minutes хвилин
        upcoming = get_upcoming_outages(data, queue, warning_minutes)
        
        if not upcoming:
            return
        
        # Перевіряємо, чи вже відправляли попередження для цього відключення
        if not hasattr(check_and_send_warnings, 'warned_times'):
            check_and_send_warnings.warned_times = set()
        
        for outage in upcoming:
            time_key = outage['time']
            
            # Якщо ще не попереджали про це відключення
            if time_key not in check_and_send_warnings.warned_times:
                minutes = outage['minutes_until']
                
                message = (
                    f"⚠️ *ПОПЕРЕДЖЕННЯ ПРО ВІДКЛЮЧЕННЯ*\n\n"
                    f"🕐 Через *{minutes} хвилин* ({outage['time']}) очікується можливе відключення світла!\n\n"
                    f"📍 Київ, Група {queue}"
                )
                
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode='Markdown'
                )
                
                logger.info(f'⚠️ Відправлено попередження про відключення о {outage["time"]} (через {minutes} хв)')
                
                # Позначаємо, що про це відключення вже попередили
                check_and_send_warnings.warned_times.add(time_key)
        
        # Очищаємо старі попередження (час вже пройшов)
        current_time_str = datetime.now().strftime('%H:%M')
        check_and_send_warnings.warned_times = {
            t for t in check_and_send_warnings.warned_times 
            if t >= current_time_str
        }
        
    except Exception as e:
        logger.error(f'❌ Помилка перевірки попереджень: {e}')


async def send_tomorrow_schedule(bot, chat_id, region, queue):
    """Відправляє графік на завтра (увечері)"""
    
    try:
        logger.info('🌙 Відправка графіку на завтра...')
        
        # Отримуємо дані з API
        data = fetch_schedule_from_api(region, queue)
        
        if not data:
            logger.warning('❌ Не вдалося отримати графік')
            return False
        
        # Визначаємо дату завтрашнього дня
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Перевіряємо чи є графік на завтра
        if not isinstance(data, dict) or 'regions' not in data:
            return False
        
        for region_data in data['regions']:
            if region_data.get('cpu') == 'kyiv':
                schedule = region_data.get('schedule', {})
                if queue in schedule and tomorrow in schedule[queue]:
                    # Є графік на завтра - відправляємо
                    message = format_schedule_for_telegram(data, queue, target_date=tomorrow, is_tomorrow=True)
                    
                    await bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                    
                    logger.info('✅ Графік на завтра відправлено')
                    return True
        
        logger.info('ℹ️ Графік на завтра ще не доступний')
        return False
        
    except TelegramError as e:
        logger.error(f'❌ Помилка Telegram: {e}')
        return False
    except Exception as e:
        logger.error(f'❌ Помилка відправки графіку на завтра: {e}')
        return False


async def send_schedule_update(bot, chat_id, region, queue, force=False):
    """Відправляє оновлення графіку у Telegram (тільки якщо змінився)"""
    
    try:
        logger.info(f'Перевірка графіку для {region}, група {queue}...')
        
        # Отримуємо дані з API
        data = fetch_schedule_from_api(region, queue)
        
        if not data:
            logger.warning('❌ Не вдалося отримати графік')
            return None
        
        # Отримуємо тільки сьогоднішній графік
        today_schedule = get_today_schedule_data(data, queue)
        
        if today_schedule is None:
            logger.warning('⚠️ Графік на сьогодні не знайдено')
            return None
        
        # Перетворюємо в JSON для порівняння
        schedule_hash = json.dumps(today_schedule, sort_keys=True)
        
        # Якщо не примусова відправка - перевіряємо зміни
        if not force:
            if hasattr(send_schedule_update, 'last_schedule'):
                if send_schedule_update.last_schedule == schedule_hash:
                    logger.info('✓ Графік не змінився, відправка не потрібна')
                    return schedule_hash
                else:
                    logger.info('🔄 ГРАФІК ЗМІНИВСЯ! Відправляю оновлення...')
            else:
                logger.info('📊 Перша перевірка, відправляю графік...')
        else:
            logger.info('📅 Ранкове повідомлення о 8:00')
        
        # Форматуємо повідомлення
        message = format_schedule_for_telegram(data, queue)
        
        # Відправляємо повідомлення
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode='Markdown'
        )
        
        logger.info('✅ Повідомлення відправлено успішно')
        
        return schedule_hash
        
    except TelegramError as e:
        logger.error(f'❌ Помилка Telegram: {e}')
        return None
    except Exception as e:
        logger.error(f'❌ Загальна помилка: {e}')
        return None


async def get_chat_id_from_updates(bot):
    """Отримує Chat ID з останніх повідомлень боту"""
    try:
        updates = await bot.get_updates(limit=1)
        if updates:
            chat_id = updates[0].message.chat.id
            logger.info(f'✅ Chat ID знайдено: {chat_id}')
            return str(chat_id)
    except Exception as e:
        logger.error(f'❌ Помилка отримання Chat ID: {e}')
    return None


async def monitor_and_send(bot_token, chat_id, region, queue, interval_minutes, morning_hour, evening_hour, warning_minutes):
    """Головна функція моніторингу з відправкою у Telegram"""
    
    logger.info('=' * 60)
    logger.info('🤖 TELEGRAM БОТ - МОНІТОРИНГ ГРАФІКУ ВІДКЛЮЧЕНЬ')
    logger.info('=' * 60)
    logger.info(f'📍 Регіон: {region}')
    logger.info(f'🔢 Група: {queue}')
    logger.info(f'⏱️  Інтервал перевірки: {interval_minutes} хвилин')
    logger.info(f'🌅 Ранкове повідомлення: {morning_hour}:00 (графік на сьогодні)')
    logger.info(f'🌙 Вечірнє повідомлення: {evening_hour}:00 (графік на завтра)')
    logger.info(f'⚠️  Попередження: за {warning_minutes} хв до відключення')
    logger.info(f'📊 Відправка: тільки при змінах або за розкладом')
    logger.info('⛔ Для зупинки натисніть Ctrl+C')
    logger.info('=' * 60)
    
    bot = Bot(token=bot_token)
    morning_sent_today = False
    evening_sent_today = False
    
    try:
        # Перевіряємо з'єднання
        bot_info = await bot.get_me()
        logger.info(f'✅ Бот підключено: @{bot_info.username}')
        
        # Якщо Chat ID не вказано, намагаємось отримати автоматично
        if chat_id is None:
            logger.info('\n📱 Chat ID не вказано.')
            logger.info('💬 Відправте будь-яке повідомлення вашому боту в Telegram!')
            logger.info(f'   Знайдіть бота: @{bot_info.username}')
            logger.info('   Напишіть йому: /start або будь-який текст\n')
            logger.info('⏳ Очікую повідомлення...')
            
            # Чекаємо повідомлення
            while chat_id is None:
                chat_id = await get_chat_id_from_updates(bot)
                if chat_id is None:
                    await asyncio.sleep(2)
            
            logger.info(f'💬 Використовую Chat ID: {chat_id}\n')
        else:
            logger.info(f'💬 Chat ID: {chat_id}')
        
        # Відправляємо стартове повідомлення
        await bot.send_message(
            chat_id=chat_id,
            text=f'🤖 *Бот запущено!*\n\n'
                 f'🌅 Щодня о {morning_hour}:00 - графік на сьогодні\n'
                 f'🌙 Щодня о {evening_hour}:00 - графік на завтра\n'
                 f'🔄 Кожні {interval_minutes} хвилин перевіряю зміни\n'
                 f'📬 Повідомлення тільки при оновленні графіку\n'
                 f'⚠️ Попередження за {warning_minutes} хв до відключення\n\n'
                 f'📍 Київ, Група {queue}',
            parse_mode='Markdown'
        )
        
        # Основний цикл
        while True:
            logger.info('\n' + '─' * 60)
            current_time = datetime.now()
            current_date = current_time.date()
            
            # Перевіряємо, чи настав новий день
            if hasattr(monitor_and_send, 'last_date'):
                if monitor_and_send.last_date != current_date:
                    morning_sent_today = False
                    evening_sent_today = False
                    monitor_and_send.last_date = current_date
            else:
                monitor_and_send.last_date = current_date
            
            # Перевіряємо, чи час для ранкового повідомлення (графік на сьогодні)
            is_morning_time = current_time.hour == morning_hour and current_time.minute < interval_minutes
            
            # Перевіряємо, чи час для вечірнього повідомлення (графік на завтра)
            is_evening_time = current_time.hour == evening_hour and current_time.minute < interval_minutes
            
            if is_morning_time and not morning_sent_today:
                logger.info(f'🌅 Ранок! Відправляю графік на сьогодні о {morning_hour}:00')
                schedule_hash = await send_schedule_update(bot, chat_id, region, queue, force=True)
                if schedule_hash:
                    send_schedule_update.last_schedule = schedule_hash
                    morning_sent_today = True
            elif is_evening_time and not evening_sent_today:
                logger.info(f'🌙 Вечір! Відправляю графік на завтра о {evening_hour}:00')
                await send_tomorrow_schedule(bot, chat_id, region, queue)
                evening_sent_today = True
            else:
                # Звичайна перевірка на зміни
                schedule_hash = await send_schedule_update(bot, chat_id, region, queue, force=False)
                if schedule_hash:
                    send_schedule_update.last_schedule = schedule_hash
            
            # Перевірка попереджень про майбутні відключення
            await check_and_send_warnings(bot, chat_id, region, queue, warning_minutes)
            
            logger.info(f'⏳ Наступна перевірка через {interval_minutes} хвилин...')
            logger.info('─' * 60)
            
            # Чекаємо інтервал
            await asyncio.sleep(interval_minutes * 60)
            
    except KeyboardInterrupt:
        logger.info('\n⛔ Зупинка бота...')
        await bot.send_message(
            chat_id=chat_id,
            text='⛔ Бот зупинено'
        )
    except Exception as e:
        logger.error(f'❌ Критична помилка: {e}')
        raise


def main():
    """Точка входу"""
    
    # Перевірка налаштувань
    if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE' or not BOT_TOKEN:
        print('❌ ПОМИЛКА: Не вказано BOT_TOKEN!')
        print('📝 Відредагуйте файл telegram_bot.py та вставте токен вашого бота')
        print('💡 Як отримати токен: напишіть @BotFather в Telegram')
        return
    
    # CHAT_ID може бути None - визначиться автоматично
    if CHAT_ID is None:
        print('✅ Chat ID буде визначено автоматично')
        print('� Після запуску відправте повідомлення вашому боту!\n')
    
    # Запуск бота
    asyncio.run(monitor_and_send(
        bot_token=BOT_TOKEN,
        chat_id=CHAT_ID,
        region=REGION,
        queue=QUEUE,
        interval_minutes=UPDATE_INTERVAL_MINUTES,
        morning_hour=MORNING_NOTIFICATION_HOUR,
        evening_hour=EVENING_NOTIFICATION_HOUR,
        warning_minutes=WARNING_MINUTES_BEFORE
    ))


if __name__ == '__main__':
    main()
