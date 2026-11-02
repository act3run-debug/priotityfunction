"""Локальные юнит-тесты src/index.py без сети: стаб requests + синтетические задачи."""
import os, sys, types, importlib.util
from datetime import datetime, timezone, timedelta

os.environ["TOKEN"] = "test-token"
os.environ["ORG"] = "test-org"

UP = "6a1d68818d0172414aa13d0e--uniquePriority"
CL = "6a1d68818d0172414aa13d0e--clarifyingAUniquePriority"

class Resp:
    def __init__(self, code, payload=None, text=""):
        self.status_code, self._p, self.text = code, payload, text
    def json(self):
        return self._p

CREATED = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

def task(key, type_d, status_d, prio="средний", **extra):
    t = {"key": key, "createdAt": CREATED,
         "priority": {"display": prio},
         "status": {"display": status_d},
         "type": {"display": type_d}}
    t.update(extra)
    return t

TASKS_QUEUE = [
    task("T-TECH",  "Технологическая", "Новый"),                       # неизвестный тип -> 0
    task("T-EPIC",  "Epic", "В работе"),                                # неизвестный тип -> 0
    task("T-OTVET", "Запрос на обслуживание", "Ответ получен"),        # = «В работе»
    task("T-INPR",  "Запрос на обслуживание", "В работе"),
    task("T-BUG",   "Ошибка", "Новый"),                                 # обычный расчёт
    task("T-CLAR",  "Story", "В работе", **{CL: 10}),                  # clarifying применяется
    task("T-CLOSE", "Ошибка", "Закрыт"),                                # ignored -> без PATCH
]
TASKS_SPRINT = [task("T-SPR", "Story", "Новый")]

patches = []

fake = types.ModuleType("requests")
def _get(url, headers=None, **kw):
    if url.endswith("/boards/214/sprints"):
        return Resp(400, {"errorMessages": ["У доски этого типа не может быть спринтов."]})
    if url.endswith("/boards/555/sprints"):
        return Resp(200, [{"id": "s1", "status": "in_progress"}, {"id": "s0", "status": "archived"}])
    raise AssertionError("неожиданный GET " + url)
def _post(url, headers=None, params=None, json=None, **kw):
    assert url.endswith("/issues/_search"), url
    if "sprint" in json["filter"]:
        assert json["filter"] == {"sprint": "s1"}
        return Resp(200, TASKS_SPRINT if params["page"] == 1 else [])
    assert json["filter"] == {"queue": "TRACKERSUPPORT"}
    return Resp(200, TASKS_QUEUE if params["page"] == 1 else [])
def _patch(url, headers=None, json=None, **kw):
    patches.append((url.rsplit("/", 1)[-1], json))
    return Resp(200, {})
fake.get, fake.post, fake.patch = _get, _post, _patch
sys.modules["requests"] = fake

spec = importlib.util.spec_from_file_location("index", os.path.join(os.path.dirname(__file__), "src", "index.py"))
index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index)

# доска 555 — проверка, что спринтовый путь не сломан
index.BOARD_CONFIGS["555"] = {"queue": None, "unique_priority_id": UP, "clarifying_priority_id": CL}

# T-SAME: значение уже равно расчётному -> PATCH не должно быть
same = task("T-SAME", "Story", "В работе")
same[UP] = round(index.calculate_priority(same), 2)
TASKS_QUEUE.append(same)

res = index.handler({})
body = res["body"]
got = {k: v[UP] for k, v in patches}
print("updated_count:", body["updated_count"], "zeroed_count:", body["zeroed_count"])
print("logs:", body["logs"])
print("patches:", got)

errors = []
def check(cond, msg):
    if not cond: errors.append(msg)

check(res["statusCode"] == 200, "statusCode != 200")
check(got.get("T-TECH") == 0.0, f"T-TECH должен быть 0, получено {got.get('T-TECH')}")
check(got.get("T-EPIC") == 0.0, f"T-EPIC должен быть 0, получено {got.get('T-EPIC')}")
check(body["zeroed_count"] == 2, "zeroed_count != 2")
check("T-OTVET" in got and "T-INPR" in got and got["T-OTVET"] == got["T-INPR"],
      f"«Ответ получен» != «В работе»: {got.get('T-OTVET')} vs {got.get('T-INPR')}")
