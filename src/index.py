# https://tracker.yandex.ru/admin/queue/TRACKERSUPPORT/automation/triggers/462?fromTab=triggers
import os
import re
import requests
from datetime import datetime, timezone

# === КОНФИГУРАЦИЯ ДОСОК И ЛОКАЛЬНЫХ ПОЛЕЙ ===
# Здесь можно добавлять новые очереди/доски
BOARD_CONFIGS = {
    "214": {
        "queue": "TRACKERSUPPORT",  # область выборки, если у доски нет спринтов (канбан)
        "unique_priority_id": "6a1d68818d0172414aa13d0e--uniquePriority",
        "clarifying_priority_id": "6a1d68818d0172414aa13d0e--clarifyingAUniquePriority"
    }
}

# === НАСТРОЙКИ ПРИОРИТЕТА (Ваши оригинальные) ===
# Учёт оценки времени (estimation) в расчёте:
#   True  — оценка участвует (бонус/штраф за длительность, критичность дедлайна и SLA)
#   False — оценка времени не учитывается в расчётах вообще
USE_ESTIMATION = True

PRIORITY_COEFFS = {
    'блокер': 5, 'критичный': 4, 'средний': 3, 'низкий': 2, 'незначительный': 1,
    'blocker': 5, 'critical': 4, 'major': 3, 'minor': 2, 'trivial': 1
}

STATUS_COEFFS = {
    'в работе': 5, 'требуется информация': 1,
    'приостановлено': 4, 'новый': 4,
    'ответ получен': 5,  # как «В работе»
    'inprogress': 5, 'needinfo': 1, 'suspended': 4, 'open': 4
}

# Типы задач, участвующие в расчёте. Тип, которого здесь нет, получает приоритет 0.
TYPE_COEFFS = {
    'доступ': 100000000, 'ошибка': 5,
    'запрос на обслуживание': 3, 'story': 3,
    'bug': 5, 'task': 3
}

WEIGHT_SLA = 1.5
SLA_VIOLATION_COEFFS = {
    'FAIL_CONDITIONS_VIOLATED': 25,
    'WARN_CONDITIONS_VIOLATED': 10,
    'NOT_VIOLATED': 0
}

SLA_CRITICAL_BONUS = 15
SLA_CLOSE_FAIL_MULTIPLIER = 0.5
SLA_OVERDUE_BASE_BONUS = 15
SLA_OVERDUE_PER_HOUR_BONUS = 0.5

WEIGHT_PRIORITY = 0.4
WEIGHT_DEADLINE_BASE = 0.4
WEIGHT_STATUS = 0.15
WEIGHT_TYPE = 2
WEIGHT_AGE = 0.1
WEIGHT_DURATION_BONUS = 0.1
WEIGHT_DEADLINE_URGENCY = 1

DURATION_BONUS_SHORT = 200.0
DURATION_PENALTY_PER_HOUR = -0.5
DURATION_BASELINE_HOURS = 4.0

DEADLINE_CRITICAL_BONUS = 10
DEADLINE_CLOSE_MULTIPLIER = 0.5
DEADLINE_OVERDUE_PENALTY = 15

IGNORED_STATUSES = [
#    "Ожидание ответа пользователя",
#    "Требуется информация",
    "Согласование результата",
    "Закрыт",
    "Отменено"
]

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def parse_duration(estimation):
    if not estimation:
        return 0.0
    hours = 0
    minutes = 0
    h_match = re.search(r'(\d+)\s*h', estimation, re.IGNORECASE)
    if h_match: hours = int(h_match.group(1))
    m_match = re.search(r'(\d+)\s*m', estimation, re.IGNORECASE)
    if m_match: minutes = int(m_match.group(1))
    return hours + minutes / 60.0

