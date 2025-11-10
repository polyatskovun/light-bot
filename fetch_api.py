"""
Скрипт для отримання графіку відключень через API svitlo.live
Використовує офіційний API endpoint
"""

import requests
import json
from datetime import datetime
import time

def fetch_schedule_from_api(region='kyiv', queue='2.2'):
    """Отримує графік через API svitlo.live"""
    
    # API endpoint (офіційний Cloudflare Worker proxy)
    api_url = 'https://svitlo-proxy.svitlo-proxy.workers.dev'
    
    print('🔄 Початок отримання графіку через API...')
    print(f'⏰ Час запиту: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'🌐 API URL: {api_url}')
    print(f'📍 Регіон: {region}, Група: {queue}')
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    
    # Параметри запиту
    params = {
        'region': region,
        'queue': queue
    }
    
    try:
        response = requests.get(api_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        
        print(f'✅ Статус відповіді: {response.status_code}')
        
        # Парсимо JSON
        data = response.json()
        
        print(f'📊 Дані отримано:')
        print(json.dumps(data, ensure_ascii=False, indent=2))
        
        # Зберігаємо у файл
        with open('schedule_api.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print('\n💾 Графік збережено у файл schedule_api.json')
        
        # Форматуємо для читабельності
        format_schedule(data, queue)
        
        return data
        
    except requests.exceptions.RequestException as e:
        print(f'❌ Помилка запиту: {e}')
        
        # Спробуємо альтернативний endpoint
        print('\n🔄 Спроба використати прямий API svitlo.live...')
        try:
            alt_url = 'https://svitlo.live/api/asistant.php'
            response = requests.get(alt_url, headers=headers, params=params, timeout=10)
            data = response.json()
            print('✅ Альтернативний API спрацював!')
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return data
        except Exception as e2:
            print(f'❌ Альтернативний API теж не спрацював: {e2}')
        
        return None
    except json.JSONDecodeError as e:
        print(f'❌ Помилка парсингу JSON: {e}')
        print(f'📄 Відповідь сервера: {response.text[:500]}')
        return None
    except Exception as e:
        print(f'❌ Загальна помилка: {e}')
        return None


def format_schedule(data, queue):
    """Форматує графік для зручного читання"""
    
    print(f'\n{"="*60}')
    print(f'⚡ ГРАФІК ВІДКЛЮЧЕНЬ ДЛЯ ГРУПИ {queue} (КИЇВ)')
    print(f'{"="*60}')
    
    if not isinstance(data, dict) or 'regions' not in data:
        print('⚠️ Дані не в очікуваному форматі')
        return
    
    # Шукаємо Київ у регіонах
    kyiv_data = None
    for region in data['regions']:
        if region.get('cpu') == 'kyiv':
            kyiv_data = region
            break
    
    if not kyiv_data or not kyiv_data.get('schedule'):
        print('⚠️ Дані для Києва не знайдено')
        return
    
    schedule = kyiv_data['schedule']
    
    # Перевіряємо чи є наша група
    if queue in schedule:
        group_schedule = schedule[queue]
        
        for date, times in group_schedule.items():
            print(f'\n📅 {date}:')
            
            # Групуємо інтервали за статусом
            current_status = None
            start_time = None
            
            sorted_times = sorted(times.items())
            
            for time, status in sorted_times:
                # 0 - дані недоступні, 1 - світло є, 2 - можливе відключення
                status_text = {
                    0: '❓ Дані недоступні',
                    1: '💡 Світло Є',
                    2: '⚠️ Можливе відключення'
                }.get(status, 'Невідомо')
                
                if status != current_status:
                    if current_status is not None and start_time is not None:
                        print(f'  {start_time} - {time}: {prev_status_text}')
                    start_time = time
                    current_status = status
                    prev_status_text = status_text
            
            # Додаємо останній інтервал
            if start_time:
                print(f'  {start_time} - 24:00: {prev_status_text}')
    else:
        print(f'⚠️ Група {queue} не знайдена в даних')
        print(f'Доступні групи: {", ".join(schedule.keys())}')
    
    print(f'{"="*60}\n')


def monitor_schedule_api(region='kyiv', queue='2.2', interval_minutes=10):
    """Постійно моніторить графік через API"""
    
    print(f'⚙️ Запуск моніторингу через API')
    print(f'📍 Регіон: {region}, Група: {queue}')
    print(f'⏱️ Інтервал оновлення: {interval_minutes} хвилин')
    print('Press Ctrl+C to stop\n')
    
    try:
        while True:
            print('=' * 60)
            fetch_schedule_from_api(region, queue)
            print('=' * 60)
            print(f'\n⏳ Наступне оновлення через {interval_minutes} хвилин...\n')
            time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        print('\n\n⛔ Моніторинг зупинено користувачем')


if __name__ == '__main__':
    import sys
    
    # Параметри за замовчуванням
    region = 'kyiv'
    queue = '2.2'
    
    if len(sys.argv) > 1:
        if sys.argv[1] == 'monitor':
            interval = int(sys.argv[2]) if len(sys.argv) > 2 else 10
            monitor_schedule_api(region, queue, interval)
        else:
            # Використовуємо перший аргумент як групу
            queue = sys.argv[1]
            if len(sys.argv) > 2:
                region = sys.argv[2]
    
    # Одноразовий запуск
    fetch_schedule_from_api(region, queue)