otvet_status_part = index.STATUS_COEFFS.get("ответ получен")
check(otvet_status_part == index.STATUS_COEFFS.get("в работе") == 5, "STATUS_COEFFS['ответ получен'] != 5")
check(got.get("T-BUG", 0) > 0, "T-BUG должен быть > 0")
check("T-CLAR" in got and abs(got["T-CLAR"] - (index.calculate_priority(same_task := task("T-CLAR2", "Story", "В работе")) + 10)) < 0.02,
      f"T-CLAR: clarifying не прибавлен: {got.get('T-CLAR')}")
check("T-CLOSE" not in got, "T-CLOSE (Закрыт) не должен патчиться")
check("T-SAME" not in got, "T-SAME (значение не изменилось) не должен патчиться")
check("T-SPR" in got, "спринтовая доска 555 не обработана")
check(body["updated_count"] == len(got), "updated_count != число PATCH")

print("\nРАСЧЁТНЫЕ ЗНАЧЕНИЯ: «Ответ получен» =", got.get("T-OTVET"), "| «В работе» =", got.get("T-INPR"))

# === Фаза 2: флаг USE_ESTIMATION ===
_now = datetime.now(timezone.utc)
def _ts(dt): return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")

cases = {
    "none": task("E-NONE", "Ошибка", "Новый"),
    "10h":  task("E-10H",  "Ошибка", "Новый", estimation="10h"),
    "10m":  task("E-10M",  "Ошибка", "Новый", estimation="10m"),
    "dl":   task("E-DL",   "Ошибка", "Новый", estimation="10h", deadline=_ts(_now + timedelta(hours=2))),
    "sla":  task("E-SLA",  "Ошибка", "Новый", estimation="10h",
                 sla=[{"clockStatus": "STARTED", "violationStatus": "NOT_VIOLATED", "failAt": _ts(_now + timedelta(hours=3))}]),
}
def run_all():
    return {k: index.calculate_priority(v) for k, v in cases.items()}

index.USE_ESTIMATION = True
on = run_all()
index.USE_ESTIMATION = False
off = run_all()
index.USE_ESTIMATION = True  # вернуть как в проде

# ON: оценка влияет (короткий бонус +20 vs штраф 10h −0.3)
check(abs((on["none"] - on["10h"]) - 20.3) < 0.01, f"ON: none−10h != 20.3 ({on['none']-on['10h']:.4f})")
check(abs(on["none"] - on["10m"]) < 0.01, "ON: none и 10m должны совпадать (оба короткий бонус)")
# OFF: оценка не учитывается ВОВСЕ — все варианты равны
check(abs(off["none"] - off["10h"]) < 0.01 and abs(off["none"] - off["10m"]) < 0.01,
      f"OFF: задачи с разной оценкой должны быть равны: none={off['none']}, 10h={off['10h']}, 10m={off['10m']}")
# OFF: короткий бонус исчезает (−20 к базе без оценки)
check(abs((on["none"] - off["none"]) - 20.0) < 0.01, f"OFF: короткий бонус не убран ({on['none']-off['none']:.4f})")
# Дедлайн (2ч, оценка 10ч): ON → критичность 10 + штраф 0.3; OFF → только «<72ч» = 35 ⇒ разница 25.3
check(abs((off["dl"] - on["dl"]) - 25.3) < 0.05, f"DL: OFF−ON != 25.3 ({off['dl']-on['dl']:.4f})")
# SLA (fail через 3ч, оценка 10ч): ON → 15*1.5=22.5 −0.3; OFF → 34.5*1.5=51.75 ⇒ разница 29.55
check(abs((off["sla"] - on["sla"]) - 29.55) < 0.05, f"SLA: OFF−ON != 29.55 ({off['sla']-on['sla']:.4f})")
print("\nФаза 2 USE_ESTIMATION  ON :", {k: round(v, 2) for k, v in on.items()})
print("Фаза 2 USE_ESTIMATION  OFF:", {k: round(v, 2) for k, v in off.items()})

if errors:
    print("\nПРОВАЛЕНО:"); [print(" -", e) for e in errors]; sys.exit(1)
print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ ✔")