def make_aware(date_str):
    """Превращает любую строку с датой из Трекера в дату с таймзоной (UTC), чтобы не было ошибок вычитания"""
    if not date_str:
        return None
    date_str = date_str.replace('Z', '+00:00')
    if '.' in date_str:
        date_str = date_str.split('.')[0] + '+00:00'
    
    # Если пришла только дата без времени (например, "2024-05-12")
    if len(date_str) == 10:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        dt = datetime.fromisoformat(date_str)
        
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def calculate_priority(task):
    """ОРИГИНАЛЬНЫЙ расчет приоритета с исправленной работой дат"""
    
    priority_display = task['priority']['display']
    status_display = task['status']['display']

    now_utc = datetime.now(timezone.utc)

    # 1. Возраст задачи
    created_at = make_aware(task['createdAt'])
    age_in_days = (now_utc - created_at).days

    # 2. Дедлайн
    has_deadline = 'deadline' in task and task['deadline'] is not None
    hours_to_deadline = 0
    if has_deadline:
        deadline = make_aware(task['deadline'])
        hours_to_deadline = (deadline - now_utc).total_seconds() / 3600

    # None — оценка отключена флагом USE_ESTIMATION и нигде ниже не учитывается
    estimated_duration = parse_duration(task.get('estimation')) if USE_ESTIMATION else None

    # Бонус/штраф за длительность
    duration_bonus = 0
    if estimated_duration is not None:
        if estimated_duration <= 0.33:
            duration_bonus = DURATION_BONUS_SHORT
        elif estimated_duration > DURATION_BASELINE_HOURS:
            extra_hours = estimated_duration - DURATION_BASELINE_HOURS
            duration_bonus = DURATION_PENALTY_PER_HOUR * extra_hours

    # Критичность дедлайна
    deadline_urgency = 0
    if has_deadline:
        if hours_to_deadline < 0:
            deadline_urgency = 10 + abs(hours_to_deadline) * 0.5
        elif estimated_duration is not None and estimated_duration > hours_to_deadline:
            deadline_urgency = DEADLINE_CRITICAL_BONUS
        elif hours_to_deadline < 72:
            deadline_urgency = (72 - hours_to_deadline) * DEADLINE_CLOSE_MULTIPLIER

    # Логика SLA
    sla_list = task.get('sla', [])
    active_slas = [s for s in sla_list if s.get('clockStatus') == 'STARTED']
    
    max_violation_score = 0
    sla_urgency_score = 0
    closest_fail_hours = float('inf')
    
    for s in active_slas:
        v_status = s.get('violationStatus', 'NOT_VIOLATED')
        status_score = SLA_VIOLATION_COEFFS.get(v_status, 0)
        if status_score > max_violation_score:
            max_violation_score = status_score
            
        fail_at_str = s.get('failAt')
        if fail_at_str:
            fail_at = make_aware(fail_at_str)
            h_to_fail = (fail_at - now_utc).total_seconds() / 3600
            if h_to_fail < closest_fail_hours:
                closest_fail_hours = h_to_fail

    if closest_fail_hours != float('inf'):
        if closest_fail_hours < 0:
            sla_urgency_score = SLA_OVERDUE_BASE_BONUS + abs(closest_fail_hours) * SLA_OVERDUE_PER_HOUR_BONUS
        elif estimated_duration is not None and estimated_duration > closest_fail_hours:
            sla_urgency_score = SLA_CRITICAL_BONUS
        elif closest_fail_hours < 72:
            sla_urgency_score = (72 - closest_fail_hours) * SLA_CLOSE_FAIL_MULTIPLIER

    total_sla_points = (max_violation_score + sla_urgency_score) * WEIGHT_SLA

    # ОСНОВНАЯ ФОРМУЛА
    priority_score = (
        PRIORITY_COEFFS.get(priority_display.lower(), 1) * WEIGHT_PRIORITY +
        (10 if has_deadline else 0) * WEIGHT_DEADLINE_BASE +
        STATUS_COEFFS.get(status_display.lower(), 1) * WEIGHT_STATUS +
        TYPE_COEFFS.get(task['type']['display'].lower(), 0) * WEIGHT_TYPE +
        age_in_days * WEIGHT_AGE +
        duration_bonus * WEIGHT_DURATION_BONUS +
        deadline_urgency * WEIGHT_DEADLINE_URGENCY +
        total_sla_points
    )

    return priority_score

# === ОСНОВНОЙ ОБРАБОТЧИК YANDEX CLOUD FUNCTIONS ===

def handler(event, context=None):
    # Получаем авторизацию из переменных окружения Cloud Functions
    token = os.getenv('TOKEN')
    org_id = os.getenv('ORG')
    
    if not token or not org_id:
        return {'statusCode': 500, 'body': 'Отсутствуют переменные окружения TOKEN и ORG'}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Cloud-Org-Id": org_id
    }
    
    base_url = "https://api.tracker.yandex.net/v3"
    logs = []
    total_updated = 0
    total_zeroed = 0

    # Проходим по всем доскам из конфига
    for board_id, fields in BOARD_CONFIGS.items():
        logs.append(f"Обработка доски {board_id}")
        
        # 1. Получаем последний активный спринт доски (у канбан-досок спринтов нет — это нормально)
        sprint_id = None
        try:
            resp = requests.get(f"{base_url}/boards/{board_id}/sprints", headers=headers)
            if resp.status_code == 200:
                active_sprints = [s for s in resp.json() if s.get('status') == 'in_progress']
                if active_sprints:
                    sprint_id = active_sprints[0]['id']
                    logs.append(f"Найден активный спринт: {sprint_id}")
            elif resp.status_code != 400:
                logs.append(f"Поиск спринтов: HTTP {resp.status_code}")
        except Exception as e:
            logs.append(f"Ошибка поиска спринта: {e}")

        # 2. Выборка задач: активный спринт, а если его нет — вся очередь из конфига доски
        if sprint_id:
            base_filter = {"sprint": str(sprint_id)}
        elif fields.get("queue"):
            logs.append(f"Активного спринта нет — беру задачи очереди {fields['queue']}")
            base_filter = {"queue": fields["queue"]}
        else:
            logs.append(f"Нет активного спринта для доски {board_id} и не указана очередь. Пропуск.")
            continue

        tasks = []
        page = 1
        while True:
            payload = {"filter": base_filter, "order": "+priority"}
            try:
                resp = requests.post(f"{base_url}/issues/_search", headers=headers, params={"page": page, "perPage": 100}, json=payload)
                if resp.status_code == 200:
                    page_tasks = resp.json()
                    if not page_tasks: break
                    tasks.extend(page_tasks)
                    if len(page_tasks) < 100: break
                    page += 1
                else:
                    logs.append(f"Поиск задач: HTTP {resp.status_code}")
                    break
            except Exception as e:
                logs.append(f"Ошибка поиска задач: {e}")
                break
                
        logs.append(f"Задач в выборке: {len(tasks)}")

        # 3. Рассчитываем и обновляем приоритет
        for task in tasks:
            issue_key = task.get('key')
            try:
                # Проверки перед расчетом
                if 'priority' not in task or 'status' not in task or 'type' not in task or 'createdAt' not in task:
                    continue
                    
                if task['status']['display'] in IGNORED_STATUSES:
                    continue

                type_display = task['type']['display'].lower()

                if type_display not in TYPE_COEFFS:
                    # Тип задачи не указан в настройках — приоритет 0
                    final_score = 0.0
                    is_zero = True
                else:
                    # Считаем базу по ВАШЕЙ формуле
                    base_score = calculate_priority(task)

                    # Добавляем clarifyingAUniquePriority (оно имеет "последнее слово")
                    clarifying_val = task.get(fields["clarifying_priority_id"], 0.0)
                    try:
                        clarifying_val = float(clarifying_val) if clarifying_val is not None else 0.0
                    except (TypeError, ValueError):
                        clarifying_val = 0.0

                    final_score = base_score + clarifying_val
                    is_zero = False

                final_score = round(final_score, 2)

                # Если значение не изменилось — не пишем (меньше событий и уведомлений)
                current_val = task.get(fields["unique_priority_id"])
                try:
                    if current_val is not None and abs(float(current_val) - final_score) < 0.005:
                        continue
                except (TypeError, ValueError):
                    pass

                # Обновляем поле uniquePriority
                update_payload = {fields["unique_priority_id"]: final_score}
                patch_resp = requests.patch(f"{base_url}/issues/{issue_key}", headers=headers, json=update_payload)
                
                if patch_resp.status_code == 200:
                    total_updated += 1
                    if is_zero:
                        total_zeroed += 1
                else:
                    logs.append(f"PATCH {issue_key}: HTTP {patch_resp.status_code}")
                    
            except Exception as e:
                logs.append(f"Ошибка в задаче {issue_key}: {str(e)}")
                continue

    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': {
            'message': 'Скрипт отработал',
            'updated_count': total_updated,
            'zeroed_count': total_zeroed,
            'logs': logs
        }
    }
