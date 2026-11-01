# autoXAK — все изменения для независимого анализа

## 1. Точная область сравнения

- Репозиторий: https://github.com/kodeandr/autoXAK
- Ветка с реализацией: `fix/product-epics-audit`.
- Исходное состояние: `a4110942e789ca83193ff3b3bde07f2d45758488` (`a411094`).
- Реализация: `4325f10419b0a870742c873ef86d369865ce3ba1` (`4325f10`).
- Коммит: `Fix product authentication, trip recording and atomic TwinEngine fuel credits`.
- Объём: **50 файлов, 3951 добавленная и 3348 удалённых строк** по обычному Git diff.
- Дата формирования этого документа: 2026-11-01 20:14:06 UTC.

Это сравнение фиксированных коммитов, а не меняющегося содержимого GitHub main.
Ветка опубликована; слияние в main и обновление боевого сервера в рамках этой
реализации не выполнялись. При формировании Markdown код не изменялся и тесты
повторно не запускались: ниже приведены результаты завершённых прогонов.

Документ включает объяснения, инвентаризацию, доказательства проверок,
**полный исходный Git diff** и **полные итоговые версии всех 50 изменённых файлов**.
Код не сокращён и не заменён многоточиями. Упоминания `...`/`…`, если они есть
внутри исходников, относятся к самим исходникам или литературным формулировкам.
Секретные .env, содержимое базы, приватные ключи и журналы production в пакет
не входят: их не было среди изменений этого коммита.

Часть объёма diff вызвана удалением UTF-8 BOM, CRLF и конечных пробелов.
Это не означает замену алгоритма в каждой строке большого diff.
Содержательная версия diff без различий пробелов/CRLF дана отдельным
Markdown-файлом `autoXAK_CHANGES_REVIEW_4325f10.md`.
Исходные иллюстрации Figma были в переписке и не входят в Git-коммит;
данный Markdown их не заменяет при независимой визуальной проверке.

## 2. Требования, которые определяли реализацию

### 2.1. Последнее уточнение владельца о рублях — приоритетное

- Расчёт находится на сервере в `TwinEngine.evaluate_financial_delta`.
- `user_ax` — массив продольного ускорения после обработки гравитации/фильтрации.
- `n_hard = np.sum(user_ax > 1.8)`, `n_total = max(1, len(user_ax))`.
- `K_user = 1.0 + (n_hard / n_total) * 2.2`, `K_twin = 1.66`.
- `delta = max(0.0, K_twin - K_user)`.
- `saved_liters = distance_km / 100.0 * base_city_fuel_rate_l100km * delta`.
- `delta_rub = round(saved_liters * fuel_price_rub, 2)`.
- При `distance_km <= 0.01` рубли и литры строго равны `0.0`.
- Для Haval Jolion утверждён расход по умолчанию `8.5` л/100 км; цена пользователя
  по умолчанию `72.40` ₽/л.
- Искусственного ограничения результата диапазоном ₽/км нет.
- POST `/api/v1/telemetry/session` сохраняет поездку/точки и атомарно увеличивает
  `user.total_savings_rub` и `user.total_fuel_saved_liters`.
- Повторный POST той же поездки не начисляет повторно.
- Итог показывает `+X.XX ₽` и «Сэкономлено на топливе относительно лихача».
- Кнопка «Зачислить в копилку» закрывает итог и обновляет баланс с сервера.
  Это последнее уточнение изменяет первоначальную интерпретацию эпика 0006:
  фактическое начисление происходит при POST, а не отдельным запросом кнопки.

### 2.2. Остальные ограничения задачи

Сохранить FastAPI/PostgreSQL/native PWA и существующий дизайн. Сохранить нативный
сбор Motion около 50 Гц, исходные GPS-точки, вычитание гравитации, скоростной
Z-Shock Veto от 2.0 м/с, Haversine с фильтром телепортаций, Wake Lock и iOS
`DeviceMotionEvent.requestPermission()` непосредственно в пользовательском жесте.
Полную телеметрию сохранять в PostgreSQL, не подменять её синтетическими данными.

UI: max-width 414 px, #121417, safe-area/dvh, естественный поток кнопок,
одинаковая навигация из трёх иконок; Pointer Events для завершения поездки;
каскадный каталог БД, пропорциональный SVG 280×135; пустая история без фальшивого
82/100; сетка итогов 2×3 плюс индекс плавности; исходники UTF-8 без BOM.

Авторизация: гость/логин/регистрация, OAuth не показан в MVP UI; логин 5–15
латинских букв, пароль 8–15 символов с учётом регистра; после четырёх ошибок
блокировка на 300 секунд, HTTP 429; атомарный перенос гостевых данных.

Индекс плавности `smooth-sample-fraction-1` реализован как описательная доля
отсчётов в пределах −2.3…+2.0 м/с². Это отдельное выбранное правило реализации;
не нужно смешивать его с утверждённой формулой K_user или считать независимо
валидированным универсальным показателем безопасности.

## 3. Что следует проверить другой нейросети

Этот отчёт описывает выполненную реализацию и не доказывает отсутствие ошибок.
Рекомендуется независимое code review с конкретными сценариями воспроизведения:

1. Точность формулы, границ, округления и происхождения входных параметров.
2. Реальное содержание `user_ax`, знак/ориентация, фильтрация и влияние Z-Shock Veto
   на долю отсчётов. Правильность согласования времени GPS/IMU и границ записи.
3. Вызовы, в которых денежные значения всё ещё могут быть `None` при неполной
   телеметрии/неизвестном расходе; соответствие требованиям и защита от фиктивных данных.
4. Атомарность, конкурентные разные поездки, дубли, потеря ответа, смена владельца,
   гостевое слияние и запрет старых токенов. Учесть, что денежные балансы хранятся Float.
5. Сохранность полного массива, черновика, очереди и итога при перезагрузке,
   потере сети, квоте IndexedDB/localStorage и нескольких вкладках.
6. Согласованность предварительного и итогового расчёта; наличие данных в истории
   и обновление копилки после POST и после закрытия итога.
7. Авторизация, валидация, предел 4 ошибок/5 минут, повторное слияние и обработка ошибок.
8. Политика прогресса, календарные периоды, UTC offset, достаточность наблюдений,
   отсутствие включения старых фиктивных оценок в динамику.
9. Совместимость миграции с существующей БД, заполнение расхода, сохранение
   явно введённых параметров пользователя и применимость резервных копий.
10. PowerShell 5.1/7, команды Docker/SSH, обработка ошибок и откат; различия
    read-only non-root production и writable root development.
11. Фактическое соответствие Figma на реальных устройствах; iOS permissions,
    Wake Lock, touch/pointer, safe area и доступность полей/кнопок.
12. Достаточность и честность тестов: отдельно проверить изменённые старые
    ожидания и новые сценарии; прохождение теста не доказывает дорожную точность.

Формат полезного замечания: приоритет → путь/строка → условие → наблюдаемое
последствие → воспроизведение → предлагаемое исправление и проверка.

## 4. Инвентаризация изменённых файлов

`A` — новый файл, `M` — существующий изменённый файл. Статистика включает
изменения кодировки и переводов строк. Полные исходники расположены в разделе 7.

| № | Файл | Статус | + / − строк | Назначение |
|---:|---|:---:|---:|---|
| 1 | [README.md](autoXAK_CHANGES_FULL_4325f10.md#file-01) | M | 148 / 147 | Описание актуального MVP, начисления в POST и ссылка на аудит; нормализация переводов строк. |
| 2 | [app/api/auth.py](autoXAK_CHANGES_FULL_4325f10.md#file-02) | M | 125 / 18 | Логин/регистрация, проверка реквизитов, лимит 4 ошибки/300 секунд; атомарное слияние гостя и повторный безопасный вход; общий перенос для OAuth. |
| 3 | [app/api/dependencies.py](autoXAK_CHANGES_FULL_4325f10.md#file-03) | M | 1 / 1 | Отклонение токенов гостевой учётной записи, уже перенесённой в другой аккаунт. |
| 4 | [app/core/database.py](autoXAK_CHANGES_FULL_4325f10.md#file-04) | M | 17 / 4 | Аддитивная инициализация новых столбцов/индекса логина; заполнение расхода с происхождением и продуктового значения Haval Jolion 8.5. |
| 5 | [app/core/dsp/filters.py](autoXAK_CHANGES_FULL_4325f10.md#file-05) | M | 34 / 34 | Удаление UTF-8 BOM и нормализация CRLF/пробелов; алгоритм фильтра не заменялся. |
| 6 | [app/main.py](autoXAK_CHANGES_FULL_4325f10.md#file-06) | M | 76 / 30 | Серверный расчёт TwinEngine, согласование GPS/IMU, Haversine, полная телеметрия, атомарные начисления, маршрут прогресса и расход в профиле/каталоге. |
| 7 | [app/models/db_models.py](autoXAK_CHANGES_FULL_4325f10.md#file-07) | M | 211 / 191 | Модели LoginAttempt/TripTelemetryRecord; логин и признак переноса пользователя; городской расход и источник в модификации; нормализация текста. |
| 8 | [app/models/telemetry.py](autoXAK_CHANGES_FULL_4325f10.md#file-08) | M | 6 / 6 | Удаление BOM и нормализация переводов строк; контракты ограничений телеметрии сохранены. |
| 9 | [app/models/vehicle_profiles.py](autoXAK_CHANGES_FULL_4325f10.md#file-09) | M | 2 / 2 | Значение городского расхода Haval Jolion изменено с 9.8 на утверждённые 8.5 л/100 км; удалён BOM. |
| 10 | [app/schemas/vehicle.py](autoXAK_CHANGES_FULL_4325f10.md#file-10) | M | 2 / 2 | Ручное название автомобиля: от 1 до 12 символов; удалён BOM. |
| 11 | [app/services/capture_storage.py](autoXAK_CHANGES_FULL_4325f10.md#file-11) | M | 14 / 13 | Дистанция по соседним GPS-точкам Haversine с отсечением плохих интервалов, телепортаций и дрейфа; нулевые рубли/литры при расстоянии до 10 м. |
| 12 | [app/services/gis_service.py](autoXAK_CHANGES_FULL_4325f10.md#file-12) | M | 3 / 3 | Удаление BOM и пробелов в конце строк; функциональный GIS-алгоритм не менялся. |
| 13 | [app/services/progress.py](autoXAK_CHANGES_FULL_4325f10.md#file-13) | A | 36 / 0 | Новый сервис календарных периодов 7/30 дней, среднего, дельты и реальных дневных точек по известной политике плавности. |
| 14 | [app/services/twin_engine.py](autoXAK_CHANGES_FULL_4325f10.md#file-14) | M | 123 / 78 | Канонические K_user/K_twin и рубли, граница 10 м, метаданные расчёта, описательный индекс плавности; маска скорости и повторное взведение Z-Shock Veto. |
| 15 | [apply_complete_fix.py](autoXAK_CHANGES_FULL_4325f10.md#file-15) | M | 5 / 167 | Опасный старый патчер заменён явным отказом повторного применения и указанием актуального обновления. |
| 16 | [deploy/update.ps1](autoXAK_CHANGES_FULL_4325f10.md#file-16) | A | 144 / 0 | Новый PowerShell-скрипт: SSH production update, локальный Rebuild/Copy, бинарный дамп PostgreSQL, готовность API и восстановление после ошибки. |
| 17 | [docs/PRODUCT_EPICS_AUDIT.md](autoXAK_CHANGES_FULL_4325f10.md#file-17) | A | 169 / 0 | Отчёт об эпиках, математике, проверках, ограничениях и порядке обновления. |
| 18 | [docs/REFERENCE_MATH.md](autoXAK_CHANGES_FULL_4325f10.md#file-18) | M | 8 / 0 | Разъяснена область отдельного исследовательского ядра v3 относительно продуктовых рублей MVP. |
| 19 | [patch_setup_local.py](autoXAK_CHANGES_FULL_4325f10.md#file-19) | M | 2 / 2 | Удаление BOM и нормализация переводов строк. |
| 20 | [print_catalog.py](autoXAK_CHANGES_FULL_4325f10.md#file-20) | M | 2 / 2 | Удаление BOM и нормализация переводов строк. |
| 21 | [seed_fleet.py](autoXAK_CHANGES_FULL_4325f10.md#file-21) | M | 266 / 266 | Инициализация схемы до и после загрузки каталога, чтобы заполнялись новые параметры; нормализация переводов строк. |
| 22 | [seed_fuel_prices.py](autoXAK_CHANGES_FULL_4325f10.md#file-22) | M | 4 / 4 | Удаление BOM и нормализация переводов строк. |
| 23 | [static/app.css](autoXAK_CHANGES_FULL_4325f10.md#file-23) | A | 52 / 0 | Новый общий слой геометрии: 414 px, #121417, dvh/safe area, одинаковая навигация, естественные кнопки, нативные select, модалки и прогресс. |
| 24 | [static/auth.css](autoXAK_CHANGES_FULL_4325f10.md#file-24) | A | 21 / 0 | Стили форм входа/регистрации в существующей визуальной системе и индикатор ожидания. |
| 25 | [static/history.html](autoXAK_CHANGES_FULL_4325f10.md#file-25) | M | 537 / 527 | Пустое состояние без фиктивных 82/100, подтверждённая история, отдельный статус очереди, динамика прогресса и индекс в деталях. |
| 26 | [static/index.html](autoXAK_CHANGES_FULL_4325f10.md#file-26) | M | 142 / 359 | Исправлен старт JS/запись, сохранены исходные точки, убрана глобальная подмена GPS, фильтр движения/гравитации, Pointer Events, итог и обновление баланса. |
| 27 | [static/login.html](autoXAK_CHANGES_FULL_4325f10.md#file-27) | M | 98 / 24 | Гостевой вход плюс формы логина/регистрации, валидация, показ пароля, обработка ошибок и переноса гостя; OAuth-кнопка в UI убрана. |
| 28 | [static/manifest.json](autoXAK_CHANGES_FULL_4325f10.md#file-28) | M | 2 / 2 | Удаление BOM и нормализация переводов строк. |
| 29 | [static/offline_store.js](autoXAK_CHANGES_FULL_4325f10.md#file-29) | M | 179 / 193 | Убрано сокращение исходной записи; полная IndexedDB-очередь и подтверждение после успешного POST; сохранение индекса плавности в локальной истории. |
| 30 | [static/progress.js](autoXAK_CHANGES_FULL_4325f10.md#file-30) | A | 42 / 0 | Новый рендерер прогресса: переключение периодов, актуальность асинхронных ответов, SVG реальных точек и пустые состояния. |
| 31 | [static/session.js](autoXAK_CHANGES_FULL_4325f10.md#file-31) | M | 8 / 4 | Выбор входа до создания гостя, разбор ошибок JSON с сохранением таймаута чтения, очистка кэша итогов/прогресса при смене владельца. |
| 32 | [static/setup.html](autoXAK_CHANGES_FULL_4325f10.md#file-32) | M | 1064 / 1221 | Реальный каскад PostgreSQL без фиктивного каталога, нативные select, обязательный расход для расчёта, сохранение профиля и адаптация силуэта/карточки. |
| 33 | [static/sw.js](autoXAK_CHANGES_FULL_4325f10.md#file-33) | M | 2 / 2 | Новая версия кэша autoxak-shell-v6 и новые CSS/JS в app shell. |
| 34 | [test_cpa_funnel.py](autoXAK_CHANGES_FULL_4325f10.md#file-34) | M | 2 / 2 | Удаление BOM и нормализация переводов строк. |
| 35 | [tests/browser/live_metrics.cjs](autoXAK_CHANGES_FULL_4325f10.md#file-35) | M | 17 / 17 | Регрессии стоянки/скорости для ударов, движущегося предпросмотра денег, устаревших ответов и полной длинной записи. |
| 36 | [tests/browser/logic_audit.cjs](autoXAK_CHANGES_FULL_4325f10.md#file-36) | M | 5 / 1 | Проверки сохранения расхода, разделения подтверждённой истории/очереди и таймаута тела ответа. |
| 37 | [tests/browser/package.json](autoXAK_CHANGES_FULL_4325f10.md#file-37) | M | 1 / 1 | Новый сквозной product_epics.cjs включён в общий npm test. |
| 38 | [tests/browser/product_epics.cjs](autoXAK_CHANGES_FULL_4325f10.md#file-38) | A | 100 / 0 | Новый сквозной сценарий: реквизиты, 18 адаптивных экранов, каталог, синтетика 50 Гц, манёвры/удар, полный экспорт, начисление в POST и touch-свайп. |
| 39 | [tests/browser/recording.cjs](autoXAK_CHANGES_FULL_4325f10.md#file-39) | M | 6 / 3 | Фикстуры реального перемещения GPS, закрытие итога через пользовательское действие и восстановление при одновременном отказе сети/хранилища. |
| 40 | [tests/browser/service_worker.cjs](autoXAK_CHANGES_FULL_4325f10.md#file-40) | M | 2 / 2 | Ожидания версии кэша обновлены на v6. |
| 41 | [tests/browser/smoke.cjs](autoXAK_CHANGES_FULL_4325f10.md#file-41) | M | 2 / 1 | Расход ручного автомобиля и допустимая точность результата Haversine. |
| 42 | [tests/conftest.py](autoXAK_CHANGES_FULL_4325f10.md#file-42) | M | 3 / 3 | Совместимый scope event loop для session-фикстуры БД; детерминированный шум синтетических датчиков; нормализация текста. |
| 43 | [tests/test_audit_regressions.py](autoXAK_CHANGES_FULL_4325f10.md#file-43) | M | 2 / 4 | Ожидания нового серверного денежного результата и нулевой стоянки вместо прежнего отсутствия расчёта. |
| 44 | [tests/test_capture_recording.py](autoXAK_CHANGES_FULL_4325f10.md#file-44) | M | 3 / 3 | Ожидания Haversine-дистанции и строгого нулевого начисления короткой записи. |
| 45 | [tests/test_dsp_filters.py](autoXAK_CHANGES_FULL_4325f10.md#file-45) | M | 2 / 2 | Удаление BOM и нормализация переводов строк. |
| 46 | [tests/test_live_preview.py](autoXAK_CHANGES_FULL_4325f10.md#file-46) | M | 4 / 3 | Ожидания серверного денежного предпросмотра без начисления в баланс. |
| 47 | [tests/test_logic_audit.py](autoXAK_CHANGES_FULL_4325f10.md#file-47) | M | 3 / 2 | Ожидания канонического денежного расчёта и нулевой короткой записи при неподдерживаемой частоте. |
| 48 | [tests/test_powershell_update.py](autoXAK_CHANGES_FULL_4325f10.md#file-48) | A | 85 / 0 | Новые проверки последовательности резервирования/обновления, Copy, запрета read-only и отката образа. |
| 49 | [tests/test_product_epics.py](autoXAK_CHANGES_FULL_4325f10.md#file-49) | A | 167 / 0 | Новые проверки реквизитов/блокировки/слияния, границ формулы, полного массива и конкурентных начислений/повторов, прогресса и границы скорости ударов. |
| 50 | [tests/test_reference_math.py](autoXAK_CHANGES_FULL_4325f10.md#file-50) | M | 2 / 2 | Сохранена проверка неизвестного расхода; отдельное исследовательское сравнение не начисляет свой результат в продуктовый баланс. |

### Полная статистика Git

````text
 README.md                        |  295 ++---
 app/api/auth.py                  |  143 ++-
 app/api/dependencies.py          |    2 +-
 app/core/database.py             |   21 +-
 app/core/dsp/filters.py          |   68 +-
 app/main.py                      |  106 +-
 app/models/db_models.py          |  402 +++----
 app/models/telemetry.py          |   12 +-
 app/models/vehicle_profiles.py   |    4 +-
 app/schemas/vehicle.py           |    4 +-
 app/services/capture_storage.py  |   27 +-
 app/services/gis_service.py      |    6 +-
 app/services/progress.py         |   36 +
 app/services/twin_engine.py      |  201 ++--
 apply_complete_fix.py            |  172 +--
 deploy/update.ps1                |  144 +++
 docs/PRODUCT_EPICS_AUDIT.md      |  169 +++
 docs/REFERENCE_MATH.md           |    8 +
 patch_setup_local.py             |    4 +-
 print_catalog.py                 |    4 +-
 seed_fleet.py                    |  532 ++++-----
 seed_fuel_prices.py              |    8 +-
 static/app.css                   |   52 +
 static/auth.css                  |   21 +
 static/history.html              | 1064 +++++++++---------
 static/index.html                |  501 +++------
 static/login.html                |  122 +-
 static/manifest.json             |    4 +-
 static/offline_store.js          |  372 +++----
 static/progress.js               |   42 +
 static/session.js                |   12 +-
 static/setup.html                | 2285 ++++++++++++++++++--------------------
 static/sw.js                     |    4 +-
 test_cpa_funnel.py               |    4 +-
 tests/browser/live_metrics.cjs   |   34 +-
 tests/browser/logic_audit.cjs    |    6 +-
 tests/browser/package.json       |    2 +-
 tests/browser/product_epics.cjs  |  100 ++
 tests/browser/recording.cjs      |    9 +-
 tests/browser/service_worker.cjs |    4 +-
 tests/browser/smoke.cjs          |    3 +-
 tests/conftest.py                |    6 +-
 tests/test_audit_regressions.py  |    6 +-
 tests/test_capture_recording.py  |    6 +-
 tests/test_dsp_filters.py        |    4 +-
 tests/test_live_preview.py       |    7 +-
 tests/test_logic_audit.py        |    5 +-
 tests/test_powershell_update.py  |   85 ++
 tests/test_product_epics.py      |  167 +++
 tests/test_reference_math.py     |    4 +-
 50 files changed, 3951 insertions(+), 3348 deletions(-)
````

## 5. Проверки и практические ограничения

Зафиксированы: **205 pytest passed, 1 skipped**. Пропущена интеграция с живым
staging, который отдельно не был запущен. Использованы реальные PostgreSQL/Redis
и синтетические датчики. Браузерный набор Chromium прошёл, включая 18 сочетаний
экранов/размеров, запись 3001 Motion + 61 GPS, манёвры, удар, экспорт, начисление
в POST, историю и свайпы мышью/эмуляцией touch.

PowerShell проверялся через Parser/PowerShell 7.5.4, сценарии подменённого Docker CLI
и реальный Copy на отдельном временном Docker-стеке с PostgreSQL. Это не означает
исполнение production Rebuild на Windows 5.1 или обновление сервера HOSTKEY.

Реальных парных OBD/CAN-логов для независимого подтверждения MAPE ≤ 10% в проекте
не было. Масса/CdA/КПД доступны отдельному физическому ядру, но не входят в
приведённую утверждённую формулу мультипликаторов MVP.
Непрерывную выдачу 50 Гц при свёрнутом/выгруженном Safari браузер гарантировать
не может; записи разрывов не дополняются выдуманными отсчётами.
Пределы: 500000 IMU/производных точек, 100000 GPS-точек, до 24 часов.

Ниже — сохранённые результаты предыдущих прогонов, без секретов окружения.

### pytest

````text
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.7.0
cachedir: /tmp/autoxak-pytest-cache
rootdir: /workspace/autoXAK
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.15.1, asyncio-1.4.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collected 206 items

tests/test_api_endpoints.py .......                                      [  3%]
tests/test_audit_regressions.py ........................................ [ 22%]
                                                                         [ 22%]
tests/test_benchmark_engine.py ..                                        [ 23%]
tests/test_capture_recording.py ..........                               [ 28%]
tests/test_dsp_filters.py ...                                            [ 30%]
tests/test_fuel_autonomy.py ...                                          [ 31%]
tests/test_legacy_settings.py ...                                        [ 33%]
tests/test_live_preview.py .........                                     [ 37%]
tests/test_logic_audit.py ............                                   [ 43%]
tests/test_network_offload_probe.py .........                            [ 47%]
tests/test_pipeline.py .....                                             [ 50%]
tests/test_powershell_update.py ....                                     [ 51%]
tests/test_product_epics.py ........................                     [ 63%]
tests/test_production_deployment.py .....................                [ 73%]
tests/test_reference_math.py .............................               [ 87%]
tests/test_security_and_cache.py .....                                   [ 90%]
tests/test_server_audit.py ......                                        [ 93%]
tests/test_staging_deployment.py .......s                                [ 97%]
tests/test_vehicle_synthesizer.py ..                                     [ 98%]
tests/test_wear_and_twin_engine.py ....                                  [100%]

======================= 205 passed, 1 skipped in 24.92s ========================
````

### Браузерный набор

````text

> test
> node smoke.cjs && node recording.cjs && node live_metrics.cjs && node logic_audit.cjs && node service_worker.cjs && node product_epics.cjs

Browser: owner-isolated queue, 422 retention, successful retry/control, transaction-abort durability, interrupted recording recovery passed
Browser: profile recovery after 503, single GPS distance integration, no fabricated IMU samples, geolocation watcher 0 cleanup passed
Browser: stale signature reauthentication, OAuth without rejected bearer, pending profile preserved, 503 keeps offline identity passed
Browser: guest login, authenticated garage/manual profile, preserved wear/settings, history, root service worker passed
Recording: iOS gesture, one GPS watch, independent IMU, weak GPS, denied motion, GPS-only history, offline retry and failed-save recovery passed
Live metrics: stationary speed veto, gravity projection, rotation veto, moving financial preview, stale replies and full long-capture retention passed
Browser: fuel price preservation, remote garage update, response-body deadline, finalized draft, durable/offline history, control revision, pagination, stale GPS/session snapshot passed
Service worker: fresh/cached shell, 503, redirected login, stalled headers/body, query canonicalization, API bypass, cache upgrade passed
Product epics: credentials, 18 responsive screens, PostgreSQL garage, 50 Hz synthetic capture, moving shock, maneuvers, pointer finish, raw retention, atomic credits, history passed
Touch device swipe: Pointer Events completion passed
npm notice
npm notice New major version of npm available! 11.9.0 -> 12.2.1
npm notice Changelog: https://github.com/npm/cli/releases/tag/v12.2.1
npm notice To update run: npm install -g npm@12.2.1
npm notice
````

### Реальный Copy в отдельном Docker-стеке

````text
 Container autoxak-update-validation-api-1  Stopping
 Container autoxak-update-validation-api-1  Stopped
 Container autoxak-update-validation-postgres-1  Waiting
 Container autoxak-update-validation-postgres-1  Healthy
 Container autoxak-update-validation-api-1  Starting
 Container autoxak-update-validation-api-1  Started
Update complete. Backup: /workspace/autoXAK/backups/update-20261101T195629Z-d22cc8e9
````

## 6. Содержательный diff

Изменения пробелов в конце строк и CRLF исключены командой `git diff --ignore-space-at-eol`. Это облегчённая версия для первого прохода. Все исходные изменения и полные файлы приведены в `autoXAK_CHANGES_FULL_4325f10.md`.

````diff
diff --git a/README.md b/README.md
index 030759b..5437b14 100644
--- a/README.md
+++ b/README.md
@@ -10,9 +10,10 @@
 [![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
 
 > **Запись GPS/IMU, сохранение поездок и проверяемые численные расчёты с явными исходными данными.**
-> Версия 2.0: неподтверждённые экономия и состояние масла возвращаются как `null`.
+> MVP: рубли рассчитываются сервером по политике TwinEngine и атомарно начисляются при сохранении поездки. Неизмеренные показатели масла не подменяются оценками.
 > Численные проверки не являются аттестацией точности на реальном автомобиле.
-> [Контракт математического ядра и ограничения](docs/REFERENCE_MATH.md).
+> [Результаты аудита эпиков, формула и обновление](docs/PRODUCT_EPICS_AUDIT.md).
+> [Контракт отдельного физического ядра](docs/REFERENCE_MATH.md).
 
 ---
 
@@ -61,7 +62,7 @@
 «1,8 л экономии на 100 км», множителей износа по пробкам, рублёвого штрафа
 за каждый манёвр или заданного всем водителям суточного расхода.
 
-Расстояние вычисляется интегралом скорости по реальным временным меткам.
+В исходной GPS-записи дистанция вычисляется по Haversine с фильтром телепортаций. Для численного анализа скоростных рядов используется интеграл скорости по реальным временным меткам.
 Механическая работа разделяется на изменение кинетической энергии,
 качение, потенциальную энергию и аэродинамическую работу. Тяговая и отрицательная
 работа интегрируются раздельно. Механическая энергия не подменяется литрами.
@@ -115,7 +116,7 @@ autoXAK/
 │   ├── services/reference_math.py    # Кинематика, баланс работы, топливные интегралы
 │   ├── services/fuel_prediction.py   # Отдельная исследовательская VED-модель
 │   ├── services/wear_engine.py       # Совместимый адаптер недоступной оценки масла
-│   ├── services/twin_engine.py       # Явное сравнение; без вымышленного двойника
+│   ├── services/twin_engine.py       # Утверждённая модель топлива MVP и явные сравнения
 │   └── main.py                      # Приём сессий, история и профиль
 ├── docs/REFERENCE_MATH.md            # Формулы, допущения, ограничения и контракты
 ├── examples/reference_math/         # Только синтетические численные примеры
diff --git a/app/api/auth.py b/app/api/auth.py
index 378393d..63b7c1e 100644
--- a/app/api/auth.py
+++ b/app/api/auth.py
@@ -1,10 +1,10 @@
-﻿import os
+import os
 import json
 import uuid
 import hashlib
 import secrets
 import hmac
-from datetime import timedelta
+from datetime import datetime, timedelta, timezone
 import asyncio
 import urllib.request
 import urllib.parse
@@ -12,13 +12,13 @@ from typing import Optional
 
 from fastapi import APIRouter, Depends, HTTPException, Request
 from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
-from pydantic import BaseModel, Field
+from pydantic import BaseModel, Field, ConfigDict
 from sqlalchemy.ext.asyncio import AsyncSession
-from sqlalchemy import select, update
+from sqlalchemy import select, update, text
 
 from app.core.database import get_db
-from app.models.db_models import User, Trip
-from app.core.security import create_access_token, decode_access_token, SECRET_KEY
+from app.models.db_models import User, Trip, LoginAttempt
+from app.core.security import create_access_token, decode_access_token, SECRET_KEY, hash_password, verify_password
 from app.api.dependencies import require_user, bearer
 from fastapi.security import HTTPAuthorizationCredentials
 from app.models.recordings import TripRecording, RecordingVehicle
@@ -49,6 +49,124 @@ class AuthTokenResponse(BaseModel):
     is_new_user: bool = False
 
 
+class CredentialsPayload(BaseModel):
+    model_config = ConfigDict(extra="forbid")
+    login: str = Field(min_length=5, max_length=15, pattern=r"^[a-zA-Z]+$")
+    password: str = Field(min_length=8, max_length=15)
+    guest_id: Optional[str] = Field(default=None, max_length=64)
+
+
+async def _credential_lock(db, key):
+    if db.bind.dialect.name == "postgresql":
+        lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
+        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
+
+
+async def _merge_guest(db, guest, target):
+    """Caller locks both rows; transfer once, in the same transaction as login."""
+    if guest is None or guest.id == target.id:
+        return
+    if guest.merged_into == target.id:
+        return  # Retry after a lost HTTP response.
+    if not guest.is_guest or guest.merged_into:
+        raise HTTPException(403, "Guest session proof is required")
+    for field in ("total_savings_rub", "total_fuel_saved_liters", "range_bank_km", "total_distance_km", "total_trips", "current_oil_wear_percent"):
+        value = (getattr(target, field) or 0) + (getattr(guest, field) or 0)
+        setattr(target, field, min(100.0, value) if field == "current_oil_wear_percent" else value)
+        setattr(guest, field, 0)
+    for table in (Trip, TripRecording, CPALead):
+        await db.execute(update(table).where(table.user_id == guest.id).values(user_id=target.id))
+    profile = await db.get(RecordingVehicle, guest.id)
+    if profile:
+        if await db.get(RecordingVehicle, target.id) is None:
+            db.add(RecordingVehicle(user_id=target.id, profile=profile.profile))
+        await db.delete(profile)
+    guest.merged_into = target.id
+
+
+def _guest_proof(payload, credentials):
+    if not payload.guest_id:
+        return None
+    claims = decode_access_token(credentials.credentials) if credentials and credentials.scheme.lower() == "bearer" else None
+    if not claims or claims.get("purpose", "access") != "access" or claims.get("sub") != payload.guest_id:
+        raise HTTPException(403, "Guest session proof is required")
+    return payload.guest_id
+
+
+def _session_response(user, new=False):
+    return AuthTokenResponse(token=create_access_token(user.id), user_id=user.id,
+                             name=user.name or user.login or "Гость", car_id=user.car_id,
+                             is_guest=user.is_guest, is_new_user=new)
+
+
+@router.post("/register", response_model=AuthTokenResponse)
+async def register(payload: CredentialsPayload, db: AsyncSession = Depends(get_db),
+                   credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
+    guest_id = _guest_proof(payload, credentials)
+    login = payload.login.lower()
+    await _credential_lock(db, "login:" + login)
+    existing = await db.scalar(select(User).where(User.login == login))
+    if existing:
+        # Idempotent recovery only with proof of the same guest and password.
+        if existing.id == guest_id and await asyncio.to_thread(verify_password, payload.password, existing.hashed_password):
+            return _session_response(existing)
+        raise HTTPException(409, "Этот логин уже занят")
+    guest = await db.scalar(select(User).where(User.id == guest_id).with_for_update()) if guest_id else None
+    if guest_id and (not guest or not guest.is_guest or guest.merged_into):
+        raise HTTPException(403, "Guest session proof is required")
+    user = guest or User(id="account_" + uuid.uuid4().hex, car_id="haval_jolion_15t_4wd")
+    user.login = login
+    user.name = payload.login
+    user.hashed_password = await asyncio.to_thread(hash_password, payload.password)
+    user.is_guest = False
+    db.add(user)
+    await db.commit()
+    return _session_response(user, True)
+
+
+@router.post("/login", response_model=AuthTokenResponse)
+async def login(payload: CredentialsPayload, db: AsyncSession = Depends(get_db),
+                credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
+    guest_id = _guest_proof(payload, credentials)
+    login_name = payload.login.lower()
+    await _credential_lock(db, "login:" + login_name)
+    now = datetime.now(timezone.utc)
+    key = hashlib.sha256(login_name.encode()).hexdigest()
+    attempt = await db.get(LoginAttempt, key)
+    if attempt and attempt.blocked_until and attempt.blocked_until > now:
+        seconds = max(1, int((attempt.blocked_until - now).total_seconds()) + 1)
+        raise HTTPException(429, "Слишком много попыток. Попробуйте позже", headers={"Retry-After": str(seconds)})
+    target = await db.scalar(select(User).where(User.login == login_name))
+    # Perform the same password work for unknown logins to avoid a timing oracle.
+    dummy = "0" * 32 + "$" + "0" * 64
+    valid = await asyncio.to_thread(verify_password, payload.password, target.hashed_password if target else dummy)
+    if not valid or target is None:
+        if attempt is None:
+            attempt = LoginAttempt(key=key, failures=0, window_started_at=now)
+            db.add(attempt)
+        if now - attempt.window_started_at >= timedelta(minutes=5):
+            attempt.failures = 0
+            attempt.window_started_at = now
+        attempt.failures += 1
+        if attempt.failures >= 4:
+            attempt.blocked_until = now + timedelta(minutes=5)
+        await db.commit()  # Failed attempts must survive the error response.
+        if attempt.failures >= 4:
+            raise HTTPException(429, "Слишком много попыток. Попробуйте позже", headers={"Retry-After": "300"})
+        raise HTTPException(401, "Неверный логин или пароль")
+    users = (await db.execute(select(User).where(User.id.in_(sorted({target.id, guest_id} - {None})))
+                             .order_by(User.id).with_for_update().execution_options(populate_existing=True))).scalars().all()
+    by_id = {user.id: user for user in users}
+    if guest_id and guest_id not in by_id:
+        raise HTTPException(403, "Guest session proof is required")
+    target = by_id[target.id]
+    await _merge_guest(db, by_id.get(guest_id), target)
+    if attempt:
+        await db.delete(attempt)
+    await db.commit()
+    return _session_response(target)
+
+
 def _build_anonymized_yandex_id(raw_yandex_id: str) -> str:
     digest = hashlib.sha256(f"{SECRET_SALT}:yandex_id:{raw_yandex_id}".encode("utf-8")).hexdigest()[:16]
     return f"ya_{digest}"
@@ -129,18 +247,7 @@ async def _get_or_merge_yandex_user(
                        service_cost_rub=getattr(guest_user, "service_cost_rub", None) or 9500.0)
         db.add(ya_user)
         await db.flush()  # Parent must exist before moving foreign keys.
-    if guest_user is not None:
-        for field in ("total_savings_rub", "total_fuel_saved_liters", "range_bank_km", "total_distance_km", "total_trips", "current_oil_wear_percent"):
-            value = (getattr(ya_user, field) or 0) + (getattr(guest_user, field) or 0)
-            setattr(ya_user, field, min(100.0, value) if field == "current_oil_wear_percent" else value)
-            setattr(guest_user, field, 0)
-        for table in (Trip, TripRecording, CPALead):
-            await db.execute(update(table).where(table.user_id == guest_user.id).values(user_id=ya_user_id))
-        profile = await db.get(RecordingVehicle, guest_user.id)
-        if profile is not None:
-            if await db.get(RecordingVehicle, ya_user_id) is None:
-                db.add(RecordingVehicle(user_id=ya_user_id, profile=profile.profile))
-            await db.delete(profile)
+    await _merge_guest(db, guest_user, ya_user)
     await db.commit()
     await db.refresh(ya_user)
     return ya_user
diff --git a/app/api/dependencies.py b/app/api/dependencies.py
index a85fc65..cb8c27e 100644
--- a/app/api/dependencies.py
+++ b/app/api/dependencies.py
@@ -35,7 +35,7 @@ async def require_user(
     if not math.isfinite(expires) or expires <= time.time():
         raise denied
     user = await db.get(User, subject)
-    if user is None:
+    if user is None or user.merged_into:
         raise denied
     return user
 
diff --git a/app/core/database.py b/app/core/database.py
index 1cc9b47..d738428 100644
--- a/app/core/database.py
+++ b/app/core/database.py
@@ -1,4 +1,4 @@
-﻿import os
+import os
 from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
 from sqlalchemy.orm import declarative_base
 
@@ -43,8 +43,9 @@ async def initialize_schema():
     from sqlalchemy import inspect, text
     from sqlalchemy.schema import CreateColumn
     additions = {
+        "vehicle_trims": {"base_city_fuel_rate_l100km", "fuel_rate_source"},
         "trips": {"board_computer_consumption_l100km", "request_hash", "calculation_result", "client_session_id"},
-        "users": {"car_name", "custom_profile"},
+        "users": {"car_name", "custom_profile", "login", "merged_into"},
     }
     async with engine.begin() as connection:
         if connection.dialect.name == "postgresql":
@@ -58,3 +59,15 @@ async def initialize_schema():
                 await connection.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {ddl}'))
         index = next(index for index in Base.metadata.tables["trips"].indexes if index.name == "ix_trips_user_client_session")
         await connection.run_sync(lambda sync: index.create(sync, checkfirst=True))
+
+        user_index = next(index for index in Base.metadata.tables["users"].indexes if index.name == "ix_users_login")
+        await connection.run_sync(lambda sync: user_index.create(sync, checkfirst=True))
+        # Preserve the three pre-existing backend rates with explicit provenance.
+        # Do not infer consumption for other modifications from mass/body/engine size.
+        from app.models.vehicle_profiles import VEHICLE_REGISTRY
+        for old, current in {"haval_jolion_15t": "haval_jolion_15t_4wd", "skoda_octavia_14tsi": "skoda_octavia_14tsi_dsg", "geely_coolray_15t": "geely_coolray_15t_fwd"}.items():
+            await connection.execute(text("UPDATE vehicle_trims SET base_city_fuel_rate_l100km=:rate, fuel_rate_source=:source WHERE id=:id AND base_city_fuel_rate_l100km IS NULL AND fuel_rate_source IS NULL"),
+                                     {"rate": VEHICLE_REGISTRY[old].base_city_fuel_rate_l100km, "source": "legacy_backend_declared_not_oem_verified:" + old, "id": current})
+        # Explicit product default approved for Haval Jolion. Do not replace
+        # consumption explicitly supplied by a user or a verified catalog import.
+        await connection.execute(text("UPDATE vehicle_trims SET base_city_fuel_rate_l100km=8.5, fuel_rate_source='product_default:twin-fuel-mvp-1' WHERE id IN ('haval_jolion_15t_4wd','haval_jolion_15t_fwd') AND (base_city_fuel_rate_l100km IS NULL OR fuel_rate_source='legacy_backend_declared_not_oem_verified:haval_jolion_15t')"))
diff --git a/app/core/dsp/filters.py b/app/core/dsp/filters.py
index 8c863c0..8c10edb 100644
--- a/app/core/dsp/filters.py
+++ b/app/core/dsp/filters.py
@@ -1,4 +1,4 @@
-﻿import numpy as np
+import numpy as np
 from scipy import signal
 from typing import Tuple, Union
 
diff --git a/app/main.py b/app/main.py
index 70a5a4d..96df0e2 100644
--- a/app/main.py
+++ b/app/main.py
@@ -39,11 +39,11 @@ from app.models.results import (
     TripHistoryResponse,
     TripSummaryItem
 )
-from app.models.db_models import User, Trip, VehicleMake, VehicleModel, VehicleTrim, FuelRegionalPrice, CPALead
+from app.models.db_models import User, Trip, VehicleMake, VehicleModel, VehicleTrim, FuelRegionalPrice, CPALead, TripTelemetryRecord
 from app.core.dsp.filters import SignalFilter
 from app.core.dsp.alignment import IMUAlignmentService
 from app.services.wear_engine import WearEngine
-from app.services.capture_storage import capture_quality, store_raw_capture, unscored_trip
+from app.services.capture_storage import capture_quality, store_raw_capture, unscored_trip, measured_distance
 
 try:
     from app.services.twin_engine import TwinEngine, AggressiveTwinEngine
@@ -157,7 +157,7 @@ class DashboardResponse(BaseModel):
     total_trips: int = Field(default=0, description="Всего завершенных поездок")
     total_distance_km: float = Field(default=0.0, description="Суммарный подтвержденный пробег, км")
     oil_remaining_percent: Optional[float] = Field(default=None, description="Остаточный ресурс масла, %")
-    ghost_twin_status: str = Field(default="Для сравнения нужна контрольная поездка", description="Статус модели сравнения")
+    ghost_twin_status: str = Field(default="базовый лихач", description="Статус модели сравнения")
     math_version: str = MATH_VERSION
     balance_provenance: str = 'legacy_or_user_reported_not_reference_validated'
     oil_resource_status: str = 'unavailable_without_validated_degradation_model'
@@ -177,6 +177,7 @@ class ProfileUpdateRequest(BaseModel):
     current_oil_wear_percent: Optional[float] = Field(default=None, ge=0.0, le=100.0)
     name: Optional[str] = Field(default=None, max_length=100)
     user_id: Optional[str] = None
+    fuel_consumption: Optional[float] = Field(default=None, gt=0, le=100)
 
 UserProfileUpdatePayload = ProfileUpdateRequest
 
@@ -249,7 +250,8 @@ async def resolve_car_profile_async(car_id: Optional[str], db: AsyncSession, use
             curb_weight_kg=trim.curb_weight_kg,rolling_resistance_coeff=trim.rolling_resistance_coeff,
             drag_coefficient_area=trim.drag_coefficient_area,drivetrain_efficiency=trim.drivetrain_efficiency,
             engine_displacement_l=trim.engine_displacement_l,rated_power_kw=trim.rated_power_kw,
-            oil_capacity_l=trim.oil_capacity_l)
+            oil_capacity_l=trim.oil_capacity_l,
+            base_city_fuel_rate_l100km=(user.custom_profile or {}).get("base_city_fuel_rate_l100km", trim.base_city_fuel_rate_l100km) if user and (user.custom_profile or {}).get("car_id") == car_id else trim.base_city_fuel_rate_l100km)
     if VEHICLE_REGISTRY and car_id in VEHICLE_REGISTRY:
         return VEHICLE_REGISTRY[car_id]
     # An unknown ID must not silently inherit the physics of a Haval.
@@ -461,26 +463,26 @@ async def get_vehicle_catalog(db: AsyncSession = Depends(get_db)):
     for trim, model, make in rows:
         catalog.append({
             "id": trim.id,
+            "make_id": make.id,
+            "model_id": model.id,
             "make": make.name,
             "model": model.name,
             "body_type": model.body_type,
             "trim": trim.badge_name,
             "badge_name": trim.badge_name,
             "full_name": f"{make.name} {model.name} {trim.badge_name}",
+            "base_city_fuel_rate_l100km": trim.base_city_fuel_rate_l100km,
+            "fuel_rate_source": trim.fuel_rate_source,
             "curb_weight_kg": trim.curb_weight_kg,
+            "engine_displacement_l": trim.engine_displacement_l,
+            "drivetrain_type": trim.drivetrain_type,
+            "transmission_type": trim.transmission_type,
             "oil_capacity_l": trim.oil_capacity_l,
             "oil_grade": trim.oil_grade,
             "oil_spec": trim.oil_spec,
             "recommended_oil_brand": trim.recommended_oil_brand
         })
 
-    if not catalog:
-        catalog = [
-            {"id": "haval_jolion_15t_4wd", "make": "Haval", "model": "Jolion", "body_type": "crossover", "trim": "1.5T 4WD", "badge_name": "1.5T 4WD 7DCT (150 л.с.)", "full_name": "Haval Jolion 1.5T 4WD", "curb_weight_kg": 1505, "oil_capacity_l": 3.8, "oil_grade": "0W-20", "recommended_oil_brand": "TotalEnergies Quartz 9000"},
-            {"id": "skoda_octavia_14tsi_dsg", "make": "Skoda", "model": "Octavia", "body_type": "liftback", "trim": "1.4 TSI", "badge_name": "1.4 TSI 7-DSG (150 л.с.)", "full_name": "Skoda Octavia 1.4 TSI", "curb_weight_kg": 1285, "oil_capacity_l": 4.0, "oil_grade": "0W-30", "recommended_oil_brand": "VAG LongLife III FE"},
-            {"id": "geely_coolray_15t_fwd", "make": "Geely", "model": "Coolray", "body_type": "crossover", "trim": "1.5T DCT", "badge_name": "1.5T 7DCT (150 л.с.)", "full_name": "Geely Coolray 1.5T", "curb_weight_kg": 1340, "oil_capacity_l": 4.0, "oil_grade": "0W-20", "recommended_oil_brand": "LUKOIL GENESIS ARMORTECH"}
-        ]
-
     return {"catalog": catalog, "total": len(catalog)}
 
 
@@ -681,6 +683,8 @@ async def _evaluate_reference_evidence(payload):
 
 
 async def _persist_math_evidence(db, payload, trip_id, result):
+    await db.flush()
+    db.add(TripTelemetryRecord(trip_id=trip_id, payload=[p.model_dump(mode="json") for p in payload.telemetry_stream]))
     if payload.reference_math is None:
         return
     from app.models.recordings import TripMathEvidenceRecord
@@ -711,6 +715,17 @@ def _calculate_session_values_sync(
     raw_az = np.array([p.az for p in stream], dtype=np.float64)
     speeds = np.array([p.speed for p in stream], dtype=np.float64)
 
+    # GPS is normally ~1 Hz while motion is ~50 Hz. Differentiating the
+    # sample-and-hold speed carried by each IMU point creates isolated impulses
+    # and makes forward-axis calibration fail even on a real acceleration.
+    # Interpolate actual adjacent fixes for DSP only; retain raw observations.
+    if payload.raw_recording is not None:
+        gps = payload.raw_recording.gps
+        if len(gps) >= 2 and all(p.speed_mps is not None and p.accuracy_m is not None and p.accuracy_m <= 50 for p in gps):
+            gps_times = np.array([p.timestamp_ms / 1000 for p in gps])
+            if np.max(np.diff(gps_times)) <= 10:
+                speeds = np.interp(times, gps_times, [p.speed_mps for p in gps])
+
     # 1. Вычисление реального шага времени dt по меткам времени (защита от плавающей частоты браузера)
     times -= times[0]
     raw_diffs = np.diff(times)
@@ -745,11 +760,12 @@ def _calculate_session_values_sync(
 
     # 3. Вертикальные удары: блокируем счет на месте (скорость строго >= 2.0 м/с = 7.2 км/ч)
     shock_mask = np.interp(grid, times, speeds) >= 2.0
-    shock_z_moving = np.where(shock_mask, shock_z, 0.0)
+    # Mask the event decision, not the signal: masking before differentiating
+    # produces false jerk at the 2 m/s boundary.
 
     if is_moving_session and hasattr(TwinEngine, "filter_road_anomalies"):
         clean_signed_ax, detected_bumps = TwinEngine.filter_road_anomalies(
-            filt_x, raw_az=shock_z_moving, dt=dt
+            filt_x, raw_az=shock_z, dt=dt, eligible_mask=shock_mask
         )
     else:
         clean_signed_ax, detected_bumps = filt_x, 0
@@ -786,7 +802,14 @@ def _calculate_session_values_sync(
     wear = WearEngine(profile=profile).evaluate_trip_wear(
         times_s=times, speed_mps=speeds, ax_mps2=clean_signed_ax
     )
-    savings = TwinEngine(profile=profile).evaluate_financial_delta()
+    if payload.raw_recording is not None:
+        distance_km = measured_distance(payload.raw_recording)
+    aligned_for_style = bool(aligned[3].get("is_aligned") or np.max(np.hypot(filt_x, aligned[1])) < 1.8)
+    from app.services.twin_engine import calculate_trip_smoothness_score
+    smooth_score = calculate_trip_smoothness_score(acceleration=clean_signed_ax) if aligned_for_style else None
+    savings = TwinEngine(profile=profile, fuel_price_rub=getattr(user, "fuel_price_rub", None) or 72.40).evaluate_financial_delta(
+        distance_km=distance_km, user_ax=clean_signed_ax)
+
     if distance_km == 0:
         harsh_accel_count = harsh_brake_count = detected_bumps = 0
     math_status = {
@@ -795,11 +818,12 @@ def _calculate_session_values_sync(
         "imu_alignment": aligned[3],
         "low_speed_time_is_not_engine_runtime": True,
         "dsp_interpolation": "linear_regular_grid_for_filtering_only",
-        "fuel_savings": {"status": savings["status"], "reason": savings["reason"]},
+        "fuel_savings": savings,
         "oil_wear": {"status": wear["status"], "reason": wear["reason"]},
-        "monetary_credit_status": "not_verified",
-        "style_score_status": "unavailable_without_validated_scoring_policy",
-        "counterfactual_status": "explicit_matched_reference_required",
+        "monetary_credit_status": "model_estimate" if savings["status"] == "model_estimate" else "not_verified",
+        "style_score_status": "descriptive_sample_fraction" if smooth_score is not None else "alignment_required",
+        "style_score_policy": "smooth-sample-fraction-1",
+        "counterfactual_status": "declared_product_multiplier_policy",
     }
     return {
         "duration_sec": duration_sec,
@@ -808,17 +832,17 @@ def _calculate_session_values_sync(
         "resolved_car_id": payload.car_id,
         "equiv_hours": None,
         "oil_wear_pct": None,
-        "total_savings_rub": None,
-        "fuel_saved_liters": None,
+        "total_savings_rub": savings["total_savings_rub"],
+        "fuel_saved_liters": savings["fuel_saved_liters"],
         "range_bonus_km": None,
         "oil_saved_rub": None,
-        "fuel_saved_rub": None,
+        "fuel_saved_rub": savings["fuel_saved_rub"],
         "autonomy_days": None,
         "harsh_accel_count": harsh_accel_count,
         "harsh_brake_count": harsh_brake_count,
         "rush_cost_rub": None,
         "base_potential_rub": None,
-        "smooth_score": None,
+        "smooth_score": smooth_score,
         "road_anomalies": int(detected_bumps),
         "hunting_detected": None,
         "traffic_jam_seconds": traffic_jam_seconds,
@@ -837,7 +861,7 @@ async def preview_telemetry_session(payload: TelemetryPreviewPayload,
         raise HTTPException(422, "Accurate GPS coverage is required for a provisional estimate")
     values = await _calculate_session_values(payload, db, owner, max_gap_seconds=2.0 * payload.sampling_stride)
     return {"status": "PREVIEW", "provisional": True, "session_id": payload.session_id,
-            "cost_savings_rub": None, "math_status": values["math_status"],
+            "cost_savings_rub": values["total_savings_rub"], "smooth_score": values["smooth_score"], "math_status": values["math_status"],
             "distance_km": round(values["distance_km"], 3),
             "duration_seconds": round(values["duration_sec"], 2),
             "sample_count": len(stream), "sampling_stride": payload.sampling_stride}
@@ -871,6 +895,8 @@ async def process_telemetry_session(
             excluded.add('reference_math')  # Preserve canonical hashes of pre-v2 queued requests.
         digest = hashlib.sha256(json.dumps(payload.model_dump(mode="json", exclude=excluded), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
         user = (await db.execute(select(User).where(User.id == owner.id).with_for_update().execution_options(populate_existing=True))).scalar_one()
+        if user.merged_into:
+            raise HTTPException(401, "Session was merged; sign in again")
         existing_trip = (await db.execute(select(Trip).where(or_(Trip.id == parsed_uuid, and_(Trip.user_id == owner.id, Trip.client_session_id == session_ref))))).scalar_one_or_none()
         if existing_trip is not None:
             if existing_trip.user_id != owner.id or (existing_trip.request_hash and existing_trip.request_hash != digest):
@@ -929,8 +955,12 @@ async def process_telemetry_session(
         hunting_detected = values["hunting_detected"]
         traffic_jam_seconds = values["traffic_jam_seconds"]
 
+        # The user row stays locked until the trip, telemetry and balances commit.
+        # A replay returns the stored receipt above and never reaches these credits.
         user.total_distance_km = float(user.total_distance_km) + distance_km
         user.total_trips += 1
+        user.total_savings_rub = round(user.total_savings_rub + (total_savings_rub or 0.0), 2)
+        user.total_fuel_saved_liters += fuel_saved_liters or 0.0
 
         new_trip = Trip(
             id=parsed_uuid,
@@ -1024,6 +1054,16 @@ async def process_telemetry_session(
 # Профиль, Дашборд, История, Аналитика и CPA-воронка
 # =====================================================================
 
+@app.get("/api/v1/users/{user_id}/progress")
+async def driver_progress(user_id: str, days: int = Query(7), utc_offset_minutes: int = Query(0, ge=-720, le=840),
+                          db: AsyncSession = Depends(get_db), owner: User = Depends(require_owner)):
+    from app.services.progress import summarize_progress
+    if days not in (7, 30):
+        raise HTTPException(422, "Supported periods: 7, 30")
+    trips = (await db.execute(select(Trip).where(Trip.user_id == user_id,
+             Trip.created_at >= datetime.now(timezone.utc) - timedelta(days=days * 2 + 1)))).scalars().all()
+    return summarize_progress(trips, days, utc_offset_minutes)
+
 @app.get("/api/v1/users/{user_id}/profile")
 async def get_user_vehicle_profile(
     user_id: str,
@@ -1049,7 +1089,7 @@ async def get_user_vehicle_profile(
         "user_id": user.id,
         "car_id": user.car_id or "haval_jolion_15t_4wd",
         "car_name": user.car_name,
-        "fuel_consumption": (user.custom_profile or {}).get("base_city_fuel_rate_l100km"),
+        "fuel_consumption": (await resolve_car_profile_async(user.car_id, db, user)).base_city_fuel_rate_l100km,
         "fuel_price_rub": user.fuel_price_rub if user.fuel_price_rub is not None else 72.40,
         "service_cost_rub": user.service_cost_rub if user.service_cost_rub is not None else 9500.0,
         "current_oil_wear_percent": user.current_oil_wear_percent if user.current_oil_wear_percent is not None else 0.0,
@@ -1090,7 +1130,7 @@ async def get_user_dashboard(user_id: str, db: AsyncSession = Depends(get_db),
         total_trips=total_trips,
         total_distance_km=round(total_dist, 2),
         oil_remaining_percent=remaining_oil,
-        ghost_twin_status="Для сравнения нужна контрольная поездка",
+        ghost_twin_status="базовый лихач",
         cpa_recommended=cpa_active,
         cpa_offer_text=cpa_text,
         range_bank_km=round(saved_range_km, 1),
@@ -1356,7 +1396,7 @@ async def update_user_vehicle_profile(
     db: AsyncSession = Depends(get_db),
     owner: User = Depends(require_owner)
 ):
-    user_stmt = select(User).where(User.id == user_id)
+    user_stmt = select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
     res = await db.execute(user_stmt)
     user = res.scalar_one_or_none()
 
@@ -1389,6 +1429,12 @@ async def update_user_vehicle_profile(
         if payload.current_oil_wear_percent is not None:
             user.current_oil_wear_percent = payload.current_oil_wear_percent
 
+    if payload.fuel_consumption is not None:
+        resolved = await resolve_car_profile_async(user.car_id, db, user)
+        user.custom_profile = {**resolved.model_dump(mode="json"), "car_name": user.car_name,
+                               "base_city_fuel_rate_l100km": payload.fuel_consumption,
+                               "fuel_rate_source": "user_declared"}
+
     await db.commit()
     await db.refresh(user)
 
diff --git a/app/models/db_models.py b/app/models/db_models.py
index c9e8dcd..6bff781 100644
--- a/app/models/db_models.py
+++ b/app/models/db_models.py
@@ -1,4 +1,4 @@
-﻿import uuid
+import uuid
 from datetime import datetime, timezone
 from sqlalchemy import (
     Column,
@@ -74,6 +74,8 @@ class VehicleTrim(Base):
     rolling_resistance_coeff = Column(Float, default=0.012)
     drivetrain_efficiency = Column(Float, default=0.90)
 
+    base_city_fuel_rate_l100km = Column(Float, nullable=True)
+    fuel_rate_source = Column(String(200), nullable=True)
     engine_displacement_l = Column(Float, nullable=False)
     rated_power_kw = Column(Float, nullable=False)
     drivetrain_type = Column(String(20), default="FWD")
@@ -96,6 +98,10 @@ class User(Base):
     name = Column(String(100), nullable=True)
     email = Column(String(128), unique=True, nullable=True)
     hashed_password = Column(String(256), nullable=True)
+    login = Column(String(15), nullable=True)
+    merged_into = Column(String(64), nullable=True)
+
+    __table_args__ = (Index("ix_users_login", "login", unique=True),)
 
     car_id = Column(String(100), default="haval_jolion_15t_4wd")
     car_name = Column(String(200), nullable=True)
@@ -199,3 +205,17 @@ class CPALead(Base):
     created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
 
     user = relationship("User", back_populates="leads")
+
+class LoginAttempt(Base):
+    __tablename__ = "login_attempts"
+    key = Column(String(64), primary_key=True)
+    failures = Column(Integer, nullable=False, default=0)
+    window_started_at = Column(DateTime(timezone=True), nullable=False)
+    blocked_until = Column(DateTime(timezone=True), nullable=True)
+
+
+class TripTelemetryRecord(Base):
+    """Immutable full derived stream; original GPS/IMU remain in RecordingChunk."""
+    __tablename__ = "trip_telemetry"
+    trip_id = Column(String(64), ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True)
+    payload = Column(JSON, nullable=False)
diff --git a/app/models/telemetry.py b/app/models/telemetry.py
index ad42200..ae16504 100644
--- a/app/models/telemetry.py
+++ b/app/models/telemetry.py
@@ -1,4 +1,4 @@
-﻿"""Finite, bounded legacy telemetry input; missing coordinates stay missing."""
+"""Finite, bounded legacy telemetry input; missing coordinates stay missing."""
 import uuid
 from typing import Optional, Literal
 from pydantic import BaseModel, ConfigDict, Field, model_validator
diff --git a/app/models/vehicle_profiles.py b/app/models/vehicle_profiles.py
index 50c4fb9..9fb9039 100644
--- a/app/models/vehicle_profiles.py
+++ b/app/models/vehicle_profiles.py
@@ -1,4 +1,4 @@
-﻿from typing import Dict, Optional
+from typing import Dict, Optional
 from pydantic import BaseModel, Field
 
 
@@ -39,7 +39,7 @@ VEHICLE_REGISTRY: Dict[str, VehiclePhysicalProfile] = {
         rolling_resistance_coeff=0.0135,
         drag_coefficient_area=0.36 * 2.38,
         drivetrain_efficiency=0.87,
-        base_city_fuel_rate_l100km=9.8,
+        base_city_fuel_rate_l100km=8.5,
         engine_displacement_l=1.5,
         rated_power_kw=110.0,
         oil_capacity_l=3.8,
diff --git a/app/schemas/vehicle.py b/app/schemas/vehicle.py
index c4ce440..c44ac6e 100644
--- a/app/schemas/vehicle.py
+++ b/app/schemas/vehicle.py
@@ -6,7 +6,7 @@ class CustomVehicleInput(BaseModel):
     model_config = {"extra": "allow", "allow_inf_nan": False, "populate_by_name": True}
     brand: str = Field(default="Custom", min_length=1, max_length=100)
     model: str = Field(default="Vehicle", min_length=1, max_length=100)
-    custom_name: str | None = Field(default=None, max_length=200)
+    custom_name: str | None = Field(default=None, min_length=1, max_length=12)
     body_type: str = Field(default="sedan", max_length=50)
     engine_displacement_l: float | None = Field(default=None, gt=0.5, le=8.0, validation_alias=AliasChoices("engine_displacement_l", "engine_disp_l"))
     engine_power_hp: float | None = Field(default=None, gt=30.0, le=1500.0, validation_alias=AliasChoices("engine_power_hp", "power_hp"))
diff --git a/app/services/capture_storage.py b/app/services/capture_storage.py
index ea65d94..3917bd1 100644
--- a/app/services/capture_storage.py
+++ b/app/services/capture_storage.py
@@ -46,16 +46,15 @@ def measured_distance(raw):
         dt = (current.timestamp_ms - previous.timestamp_ms) / 1000
         if not 0 < dt <= 10:
             continue
-        speeds = [previous.speed_mps, current.speed_mps]
-        if all(speed is not None and 0 <= speed <= 75 for speed in speeds):
-            distance += sum(speeds) * 0.5 * dt / 1000
-        else:
-            latitude = math.radians(current.latitude - previous.latitude)
-            longitude = math.radians(current.longitude - previous.longitude)
-            a = math.sin(latitude / 2) ** 2 + math.cos(math.radians(previous.latitude)) * math.cos(math.radians(current.latitude)) * math.sin(longitude / 2) ** 2
-            meters = 6371000 * 2 * math.atan2(math.sqrt(min(1, a)), math.sqrt(max(0, 1 - a)))
-            if 3 <= meters <= 75 * dt:
-                distance += meters / 1000
+        latitude = math.radians(current.latitude - previous.latitude)
+        longitude = math.radians(current.longitude - previous.longitude)
+        a = math.sin(latitude / 2) ** 2 + math.cos(math.radians(previous.latitude)) * math.cos(math.radians(current.latitude)) * math.sin(longitude / 2) ** 2
+        meters = 6371000 * 2 * math.atan2(math.sqrt(min(1, a)), math.sqrt(max(0, 1 - a)))
+        # Haversine is the distance source; speed only rejects stationary GPS drift.
+        stationary = previous.speed_mps is not None and current.speed_mps is not None and max(previous.speed_mps, current.speed_mps) < .5
+        minimum = .2 if previous.speed_mps is not None and current.speed_mps is not None else 3
+        if not stationary and minimum <= meters <= 75 * dt:
+            distance += meters / 1000
     return distance
 
 
@@ -89,12 +88,14 @@ def unscored_trip(payload, trip_id, digest, owner, quality):
     raw = payload.raw_recording
     duration = (raw.ended_at_ms - raw.started_at_ms) / 1000
     distance = measured_distance(raw)
+    stationary = distance <= .01
     result = {"session_id": trip_id, "duration_seconds": duration, "distance_km": distance,
-              "oil_wear_percent": None, "cost_savings_rub": None, "status": "RECORDED",
+              "oil_wear_percent": None, "cost_savings_rub": 0.0 if stationary else None, "fuel_saved_rub": 0.0 if stationary else None,
+              "fuel_saved_liters": 0.0 if stationary else None, "status": "RECORDED",
               "metrics": {"capture_quality": quality, "math_status": {
-                  "version":"reference-math-1", "fuel_savings":{"status":"unavailable","reason":"matched_reference_fuel_required"},
+                  "version":"reference-math-1", "fuel_savings":{"status":"model_estimate" if stationary else "insufficient_observations","reason":"distance_at_most_10m" if stationary else "incomplete_sensor_capture"},
                   "oil_wear":{"status":"unavailable","reason":"oil_condition_reference_required"},
-                  "monetary_credit_status":"not_verified"}}}
+                  "monetary_credit_status":"zero_distance" if stationary else "insufficient_observations"}}}
     trip = Trip(id=trip_id, client_session_id=payload.session_id, user_id=owner.id, car_id=payload.car_id,
                 created_at=datetime.fromtimestamp(raw.started_at_ms / 1000, tz=timezone.utc),
                 request_hash=digest, calculation_result=result, duration_seconds=duration, distance_km=distance,
diff --git a/app/services/gis_service.py b/app/services/gis_service.py
index ead973b..12e3de1 100644
--- a/app/services/gis_service.py
+++ b/app/services/gis_service.py
@@ -1,4 +1,4 @@
-﻿import os
+import os
 import json
 import hashlib
 import math
diff --git a/app/services/progress.py b/app/services/progress.py
new file mode 100644
index 0000000..da60c4f
--- /dev/null
+++ b/app/services/progress.py
@@ -0,0 +1,36 @@
+"""Calendar-day progress; only saved trips with a known scoring policy qualify."""
+from datetime import datetime, timedelta, timezone
+from statistics import mean
+
+POLICY = "smooth-sample-fraction-1"
+
+
+def summarize_progress(trips, days=7, utc_offset_minutes=0, now=None):
+    zone = timezone(timedelta(minutes=utc_offset_minutes))
+    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
+    start = today - timedelta(days=days - 1)
+    previous_start = start - timedelta(days=days)
+    current, previous, buckets = [], [], {}
+    for trip in trips:
+        result = trip.calculation_result or {}
+        score = result.get("smooth_score")
+        status = (result.get("metrics") or {}).get("math_status") or {}
+        if status.get("style_score_policy") != POLICY or not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
+            continue
+        date = trip.created_at.astimezone(zone).date()
+        if start <= date <= today:
+            current.append(score)
+            buckets.setdefault(date, []).append(score)
+        elif previous_start <= date < start:
+            previous.append(score)
+    # One trip is an observation, not evidence of a trend across days.
+    enough = len(buckets) >= 2
+    return {"days": days, "policy": POLICY, "trip_count": len(current),
+            "average": round(mean(current), 1) if current else None,
+            "previous_average": round(mean(previous), 1) if previous else None,
+            "delta": round(mean(current) - mean(previous), 1) if enough and len(previous) >= 2 else None,
+            "sufficient_data": enough,
+            "points": [{"date": (start + timedelta(days=i)).isoformat(),
+                        "score": round(mean(buckets[start + timedelta(days=i)]), 1) if start + timedelta(days=i) in buckets else None}
+                       for i in range(days)],
+            "period_start": start.isoformat(), "period_end": today.isoformat()}
diff --git a/app/services/twin_engine.py b/app/services/twin_engine.py
index 61b44ce..796d517 100644
--- a/app/services/twin_engine.py
+++ b/app/services/twin_engine.py
@@ -28,7 +28,8 @@ class TwinEngine:
     def filter_road_anomalies(
         user_ax: np.ndarray,
         raw_az: Optional[np.ndarray] = None,
-        dt: float = 0.02
+        dt: float = 0.02,
+        eligible_mask=None
     ) -> Tuple[np.ndarray, int]:
         """Z-Shock Veto: подавление ложных перегрузок при наезде на лежачие полицейские и ямы."""
         if not np.isfinite(dt) or dt <= 0:
@@ -45,17 +46,22 @@ class TwinEngine:
             az = np.asarray(raw_az, dtype=float)
             az = az - np.median(az)  # Accept gravity-inclusive and linear vertical inputs.
             jerk_z = np.gradient(az, dt) if n > 1 else np.zeros(n)
-            impact_mask = (np.abs(az) > 3.5) | (np.abs(jerk_z) > 15.0)
-
-            i = 0
-            while i < n:
+            impact_mask = (np.abs(az) > 3.5) | ((np.abs(az) > .8) & (np.abs(jerk_z) > 15.0))
+            if eligible_mask is not None:
+                eligible = np.asarray(eligible_mask, dtype=bool)
+                if eligible.shape != az.shape:
+                    raise ValueError('Shock eligibility must align with acceleration')
+                impact_mask &= eligible
+            armed, quiet, last = True, 0.0, -1.0
+            for i in range(n):
+                quiet = quiet + dt if abs(az[i]) < .5 else 0.0
+                if quiet >= .12:
+                    armed = True
                 if impact_mask[i]:
-                    anomalies_count += 1
-                    end_idx = min(n, i + suppression_window)
-                    clean_ax[i:end_idx] = 0.0
-                    i = end_idx
-                else:
-                    i += 1
+                    clean_ax[max(0, i-int(.1/dt)):min(n, i+suppression_window)] = 0.0
+                    if armed and i*dt-last >= .7:
+                        anomalies_count += 1
+                        armed, last = False, i*dt
         else:
             jerk_x = np.gradient(clean_ax, dt) if n > 1 else np.zeros(n)
             spike_mask = (np.abs(clean_ax) > 2.5) & (np.abs(jerk_x) > 20.0)
@@ -100,11 +106,13 @@ class TwinEngine:
     def calculate_cmem_traction_power(self, speed_mps, ax_mps2):
         raise ValueError('Use road_energy with explicit slope, wind, rotating mass and road-load parameters')
 
-    def evaluate_financial_delta(self, *args, comparison=None, fuel_price_rub=None, **kwargs):
-        """Only explicit matched fuel observations support a signed comparison.
+    def evaluate_financial_delta(self, *args, comparison=None, fuel_price_rub=None,
+                                 distance_km=None, user_ax=None, **kwargs):
+        """Evaluate the approved MVP fuel multiplier policy on cleaned user_ax.
 
-        Legacy speed, distance and oil arguments cannot manufacture liters or rubles.
-        A numerical comparison of caller declarations is not a verified causal saving.
+        K_user = 1 + mean(user_ax > 1.8) * 2.2; K_twin = 1.66.
+        Distance <= 10 m earns exactly zero. Matched observations remain an
+        independent explicit comparison path for research callers.
         """
         from app.services.reference_math import VERSION, compare_fuel
         result = {'status':'unavailable','reason':'matched_reference_fuel_required','math_version':VERSION,
@@ -117,6 +125,33 @@ class TwinEngine:
             result.update(compared, fuel_saved_rub=compared['delta_cost_rub'],fuel_savings_rub=compared['delta_cost_rub'],
                           total_savings_rub=compared['delta_cost_rub'],cost_savings_rub=compared['delta_cost_rub'],
                           saved_fuel_liters=compared['delta_fuel_l'],fuel_saved_liters=compared['delta_fuel_l'],reason=None)
+        elif distance_km is not None and user_ax is not None:
+            distance = float(distance_km)
+            ax = np.asarray(user_ax, dtype=float)
+            if not np.isfinite(distance) or distance < 0 or ax.ndim != 1 or not np.all(np.isfinite(ax)):
+                raise ValueError('Finite nonnegative distance and finite acceleration samples are required')
+            rate = getattr(self.profile, 'base_city_fuel_rate_l100km', None)
+            price = fuel_price_rub if fuel_price_rub is not None else (self.fuel_price if self.fuel_price is not None else 72.40)
+            if distance > .01 and (not len(ax) or rate is None or price is None):
+                result.update(reason='declared_fuel_rate_price_and_acceleration_required')
+                return result
+            if rate is not None and (not np.isfinite(rate) or not 0 < rate <= 100):
+                raise ValueError('Fuel rate must be in (0, 100] l/100km')
+            if price is not None and (not np.isfinite(price) or not 0 < price <= 10000):
+                raise ValueError('Fuel price must be in (0, 10000] rub/l')
+            hard = int(np.count_nonzero(ax > 1.8))
+            fraction = hard / max(1, len(ax))
+            multiplier = 1.0 + fraction * 2.2
+            delta = max(0.0, 1.66 - multiplier)
+            liters = 0.0 if distance <= .01 else distance / 100 * rate * delta
+            rubles = round(liters * price, 2)
+            result.update(status='model_estimate', reason=None, policy_version='twin-fuel-mvp-1',
+                          n_hard=hard, n_total=max(1, len(ax)), stress_fraction=fraction,
+                          k_user=multiplier, k_twin=1.66, distance_km=distance,
+                          base_city_fuel_rate_l100km=rate, price_rub_per_l=price,
+                          fuel_saved_rub=rubles, fuel_savings_rub=rubles, total_savings_rub=rubles,
+                          cost_savings_rub=rubles, saved_fuel_liters=liters, fuel_saved_liters=liters,
+                          causal_savings_verified=False, validation_status='not_established_by_real_obd_logs')
         return result
 
     simulate_twin_and_delta = evaluate_financial_delta
@@ -125,5 +160,15 @@ class TwinEngine:
 AggressiveTwinEngine = TwinEngine
 
 def calculate_trip_smoothness_score(*args, **kwargs):
-    """No validated mapping from event rates to a universal 0..100 score exists."""
-    return None
+    """Product indicator: percent of valid samples within maneuver thresholds.
+
+    This is an explicit descriptive policy, not a safety or fuel certification.
+    Legacy count-only calls cannot determine the fraction of smooth samples.
+    """
+    signal = kwargs.get('acceleration')
+    if signal is None:
+        return None
+    values = np.asarray(signal, dtype=float)
+    if not len(values) or not np.all(np.isfinite(values)):
+        return None
+    return int(np.floor(100 * np.mean((values <= 2.0) & (values >= -2.3)) + .5))
diff --git a/apply_complete_fix.py b/apply_complete_fix.py
index ced864d..54ce9fe 100644
--- a/apply_complete_fix.py
+++ b/apply_complete_fix.py
@@ -1,168 +1,6 @@
-﻿import os
-import glob
-import re
+"""Retired: this historical patch corrupted the recorder and discarded raw data.
 
-# 1. Исправление окончаний строк CRLF -> LF в bash-скриптах deploy/
-for sh_file in glob.glob("deploy/*.sh"):
-    with open(sh_file, "rb") as f:
-        data = f.read().replace(b"\r\n", b"\n")
-    with open(sh_file, "wb") as f:
-        f.write(data)
-print("[OK] deploy/*.sh: окончания строк переведены в Unix LF.")
-
-# 2. Калибровка app/main.py под 100% прохождение тестов и поддержку клиента
-main_path = "app/main.py"
-with open(main_path, "r", encoding="utf-8-sig") as f:
-    code = f.read()
-
-# а) Восстанавливаем строгий контракт эндпоинта /preview (422 при шуме GPS и None для начислений)
-preview_regex = re.compile(
-    r'@app\.post\("/api/v1/telemetry/preview"\)\s*async def preview_telemetry_session\(.*?\):\s*.*?return \{.*?\}',
-    re.DOTALL
-)
-canonical_preview = """@app.post("/api/v1/telemetry/preview")
-async def preview_telemetry_session(payload: TelemetryPreviewPayload,
-                                    db: AsyncSession = Depends(get_db),
-                                    owner: User = Depends(require_user)):
-    if payload.user_id != owner.id:
-        raise HTTPException(403, "Access to another user is forbidden")
-    stream = payload.telemetry_stream
-    if sum(p.lat is not None and p.lon is not None for p in stream) / len(stream) < 0.9:
-        raise HTTPException(422, "Accurate GPS coverage is required for a provisional estimate")
-    values = await _calculate_session_values(payload, db, owner, max_gap_seconds=2.0 * payload.sampling_stride)
-    return {"status": "PREVIEW", "provisional": True, "session_id": payload.session_id,
-            "cost_savings_rub": None, "math_status": values["math_status"],
-            "distance_km": round(values["distance_km"], 3),
-            "duration_seconds": round(values["duration_sec"], 2),
-            "sample_count": len(stream), "sampling_stride": payload.sampling_stride}"""
-
-code = preview_regex.sub(canonical_preview, code, count=1)
-
-# б) Фильтрация кочек на месте: блокируем счет при скорости < 2.0 м/с (7.2 км/ч)
-old_bump_code = """    if is_moving_session and hasattr(TwinEngine, "filter_road_anomalies"):
-        clean_signed_ax, detected_bumps = TwinEngine.filter_road_anomalies(
-            filt_x, raw_az=shock_z, dt=dt
-        )
-    else:
-        clean_signed_ax, detected_bumps = filt_x, 0"""
-
-new_bump_code = """    # Отсекаем подсчет неровностей на месте (скорость строго >= 2.0 м/с = 7.2 км/ч)
-    shock_mask = np.interp(grid, times, speeds) >= 2.0
-    shock_z_moving = np.where(shock_mask, shock_z, 0.0)
-
-    if is_moving_session and hasattr(TwinEngine, "filter_road_anomalies"):
-        clean_signed_ax, detected_bumps = TwinEngine.filter_road_anomalies(
-            filt_x, raw_az=shock_z_moving, dt=dt
-        )
-    else:
-        clean_signed_ax, detected_bumps = filt_x, 0"""
-
-if old_bump_code in code:
-    code = code.replace(old_bump_code, new_bump_code, 1)
-
-# в) Возврат проверки частоты семплирования (>= 1 мс) и пауз телеметрии
-old_gap_variant = """    clipped_diffs = np.clip(raw_diffs, 0.001, 1.0)
-    dt = float(np.median(clipped_diffs)) if len(clipped_diffs) > 0 else 0.02
-    if not math.isfinite(dt) or dt < 0.001:
-        dt = 0.02"""
-
-new_gap_variant = """    dt = float(np.mean(raw_diffs))
-    if not math.isfinite(dt) or dt < 0.001:
-        raise HTTPException(
-            422, "Telemetry sampling period must be at least one millisecond"
-        )
-    if np.max(raw_diffs) > max_gap_seconds:
-        raise HTTPException(422, "Telemetry contains gaps longer than two seconds")"""
-
-if old_gap_variant in code:
-    code = code.replace(old_gap_variant, new_gap_variant, 1)
-
-# г) Возврат статуса RECORDED для unscored_trip при дефектах захвата сенсоров
-code = code.replace(
-    'if quality["analysis_status"] != "available" and points_count < 50:',
-    'if quality["analysis_status"] != "available":'
-)
-
-# д) Безопасность дашборда: остаток ресурса масла и CPA-триггер
-code = code.replace(
-    "remaining_oil = round(max(0.0, 100.0 - wear_val), 1)",
-    "remaining_oil = None"
-)
-code = code.replace(
-    "cpa_active = remaining_oil <= 20.0",
-    "cpa_active = False"
-)
-code = code.replace(
-    "est_autonomy_days = round(saved_liters / 4.2, 1)",
-    "est_autonomy_days = None"
-)
-
-# е) Возврат эталонного контракта хранения баланса
-old_user_totals = """        user.total_distance_km = round(float(user.total_distance_km) + distance_km, 3)
-        user.total_trips += 1
-        user.total_savings_rub = round(float(user.total_savings_rub) + float(total_savings_rub or 0.0), 2)
-        user.total_fuel_saved_liters = round(float(user.total_fuel_saved_liters) + float(fuel_saved_liters or 0.0), 3)
-        user.range_bank_km = round(float(user.range_bank_km) + float(range_bonus_km or 0.0), 2)
-        user.current_oil_wear_percent = min(100.0, round(float(user.current_oil_wear_percent) + float(oil_wear_pct or 0.0), 4))"""
-
-new_user_totals = """        user.total_distance_km = float(user.total_distance_km) + distance_km
-        user.total_trips += 1"""
-
-if old_user_totals in code:
-    code = code.replace(old_user_totals, new_user_totals, 1)
-
-# ж) Возврат контракта values в _calculate_session_values_sync
-old_sync_return = re.compile(
-    r'    return \{\s*"duration_sec": duration_sec,.*?"math_status": math_status,\s*\}',
-    re.DOTALL
-)
-canonical_sync_return = """    traffic_jam_seconds = kinematics["below_threshold_s"]
-    idle_ratio = traffic_jam_seconds / duration_sec
-    wear = WearEngine(profile=profile).evaluate_trip_wear(
-        times_s=times, speed_mps=speeds, ax_mps2=clean_signed_ax
-    )
-    savings = TwinEngine(profile=profile).evaluate_financial_delta()
-    if distance_km == 0:
-        harsh_accel_count = harsh_brake_count = detected_bumps = 0
-    math_status = {
-        "version": MATH_VERSION,
-        "kinematics": kinematics,
-        "imu_alignment": aligned[3],
-        "low_speed_time_is_not_engine_runtime": True,
-        "dsp_interpolation": "linear_regular_grid_for_filtering_only",
-        "fuel_savings": {"status": savings["status"], "reason": savings["reason"]},
-        "oil_wear": {"status": wear["status"], "reason": wear["reason"]},
-        "monetary_credit_status": "not_verified",
-        "style_score_status": "unavailable_without_validated_scoring_policy",
-        "counterfactual_status": "explicit_matched_reference_required",
-    }
-    return {
-        "duration_sec": duration_sec,
-        "distance_km": distance_km,
-        "idle_ratio": idle_ratio,
-        "resolved_car_id": payload.car_id,
-        "equiv_hours": None,
-        "oil_wear_pct": None,
-        "total_savings_rub": None,
-        "fuel_saved_liters": None,
-        "range_bonus_km": None,
-        "oil_saved_rub": None,
-        "fuel_saved_rub": None,
-        "autonomy_days": None,
-        "harsh_accel_count": harsh_accel_count,
-        "harsh_brake_count": harsh_brake_count,
-        "rush_cost_rub": None,
-        "base_potential_rub": None,
-        "smooth_score": None,
-        "road_anomalies": int(detected_bumps),
-        "hunting_detected": None,
-        "traffic_jam_seconds": traffic_jam_seconds,
-        "math_status": math_status,
-    }"""
-
-code = old_sync_return.sub(canonical_sync_return, code, count=1)
-
-compile(code, main_path, "exec")
-with open(main_path, "w", encoding="utf-8") as f:
-    f.write(code)
-print("[OK] app/main.py: успешно скомпилирован и приведен к эталону.")
+Use deploy/update.ps1 or deploy/production.sh; application source is already fixed.
+"""
+if __name__ == "__main__":
+    raise SystemExit("This obsolete patch must not be reapplied. Use deploy/update.ps1 or deploy/production.sh update.")
diff --git a/deploy/update.ps1 b/deploy/update.ps1
new file mode 100644
index 0000000..b587c1e
--- /dev/null
+++ b/deploy/update.ps1
@@ -0,0 +1,144 @@
+#requires -Version 5.1
+<##
+.SYNOPSIS
+Updates autoXAK with a database backup and readiness check.
+.EXAMPLE
+powershell -ExecutionPolicy Bypass -File deploy/update.ps1 -SshHost root@185.149.145.234 -RemotePath /opt/autoxak-production
+.EXAMPLE
+powershell -ExecutionPolicy Bypass -File deploy/update.ps1 -Mode Copy -ComposeFile docker-compose.yml -EnvFile .env -HealthUrl https://localhost:8000/ready -HealthCaFile /app/cert.pem
+##>
+[CmdletBinding()]
+param(
+    [ValidateSet('Rebuild','Copy')][string]$Mode = 'Rebuild',
+    [string]$ComposeFile = 'compose.production.yml',
+    [string]$EnvFile = '.env.production',
+    [string]$SshHost,
+    [string]$RemotePath = '/opt/autoxak-production',
+    [string]$HealthUrl = 'http://127.0.0.1:8000/ready',
+    [string]$HealthCaFile = '',
+    [ValidateRange(5,600)][int]$HealthTimeout = 180
+)
+Set-StrictMode -Version Latest
+$ErrorActionPreference = 'Stop'
+$PSNativeCommandUseErrorActionPreference = $false
+
+function Invoke-Checked([string]$Program, [string[]]$Arguments) {
+    $result = & $Program @Arguments
+    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE). Update aborted." }
+    return $result
+}
+
+if ($SshHost) {
+    if ($SshHost -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.:-]+$' -or $RemotePath -notmatch '^/[a-zA-Z0-9_./-]+$') {
+        throw 'Use user@host and an absolute remote path containing letters, digits, /, _, . or -.'
+    }
+    if ($Mode -ne 'Rebuild') { throw 'SSH deployments use Rebuild. Copy is for local writable Docker containers.' }
+    # The production helper backs up PostgreSQL, pulls with --ff-only and rebuilds
+    # the API image. It preserves .env.production and all data volumes.
+    Invoke-Checked 'ssh' @('-T','-o','ConnectTimeout=15','--',$SshHost,"cd -- '$RemotePath' && bash deploy/production.sh update && bash deploy/production.sh check") | Out-Host
+    exit 0
+}
+
+$repo = Split-Path -Parent $PSScriptRoot
+Push-Location $repo
+$oldTag = [Environment]::GetEnvironmentVariable('APP_IMAGE_TAG','Process')
+$backup = $null
+$changed = $false
+$container = $null
+$rollbackFile = $null
+try {
+    $composePath = (Resolve-Path -LiteralPath $ComposeFile).Path
+    $envPath = (Resolve-Path -LiteralPath $EnvFile).Path
+    $composeArgs = @('compose','--env-file',$envPath,'-f',$composePath)
+    Invoke-Checked 'docker' ($composeArgs + @('config','--quiet')) | Out-Host
+    $container = (Invoke-Checked 'docker' ($composeArgs + @('ps','-q','api')) | Out-String).Trim()
+    if (-not $container -or $container -match '\s') { throw 'Expected exactly one running API container. Start the stack first.' }
+    $readOnly = (Invoke-Checked 'docker' @('inspect','--format','{{.HostConfig.ReadonlyRootfs}}',$container) | Out-String).Trim()
+    if ($Mode -eq 'Copy' -and $readOnly -eq 'true') {
+        throw 'API filesystem is read-only. Use -Mode Rebuild for production.'
+    }
+    if ($Mode -eq 'Copy') {
+        $containerUser = (Invoke-Checked 'docker' @('inspect','--format','{{.Config.User}}',$container) | Out-String).Trim()
+        if ($containerUser -and $containerUser -notin @('root','0','0:0')) {
+            throw 'Copy requires a root development container to preserve readable files. Use -Mode Rebuild for a non-root API.'
+        }
+    }
+    $oldImage = (Invoke-Checked 'docker' @('inspect','--format','{{.Image}}',$container) | Out-String).Trim()
+    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
+    $backup = Join-Path $repo ('backups/update-' + $stamp)
+    New-Item -ItemType Directory -Path $backup -Force | Out-Null
+    Set-Content -LiteralPath (Join-Path $backup 'previous-image.txt') -Value $oldImage -Encoding Ascii
+    # Write the binary dump inside PostgreSQL, then docker cp it. PowerShell 5.1
+    # text redirection corrupts binary pg_dump output, so it is never used here.
+    $dbContainer = (Invoke-Checked 'docker' ($composeArgs + @('ps','-q','postgres')) | Out-String).Trim()
+    if (-not $dbContainer) { throw 'PostgreSQL container is not running.' }
+    $dumpPath = '/tmp/autoxak-update-' + $stamp + '.dump'
+    $dumpCommand = 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file=' + $dumpPath + ' && pg_restore --list ' + $dumpPath + ' >/dev/null'
+    try {
+        Invoke-Checked 'docker' @('exec',$dbContainer,'sh','-ec',$dumpCommand) | Out-Host
+        Invoke-Checked 'docker' @('cp',"${dbContainer}:$dumpPath",(Join-Path $backup 'database.dump')) | Out-Host
+        if ((Get-Item -LiteralPath (Join-Path $backup 'database.dump')).Length -eq 0) { throw 'Database backup is empty.' }
+    } finally {
+        & docker exec $dbContainer rm -f $dumpPath | Out-Null
+    }
+    if ($Mode -eq 'Rebuild') {
+        $env:APP_IMAGE_TAG = 'update-' + $stamp.ToLowerInvariant()
+        Invoke-Checked 'docker' ($composeArgs + @('build','api')) | Out-Host
+        $changed = $true
+        Invoke-Checked 'docker' ($composeArgs + @('up','-d','--no-deps','--wait','--wait-timeout',"$HealthTimeout",'api')) | Out-Host
+        $container = (Invoke-Checked 'docker' ($composeArgs + @('ps','-q','api')) | Out-String).Trim()
+    } else {
+        # Copy complete directories, including new modules. .env, certificates,
+        # database volumes and logs are deliberately outside the copied paths.
+        $paths = @('app','static','deploy/entrypoint.py','seed_fleet.py','seed_fuel_prices.py')
+        foreach ($path in $paths) {
+            $destination = Join-Path $backup $path
+            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
+            Invoke-Checked 'docker' @('cp',"${container}:/app/$path",$destination) | Out-Host
+        }
+        Invoke-Checked 'docker' ($composeArgs + @('stop','--timeout','100','api')) | Out-Host
+        $changed = $true
+        foreach ($path in $paths) {
+            $source = Join-Path $repo $path
+            # A directory's /. suffix copies its contents, avoiding app/app.
+            $target = '/app/' + $path
+            if (Test-Path -LiteralPath $source -PathType Container) { $source += '/.' }
+            Invoke-Checked 'docker' @('cp',$source,"${container}:$target") | Out-Host
+        }
+        Invoke-Checked 'docker' ($composeArgs + @('start','api')) | Out-Host
+    }
+    $probe = 'import sys, ssl, urllib.request; ctx=ssl.create_default_context(cafile=sys.argv[2]) if len(sys.argv)>2 and sys.argv[2] else None; r=urllib.request.urlopen(sys.argv[1],context=ctx,timeout=4); assert r.status==200; assert b"ready" in r.read()'
+    $deadline = [DateTime]::UtcNow.AddSeconds($HealthTimeout)
+    $ready = $false
+    do {
+        & docker exec $container python -c $probe $HealthUrl $HealthCaFile 2>$null | Out-Null
+        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
+        Start-Sleep -Seconds 2
+    } while ([DateTime]::UtcNow -lt $deadline)
+    if (-not $ready) { throw 'API readiness check failed.' }
+    Write-Host "Update complete. Backup: $backup"
+} catch {
+    $failure = $_
+    if ($changed -and $backup) {
+        Write-Warning 'Update failed. Restoring the previous application; the database backup is retained.'
+        try {
+            if ($Mode -eq 'Rebuild') {
+                $rollbackFile = Join-Path $backup 'rollback.json'
+                @{services=@{api=@{image=$oldImage}}} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $rollbackFile -Encoding Ascii
+                Invoke-Checked 'docker' ($composeArgs + @('-f',$rollbackFile,'up','-d','--no-deps','--no-build','--wait','--wait-timeout',"$HealthTimeout",'api')) | Out-Host
+            } else {
+                Invoke-Checked 'docker' ($composeArgs + @('stop','--timeout','100','api')) | Out-Host
+                foreach ($path in $paths) {
+                    $source = Join-Path $backup $path
+                    if (Test-Path -LiteralPath $source -PathType Container) { $source += '/.' }
+                    Invoke-Checked 'docker' @('cp',$source,"${container}:/app/$path") | Out-Host
+                }
+                Invoke-Checked 'docker' ($composeArgs + @('start','api')) | Out-Host
+            }
+        } catch { Write-Warning "Application rollback failed. Backup is at $backup. $($_.Exception.Message)" }
+    }
+    throw $failure
+} finally {
+    [Environment]::SetEnvironmentVariable('APP_IMAGE_TAG',$oldTag,'Process')
+    Pop-Location
+}
diff --git a/docs/PRODUCT_EPICS_AUDIT.md b/docs/PRODUCT_EPICS_AUDIT.md
new file mode 100644
index 0000000..49d03e6
--- /dev/null
+++ b/docs/PRODUCT_EPICS_AUDIT.md
@@ -0,0 +1,169 @@
+# Аудит продуктовых эпиков и расчёта топлива
+
+Проверена ветка `fix/product-epics-audit` относительно `main` (`a411094`).
+Основание: PRODUKT-0001…0009, приложенные изображения Figma и последнее
+уточнение владельца о начислении денег непосредственно в POST поездки.
+Существующие FastAPI, PostgreSQL, PWA и визуальная структура сохранены.
+
+## Исправленные причины сбоев
+
+- В `index.html` исправлена синтаксическая ошибка JavaScript, блокировавшая
+  инициализацию поездки. Старый `apply_complete_fix.py`, вновь вносивший эту
+  ошибку и удалявший исходные данные, выведен из использования.
+- Убрана подмена нативного GPS глобальной обёрткой. На поездку создаётся один
+  `watchPosition`; идентификатор `0` тоже корректно освобождается.
+- GPS около 1 Гц и IMU около 50 Гц согласуются на сервере по времени.
+  Удерживаемая между GPS-отсчётами скорость больше не превращает разгон
+  в единичные импульсы производной, мешавшие определению продольной оси.
+  Интерполяция применяется только при обработке; исходные точки сохраняются.
+- Неровности учитываются при скорости от 2 м/с. Смена признака движения сама
+  по себе не создаёт вертикальный удар; длительный удар считается одним событием.
+- Гравитация и наклон отделяются от динамического ускорения. На стоянке
+  движение телефона не включает красный HUD и не накручивает неровности.
+- Дистанция исходной записи считается по Haversine. Плохая точность, интервалы
+  более 10 секунд, телепортации быстрее 75 м/с и неподвижный GPS-дрейф исключаются.
+- Полные массивы IMU/GPS и обработанная телеметрия сохраняются в PostgreSQL.
+  Ограничение размера предпросмотра не сокращает итоговую запись.
+- Сохранение поездки, исходных точек и изменение баланса выполняются в одной
+  транзакции с блокировкой пользователя. Повторный `session_id` с теми же
+  данными возвращает прежний результат; изменённые данные дают 409.
+- Исправлена обработка таймаута чтения JSON: прерванный ответ не выдаётся за
+  успешный ответ с пустым телом. Повреждённый JSON ошибки допускает `data=null`.
+- Очередь IndexedDB удаляется после серверного подтверждения. Сбой локального
+  и серверного сохранения сохраняет исходный payload в памяти для повторного
+  свайпа. Черновики восстанавливаются, активная вкладка не считается прерванной.
+
+## Соответствие эпикам
+
+| Эпик | Реализация |
+|---|---|
+| 0001 | Гость, вход, регистрация; восстановление сессии. OAuth скрыт в интерфейсе, серверные маршруты сохранены. |
+| 0002 | Логин 5–15 латинских букв, пароль 8–15 символов, показ пароля, ошибка занятого логина, сохранение введённых значений. |
+| 0003 | Проверка пароля с учётом регистра, защита повторной отправки, блокировка после 4 ошибок на 300 секунд; 429 и Retry-After. |
+| 0004 | Выбранный автомобиль, копилка из БД, переход в историю, начальное +0 ₽, естественное расположение кнопки поездки. |
+| 0005 | Независимые разрешения iOS Motion и GPS, запись при отказе GPS, таймер, статусы, подсветка, Wake Lock, Pointer Events для завершения. |
+| 0006 | Итог +X.XX ₽, шесть показателей, индекс плавности, локальное сохранение, восстановление итогов, единственная кнопка закрытия. Начисление выполняется уже в POST согласно последнему уточнению владельца. |
+| 0007 | Живой каталог PostgreSQL: марка → модель → модификация; ручной автомобиль и сохранение параметров, векторный силуэт 280×135. |
+| 0008 | Только подтверждённые сервером поездки в списке, обратная хронология, подробности, пустое состояние; несинхронизированные записи показаны отдельным статусом. |
+| 0009 | Средние за 7/30 календарных дней, сравнение с предыдущим периодом, график только реальных значений; отсутствующие дни не заполняются фиктивными точками. |
+
+Гостевой аккаунт при регистрации обновляется на месте. При входе в существующий
+аккаунт записи и накопления переносятся атомарно, повторное слияние не повторяет
+начисление. Старый гостевой токен после переноса отклоняется.
+
+## Канонический расчёт TwinEngine
+
+Для полного массива очищенного продольного ускорения `user_ax`:
+
+```python
+n_hard = np.sum(user_ax > 1.8)
+n_total = max(1, len(user_ax))
+k_user = 1.0 + (n_hard / n_total) * 2.2
+k_twin = 1.66
+fuel_multiplier_delta = max(0.0, k_twin - k_user)
+saved_liters = distance_km / 100.0 * base_city_fuel_rate_l100km * fuel_multiplier_delta
+delta_rub = round(saved_liters * fuel_price_rub, 2)
+```
+
+При `distance_km <= 0.01` литры и рубли строго равны нулю. Отрицательного
+начисления нет. Порог 1.8 является порогом **отсчётов**, а не числом манёвров.
+Формула не ограничивается искусственным диапазоном рублей за километр.
+
+Для 10 км, 8.5 л/100 км и 72.40 ₽/л:
+
+| Доля отсчётов выше 1.8 | K_user | Литры | Рубли |
+|---|---:|---:|---:|
+| 0% | 1.00 | 0.561 | 40.62 |
+| 10% | 1.22 | 0.374 | 27.08 |
+| 30% | 1.66 | 0 | 0.00 |
+| 90% | 2.98 | 0 | 0.00 |
+
+Городской расход берётся из профиля конкретного автомобиля. Для Haval Jolion
+добавлено явно утверждённое продуктовое значение 8.5 л/100 км; цена по умолчанию
+72.40 ₽/л. В гараже расход можно уточнить; выбор другого автомобиля без заполненного расхода не сохраняется, чтобы поездка не заканчивалась без рублей из-за пропущенного параметра. Значения других автомобилей не
+подставляются из массы или объёма двигателя. Ранее существовавшие значения
+Skoda/Geely имеют явную отметку происхождения, а не статус проверенных OEM-данных.
+
+Это утверждённая продуктовая модель сравнения. Масса, CdA и КПД сохраняются в
+профиле и доступны отдельному физическому ядру; приведённая формула не выдаётся
+за моделирование расхода двигателя с картой КПД или за измеренный расход.
+Версия политики — `twin-fuel-mvp-1`. В результате сохранены входные значения,
+число отсчётов, доля стресса и множители для проверки начисления.
+
+Скрипт проверяет численную реализацию. Подтверждения MAPE ≤ 10% по реальным
+парным OBD/CAN-логам в репозитории нет; такой результат не заявляется.
+Отдельные исследовательские API v3 не изменяют баланс автоматически.
+
+## Индекс плавности и качество
+
+`smooth-sample-fraction-1` — округлённая доля отсчётов в диапазоне
+−2.3…+2.0 м/с², выраженная в процентах. Это описательный показатель телеметрии.
+При неопределённой продольной оси и существенных горизонтальных движениях
+индекс/манёвры не выдаются за достоверные. Для графика нужны поездки минимум
+в два разных дня; для дельты — также данные предыдущего периода.
+Старые значения с неизвестной методикой не включаются в новый прогресс.
+
+При отсутствии разрешений/исходных данных запись всё равно сохраняется.
+Неизмеренные показатели отображаются прочерками; короткая запись с нулевой
+подтверждённой дистанцией не приносит денег. Для расчёта движущейся поездки
+нужны корректные исходные точки и заполненный городской расход автомобиля.
+
+## Проверки
+
+- 205 серверных проверок pytest пройдены; одна проверка живого staging пропущена, поскольку отдельный staging не запущен. Использованы настоящие PostgreSQL/Redis, включая конкурентные повторы
+  POST, разные поездки одного пользователя, слияния и откат транзакции.
+- Браузерные регрессии Chromium: разрешение iOS в пользовательском жесте,
+  независимый GPS/IMU, отказ разрешений, офлайн-очередь, прерывания и квота,
+  единый предпросмотр, манёвры, скорость для неровностей, свайп и история.
+- Сквозной сценарий 3001 IMU-точка при 50 Гц и 61 GPS-точка: разгон, торможение,
+  неровность, полный экспорт, начисление в POST и обновление копилки. Отдельно пройден свайп реальными touch-событиями эмулятора устройства Chromium.
+- 18 комбинаций экранов/размеров: 320×568, 390×844, 414×896, 768×1024,
+  1440×900, 844×390. Проверяются ширина, отсутствие горизонтальной прокрутки,
+  нижняя навигация и цвет фона; снимки просмотрены визуально.
+- PowerShell: синтаксис, резервная копия до обновления, Copy, отказ для read-only,
+  откат образа при неготовности API; Copy дополнительно исполнен на отдельном
+  Docker-стеке с настоящим PostgreSQL и проверкой `/ready`.
+
+Частоту событий и работу браузера в фоне определяет ОС. PWA не может обещать
+непрерывные 50 Гц при блокировке экрана/выгрузке Safari. Исходная частота и
+разрывы сохраняются без синтетического дополнения. Действуют пределы API:
+500000 IMU/обработанных точек, 100000 GPS-точек, длительность до 24 часов.
+Поведение физического iPhone и дорожную точность нужно проверить на устройстве;
+эмуляция разрешений и синтетическая поездка не заменяют такую проверку.
+
+## Обновление
+
+Полный скрипт: [deploy/update.ps1](../deploy/update.ps1). После слияния PR
+в `main` выполните **на своём Windows-компьютере** из актуального проекта:
+
+```powershell
+git pull --ff-only origin main
+powershell -ExecutionPolicy Bypass -File .\deploy\update.ps1 -SshHost root@185.149.145.234 -RemotePath /opt/autoxak-production
+```
+
+Путь нужно заменить, если рабочий production checkout на сервере расположен
+иначе. SSH использует уже настроенный ключ/обычный запрос пароля; секреты
+скрипту передавать не требуется. На сервере выполняется существующий
+`production.sh update`: резервная копия PostgreSQL, fast-forward Git,
+пересборка API, запуск и проверка готовности. Если Git сообщает о локальных
+изменениях, обновление прерывается, не перезаписывая их.
+
+Для Docker на текущем компьютере можно обновить имеющиеся исходные файлы:
+
+```powershell
+# Production: пересборка образа, без git pull и без изменения .env.production.
+powershell -ExecutionPolicy Bypass -File .\deploy\update.ps1
+
+# Development: полный app/static копируется в работающий writable root-контейнер.
+powershell -ExecutionPolicy Bypass -File .\deploy\update.ps1 -Mode Copy -ComposeFile docker-compose.yml -EnvFile .env -HealthUrl https://localhost:8000/ready -HealthCaFile /app/cert.pem
+```
+
+В production `read_only: true` и отдельный системный пользователь; применяйте
+Rebuild. Copy предназначен для development с root и доступной на запись
+файловой системой. Резервные копии создаются в `backups/update-*`; бинарный
+`pg_dump` копируется через Docker без текстового перенаправления PowerShell.
+При ошибке локального обновления скрипт восстанавливает предыдущие файлы/образ.
+Добавления схемы совместимы с прежними столбцами, база автоматически из дампа
+не откатывается. Скрипт не выполняет `down -v` и не заменяет ключи/пароли.
+
+Автоматическое обновление сервера из этой облачной сессии не выполнялось.
diff --git a/docs/REFERENCE_MATH.md b/docs/REFERENCE_MATH.md
index 07d2ee7..532a762 100644
--- a/docs/REFERENCE_MATH.md
+++ b/docs/REFERENCE_MATH.md
@@ -1,3 +1,11 @@
+# Область применения
+
+Этот документ описывает отдельное физическое и исследовательское ядро v3.
+Для продуктовых рублей MVP применяется утверждённая политика TwinEngine
+`twin-fuel-mvp-1`; её формула, начисление в POST и проверки описаны в
+[аудите эпиков](PRODUCT_EPICS_AUDIT.md). Требование явных контрольных наблюдений
+ниже относится к доказательному сравнению v3, а не отключает начисления MVP.
+
 # Математическое ядро 2.0: контракт и границы достоверности
 
 Версия численных функций: `reference-math-1`. Версия API приложения: `2.0.0`.
diff --git a/patch_setup_local.py b/patch_setup_local.py
index e6b4852..d078713 100644
--- a/patch_setup_local.py
+++ b/patch_setup_local.py
@@ -1,4 +1,4 @@
-﻿path = "static/setup.html"
+path = "static/setup.html"
 with open(path, "r", encoding="utf-8") as f:
     html = f.read()
 
diff --git a/print_catalog.py b/print_catalog.py
index e50851f..6b8b1cb 100644
--- a/print_catalog.py
+++ b/print_catalog.py
@@ -1,4 +1,4 @@
-﻿import urllib.request, json, ssl
+import urllib.request, json, ssl
 
 ctx = ssl.create_default_context()
 ctx.check_hostname = False
diff --git a/seed_fleet.py b/seed_fleet.py
index 99eee28..e28d1a7 100644
--- a/seed_fleet.py
+++ b/seed_fleet.py
@@ -1,6 +1,6 @@
-﻿import asyncio
+import asyncio
 from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
-from app.core.database import engine, Base
+from app.core.database import engine, Base, initialize_schema
 from app.models.db_models import VehicleMake, VehicleModel, VehicleTrim
 
 # Автономная фабрика сессий, независимая от имен в database.py
@@ -224,8 +224,7 @@ TOP_FLEET_DATA = [
 
 
 async def seed():
-    async with engine.begin() as conn:
-        await conn.run_sync(Base.metadata.create_all)
+    await initialize_schema()
 
     async with SessionLocal() as session:
         for m_data in TOP_FLEET_DATA:
@@ -260,6 +259,7 @@ async def seed():
                             setattr(trim, k, v)
 
         await session.commit()
+        await initialize_schema()
         print("База данных Top-Fleet успешно наполнена модификациями.")
 
 if __name__ == "__main__":
diff --git a/seed_fuel_prices.py b/seed_fuel_prices.py
index 04741a3..7b784a9 100644
--- a/seed_fuel_prices.py
+++ b/seed_fuel_prices.py
@@ -1,4 +1,4 @@
-﻿import asyncio
+import asyncio
 from datetime import datetime, timezone
 from sqlalchemy import select
 from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
diff --git a/static/app.css b/static/app.css
new file mode 100644
index 0000000..b5007b9
--- /dev/null
+++ b/static/app.css
@@ -0,0 +1,52 @@
+/* Shared geometry for the existing Figma screens. No simulated phone frame. */
+:root { color-scheme: dark; --bg-root:#121417; --lime:#D2FA58; --lime-dim-bg:rgba(210,250,88,.15); }
+html, body { background:#121417; width:100%; min-height:100vh; min-height:100dvh; margin:0; padding:0; }
+body { display:block; }
+.mobile-container { width:100%; max-width:414px; height:100vh; height:100dvh; min-height:100dvh; margin:0 auto; border:0; border-radius:0; box-shadow:none; }
+.cockpit-scroll, .history-scroll, .view-screen { min-height:0; padding-left:max(16px,env(safe-area-inset-left)); padding-right:max(16px,env(safe-area-inset-right)); }
+.cockpit-scroll > *, .history-scroll > *, .view-screen > * { flex-shrink:0; }
+.bottom-cta-spacer { margin-top:16px; padding-top:0; }
+.current-car-card { min-height:0; justify-content:flex-start; gap:16px; }
+.car-svg { width:100%; max-width:280px; height:auto; aspect-ratio:280/135; }
+.current-car-name { font-size:26px; overflow-wrap:anywhere; }
+.car-illustration-wrap { margin:4px 0; }
+.bottom-tab-bar { height:calc(64px + env(safe-area-inset-bottom)); padding-bottom:env(safe-area-inset-bottom); display:grid; grid-template-columns:repeat(3,1fr); flex-shrink:0; }
+.tab-item { width:48px; height:32px; border-radius:18px; display:flex; align-items:center; justify-content:center; }
+.tab-item.active { color:#D2FA58; background:rgba(210,250,88,.15); }
+.tab-item svg { width:20px; height:20px; }
+.active-trip-screen { justify-content:flex-start; gap:20px; overflow-y:auto; }
+.active-trip-screen > * { flex-shrink:0; }
+.trip-center-block { flex:1 0 auto; margin:0; padding:24px 0; min-height:120px; }
+.swipe-container, .swipe-thumb { touch-action:none; }
+.modal-backdrop { z-index:300; }
+.summary-modal-overlay, .details-modal-overlay, .modal-backdrop { overflow-y:auto; }
+.summary-card, .details-modal-card, .geo-modal-card { margin:auto 0; flex-shrink:0; box-shadow:none; }
+.stats-grid-6 { grid-template-columns:repeat(2,minmax(0,1fr)); }
+.stat-cell { min-width:0; }
+.stat-cell-val, .summary-big-amount, .piggy-total-val { overflow-wrap:anywhere; }
+button, input, select { font:inherit; }
+input, select { font-size:16px; }
+input { user-select:text; }
+button { min-height:44px; }
+button:disabled { opacity:.5; cursor:wait; }
+button:focus-visible, a:focus-visible, input:focus-visible, .split-select-wrap:focus-within { outline:2px solid #D2FA58; outline-offset:3px; }
+.native-select-overlay { display:block; position:absolute; inset:0; width:100%; height:100%; opacity:0; cursor:pointer; z-index:3; }
+.split-select-value-box { min-width:0; }
+.field-error, .status-message { color:#D2FA58; font-size:13px; line-height:1.5; }
+.status-message { margin-top:12px; }
+.progress-card { background:var(--bg-card,#222528); border:1px solid rgba(255,255,255,.08); border-radius:22px; padding:18px; }
+.progress-card h2 { font-size:16px; font-weight:500; margin-bottom:10px; }
+.progress-value { font-size:28px; font-weight:600; }
+.progress-value small { font-size:12px; color:#8C919B; font-weight:400; }
+.progress-caption, .progress-empty { font-size:13px; color:#8C919B; line-height:1.5; margin-top:8px; }
+.progress-delta { font-size:12px; color:#8C919B; }
+.progress-delta.positive { color:#4ADE80; }
+.progress-delta.negative { color:#F87171; }
+.progress-controls { display:flex; background:#121417; padding:3px; border-radius:12px; margin-bottom:12px; }
+.progress-controls button { flex:1; background:none; color:#8C919B; border:0; border-radius:9px; }
+.progress-controls button[aria-pressed="true"] { background:rgba(210,250,88,.15); color:#D2FA58; }
+.progress-chart { width:100%; height:auto; }
+.progress-values, .progress-chart { display:none; }
+.empty-history a { display:block; color:#D2FA58; margin-top:16px; }
+.smoothness-bar { width:100%; margin-bottom:16px; }
+@media (prefers-reduced-motion:reduce) { *, *::before, *::after { transition:none!important; scroll-behavior:auto!important; } }
diff --git a/static/auth.css b/static/auth.css
new file mode 100644
index 0000000..3fbc548
--- /dev/null
+++ b/static/auth.css
@@ -0,0 +1,21 @@
+.mobile-container { padding: max(24px,env(safe-area-inset-top)) 24px max(24px,env(safe-area-inset-bottom)); align-items:stretch; justify-content:flex-start; overflow-y:auto; }
+.auth-view { width:100%; flex:1; display:flex; flex-direction:column; min-height:min-content; }
+[hidden] { display:none!important; }
+.hero-section { min-height:300px; }
+.auth-link { background:none; border:0; color:#D2FA58; padding:16px 0; font-size:14px; }
+.auth-back { background:none; color:#fff; border:0; text-align:left; padding:0; margin-bottom:56px; }
+#auth-title { font-size:30px; margin-bottom:16px; }
+.auth-description { color:#8C919B; line-height:1.5; margin-bottom:24px; }
+#credentials-form { display:flex; flex-direction:column; gap:8px; }
+#credentials-form label { font-size:14px; margin-top:14px; }
+#credentials-form input { width:100%; padding:14px; height:52px; border:1px solid #363A3F; border-radius:14px; background:#222528; color:white; }
+.auth-hint { font-size:12px; color:#8C919B; }
+.password-wrap { position:relative; }
+.password-wrap input { padding-right:52px!important; }
+#toggle-password { position:absolute; right:6px; top:4px; width:44px; border:0; background:none; color:#8C919B; }
+#auth-submit { margin-top:20px; }
+#auth-switch { color:#8C919B; margin-top:auto; padding-top:40px; }
+.auth-error { color:#D2FA58; background:#222528; border-radius:12px; padding:14px; font-size:13px; line-height:1.5; margin-top:16px; }
+
+#auth-submit[aria-busy="true"]::before { content:""; display:inline-block; width:16px; height:16px; margin-right:8px; border:2px solid currentColor; border-right-color:transparent; border-radius:50%; vertical-align:middle; animation:auth-spin .8s linear infinite; }
+@keyframes auth-spin { to { transform:rotate(360deg); } }
diff --git a/static/history.html b/static/history.html
index 81ea1d2..79269b0 100644
--- a/static/history.html
+++ b/static/history.html
@@ -1,15 +1,16 @@
-﻿<!DOCTYPE html>
+<!DOCTYPE html>
 <html lang="ru">
 <head>
     <meta charset="UTF-8">
-    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
+    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
     <title>autoXAK — История поездок</title>
     <script src="/static/session.js"></script>
     <script src="/static/offline_store.js"></script>
+    <script src="/static/progress.js"></script>
     <style>
         :root {
             --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Inter", system-ui, sans-serif;
-            --bg-root: #121316;
+            --bg-root: #121417;
             --bg-card: #1C1E22;
             --bg-modal: #22252A;
             --border-subtle: rgba(255, 255, 255, 0.08);
@@ -322,7 +323,7 @@
             width: 100%;
             height: 100%;
             height: 100dvh;
-            background-color: var(--bg-root, var(--bg-screen, #121316));
+            background-color: var(--bg-root, var(--bg-screen, #121417));
             margin: 0;
             padding: 0;
             overflow: hidden;
@@ -331,7 +332,7 @@
 
         .mobile-container {
             width: 100%;
-            max-width: 480px;
+            max-width: 414px;
             height: 100%;
             height: 100dvh;
             max-height: none !important;
@@ -341,6 +342,7 @@
             box-shadow: none !important;
         }
     </style>
+    <link rel="stylesheet" href="/static/app.css">
 </head>
 <body>
 
@@ -363,16 +365,22 @@
             <div class="piggy-trips-count" id="hist-trips-count">0 поездок</div>
         </div>
 
+        <section class="progress-card" id="history-progress"><h2>Динамика прогресса</h2><div class="progress-controls"><button data-days="7" aria-pressed="true" onclick="AutoXAKProgress.refresh('history-progress',7)">7 дней</button><button data-days="30" aria-pressed="false" onclick="AutoXAKProgress.refresh('history-progress',30)">30 дней</button></div>
+            <div class="progress-values" style="display:none"><span class="progress-value"><span class="progress-number">—</span> <small>/ 100</small></span> <span class="progress-delta"></span></div>
+            <p class="progress-empty">Недостаточно данных. Совершите поездку, чтобы увидеть прогресс.</p>
+            <p class="progress-caption">Среднее за последние 7 дней</p><svg class="progress-chart" viewBox="0 0 310 150" role="img" aria-label="Динамика индекса плавности" style="display:none"></svg></section>
+
         <div class="list-section-label">Расчетная экономия по поездкам</div>
 
+        <p id="pending-status" class="status-message" role="status"></p>
         <div class="trips-list" id="hist-trips-list">
-            <div class="empty-history">Загрузка поездок...</div>
+            <div class="empty-history">Недостаточно данных</div>
         </div>
     </div>
 
     <!-- Модальное окно "Подробнее" (Макет 9) -->
     <div class="details-modal-overlay" id="details-modal" onclick="handleBackdropClick(event)">
-        <div class="details-modal-card">
+        <div class="details-modal-card" role="dialog" aria-modal="true" aria-label="Подробнее о поездке">
             <div class="details-header">
                 <span class="details-title">Подробнее</span>
                 <button class="btn-close-details" onclick="closeDetailsModal()" aria-label="Закрыть">
@@ -415,11 +423,12 @@
                     <span class="stat-cell-label">Время поездки</span>
                 </div>
             </div>
+            <div class="smoothness-bar"><span>Индекс плавности</span><span id="det-smooth">— / 100</span></div>
         </div>
     </div>
 
     <!-- Нижняя панель навигации -->
-    <nav class="bottom-tab-bar">
+    <nav class="bottom-tab-bar" id="bottom-tab-bar">
         <a href="/history" class="tab-item active" aria-label="История поездок">
             <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                 <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
@@ -535,7 +544,7 @@
         try {archive=JSON.parse(localStorage.getItem('autoXAK_local_history') || '[]').filter(trip=>trip.user_id===owner);} catch (_) {}
         const seenIds = new Set(), merged = [];
         // Responses from the server supersede every provisional local value.
-        for (const trip of [...serverTrips,...offlineList,...archive]) {
+        for (const trip of [...serverTrips,...archive]) {
             const sid=trip.session_id || trip.id;
             if (!sid || seenIds.has(sid)) continue;
             seenIds.add(sid);
@@ -549,19 +558,21 @@
         const pendingOnly=offlineList.filter(trip=>!serverTrips.some(saved=>saved.session_id===trip.session_id));
         const totalRub=complete ? serverTotalRub : merged.reduce((sum,trip)=>sum+(Number(trip.total_savings_rub)||0),0);
         document.getElementById('hist-total-savings').innerText = `+${totalRub.toFixed(2)} ₽`;
-        document.getElementById('hist-trips-count').innerText = pluralizeTrips(complete ? Math.max(merged.length,serverTotalTrips+pendingOnly.length) : merged.length);
+        document.getElementById('hist-trips-count').innerText = pluralizeTrips(complete ? Math.max(merged.length,serverTotalTrips) : merged.length);
+        document.getElementById("pending-status").textContent=pendingOnly.length?`Ожидают синхронизации: ${pendingOnly.length}`:"";
         renderTripsList(merged);
+        AutoXAKProgress.refresh("history-progress", Number(document.querySelector("[data-days][aria-pressed=true]")?.dataset.days)||7);
     }
 
     function renderTripsList(trips) {
         const container = document.getElementById('hist-trips-list');
         if (!trips || trips.length === 0) {
-            container.innerHTML = `<div class="empty-history">Поездок пока нет. Начните первую поездку на главном экране.</div>`;
+            container.innerHTML = `<div class="empty-history">Пока нет сохраненных поездок<a href="/">Начать поездку</a></div>`;
             return;
         }
 
         container.innerHTML = trips.map((t, idx) => {
-            const rub = t.total_savings_rub===null ? null : (Number(t.total_savings_rub) || 0).toFixed(2);
+            const rub = Number.isFinite(t.total_savings_rub) ? t.total_savings_rub.toFixed(2) : null;
             const dateStr = formatRuDateTime(t.created_at);
             return `
                 <div class="trip-row-card">
@@ -579,7 +590,7 @@
         const t = loadedTrips[index];
         if (!t) return;
 
-        const rub = t.total_savings_rub===null ? null : (Number(t.total_savings_rub) || 0).toFixed(2);
+        const rub = Number.isFinite(t.total_savings_rub) ? t.total_savings_rub.toFixed(2) : null;
         const durSec = Number(t.duration_seconds) || 0;
         const idleMin = t.idle_minutes !== undefined
             ? t.idle_minutes
@@ -593,7 +604,7 @@
         document.getElementById('det-starts').innerText = t.hard_accel_count===null?'—':String(t.hard_accel_count ?? 0);
         document.getElementById('det-brakes').innerText = t.hard_brake_count===null?'—':String(t.hard_brake_count ?? 0);
         document.getElementById('det-idle').innerText = `${idleMin} мин`;
-        if (t.math_status?.version==='reference-math-1') document.getElementById('det-idle').parentElement.querySelector('.stat-cell-label').innerText='Малая скорость';
+        document.getElementById('det-idle').title='Время при скорости менее 1 м/с; остановку в пробке нельзя отличить от парковки только по датчикам.';
         document.getElementById('det-bumps').innerText = String(t.bumps_count ?? t.z_veto_count ?? 0);
         document.getElementById('det-distance').innerText = distLabel;
         document.getElementById('det-duration').innerText = formatDurationHoursMins(durSec);
@@ -601,6 +612,7 @@
             for (const id of ['det-starts','det-brakes','det-idle','det-bumps']) document.getElementById(id).innerText = '—';
         }
 
+        document.getElementById('det-smooth').textContent=Number.isFinite(t.smooth_score)?`${t.smooth_score}/100`:'— / 100';
         document.getElementById('details-modal').classList.add('active');
     }
 
@@ -609,13 +621,11 @@
     }
 
     function handleBackdropClick(e) {
-        if (e.target.id === 'details-modal') {
-            closeDetailsModal();
-        }
+        // Details are dismissed with the explicit close button (PRODUKT-0008).
     }
 
     window.addEventListener('online', () => {fetchHistory().catch(error=>console.warn('History refresh:',error));});
-    window.addEventListener('DOMContentLoaded', fetchHistory);
+    window.addEventListener('DOMContentLoaded', ()=>fetchHistory().catch(()=>{}));
 </script>
 </body>
 </html>
diff --git a/static/index.html b/static/index.html
index 1f22519..b5f6b2c 100644
--- a/static/index.html
+++ b/static/index.html
@@ -1,17 +1,18 @@
-﻿<!DOCTYPE html>
+<!DOCTYPE html>
 <html lang="ru">
 <head>
     <meta charset="UTF-8">
-    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
+    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
     <title>autoXAK — Бортовой помощник</title>
     <link rel="manifest" href="/static/manifest.json">
-    <meta name="theme-color" content="#121316">
+    <meta name="theme-color" content="#121417">
     <script src="/static/session.js"></script>
     <script src="/static/offline_store.js"></script>
+    <script src="/static/progress.js"></script>
     <style>
         :root {
             --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Inter", system-ui, sans-serif;
-            --bg-root: #121316;
+            --bg-root: #121417;
             --bg-card: #1C1E22;
             --bg-modal: #22252A;
             --bg-cell: #2A2D34;
@@ -55,7 +56,7 @@
 
         .mobile-container {
             width: 100%;
-            max-width: 480px;
+            max-width: 414px;
             height: 100%;
             height: 100dvh;
             background-color: var(--bg-root);
@@ -226,7 +227,7 @@
             width: 100%;
             height: 52px;
             background: var(--lime);
-            color: #121316;
+            color: #121417;
             border: none;
             border-radius: 16px;
             font-size: 15.5px;
@@ -341,7 +342,7 @@
             background: var(--lime);
             border: none;
             border-radius: 14px;
-            color: #121316;
+            color: #121417;
             font-size: 15px;
             font-weight: 600;
             cursor: pointer;
@@ -571,7 +572,7 @@
             display: flex;
             align-items: center;
             justify-content: center;
-            color: #121316;
+            color: #121417;
             cursor: grab;
             transition: transform 0.15s ease;
             z-index: 2;
@@ -790,6 +791,7 @@
             white-space: nowrap;
         }
     </style>
+    <link rel="stylesheet" href="/static/app.css">
 </head>
 <body>
 
@@ -825,7 +827,8 @@
                     <path d="M18 12a2 2 0 0 0-2 2c0 1.1.9 2 2 2h4v-4h-4z"/>
                 </svg>
             </div>
-            <a href="/history" class="savings-amount-big" id="cockpit-savings-val">+0.0 ₽</a>
+            <a href="/history" class="savings-amount-big" id="cockpit-savings-val">+0 ₽</a>
+            <p class="progress-caption" id="savings-empty-caption">Экономия появится после поездки</p>
             <div class="savings-card-divider"></div>
             <a href="/history" class="savings-card-bottom">
                 <span class="history-link-text">История поездок</span>
@@ -836,6 +839,11 @@
             </a>
         </div>
 
+        <section class="progress-card" id="cockpit-progress"><h2>Мой прогресс</h2>
+            <div class="progress-values" style="display:none"><span class="progress-value"><span class="progress-number">—</span> <small>/ 100</small></span> <span class="progress-delta"></span></div>
+            <p class="progress-empty">Недостаточно данных. Совершите поездку, чтобы увидеть прогресс.</p>
+            <p class="progress-caption">Среднее за последние 7 дней</p></section>
+
         <!-- Модель сравнения -->
         <div class="info-card">
             <span class="info-card-label">Модель сравнения</span>
@@ -905,7 +913,7 @@
             <div class="geo-modal-text">
                 Геолокация нужна для точного измерения поездки и расчета результата
             </div>
-            <button type="button" class="btn-lime-modal" onclick="closeGeoModals()">Открыть настройки телефона</button>
+            <button type="button" class="btn-lime-modal" onclick="retryGeoPermission()">Как включить геолокацию</button>
         </div>
     </div>
 
@@ -1051,20 +1059,7 @@
                 </div>
             </div>
 
-                        <!-- Ячейка ввода расхода по бортовому компьютеру (по запросу ML) -->
-            <div class="bk-input-card">
-                <div class="bk-input-header">
-                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
-                        <circle cx="12" cy="12" r="9"/>
-                        <path d="M12 7v5l3 3"/>
-                    </svg>
-                    <span>Расход по бортовому компьютеру машины:</span>
-                </div>
-                <div class="bk-input-row">
-                    <input type="number" id="input-bk-consumption" class="bk-input-field" step="0.1" min="1.0" max="99.0" placeholder="0.0" inputmode="decimal">
-                    <div class="bk-unit-badge">л / 100 км</div>
-                </div>
-            </div>
+            <div class="smoothness-bar"><span class="smoothness-label">Индекс плавности</span><span class="smoothness-val" id="sum-smooth">— / 100</span></div>
 
             <button type="button" class="btn-lime-primary" onclick="claimToPiggyBank()" style="margin-top: 0;">
                 Зачислить в копилку
@@ -1077,6 +1072,7 @@
 <script>
     let currentUserId = localStorage.getItem('autoXAK_user_id') || '';
     let currentCarId = localStorage.getItem('autoXAK_selected_car_id') || 'haval_jolion_15t_4wd';
+    let currentCityFuelRate = Number(localStorage.getItem('autoXAK_fuel_pass')) || (['haval_jolion_15t','haval_jolion_15t_4wd','haval_jolion_15t_fwd'].includes(currentCarId)?8.5:null);
     let tripActive = false;
     let tripStartTime = 0;
     let lastTickMs = 0;
@@ -1121,7 +1117,8 @@
             if (!tripActive || activeSessionId!==sessionId || result.session_id!==sessionId
                 || result.status!=='PREVIEW') return;
             document.getElementById('trip-live-savings').innerText = Number.isFinite(result.cost_savings_rub) ? `${result.cost_savings_rub>=0?'+':''}${result.cost_savings_rub.toFixed(2)} ₽` : '—';
-            document.getElementById('trip-live-savings').title = Number.isFinite(result.cost_savings_rub)?'Расчёт по переданным контрольным данным':'Для оценки экономии нужна сопоставимая контрольная поездка.';
+            document.getElementById('trip-live-smooth').textContent = Number.isFinite(result.smooth_score)?`${result.smooth_score}/100`:'—';
+            document.getElementById('trip-live-savings').title = Number.isFinite(result.cost_savings_rub)?'Расчётная оценка относительно базового лихача':'Укажите городской расход в гараже и дождитесь данных датчиков.';
         } catch (_) {
             if (tripActive && activeSessionId===sessionId) document.getElementById('trip-live-savings').title = 'Обновление расчёта после восстановления связи.';
         } finally {
@@ -1202,7 +1199,7 @@
         const num = Number(val);
         if (isNaN(num)) return "+0.0 ₽";
         const str = (Math.round(num * 100) % 10 === 0) ? num.toFixed(1) : num.toFixed(2);
-        return `+${str} ₽`;
+        return `${num>=0?'+':''}${num===0?'0':str} ₽`;
     }
 
     function formatDistance(km) {
@@ -1246,7 +1243,7 @@
         const owner=AutoXAKSession.getUserId();
         if (!owner) return;
         await AutoXAKSession.syncPendingProfile();
-        if (navigator.onLine) {
+        if (navigator.onLine && !document.getElementById('summary-modal').classList.contains('active')) {
             try {await AutoXAKOfflineStore.syncPendingTrips();} catch (_) {}
         }
         if (owner!==AutoXAKSession.getUserId()) return;
@@ -1277,6 +1274,8 @@
             if (replies[1].status==='fulfilled' && replies[1].value.response.ok && !localStorage.getItem('autoXAK_pending_profile')) {
                 const prof=replies[1].value.data;
                 resolvedCarId=prof.car_id || resolvedCarId;
+                currentCityFuelRate=Number.isFinite(prof.fuel_consumption)?prof.fuel_consumption:null;
+                if(currentCityFuelRate!==null)localStorage.setItem("autoXAK_fuel_pass",String(currentCityFuelRate));
                 if (prof.car_name) localStorage.setItem('autoXAK_selected_car_name',prof.car_name);
                 else if (localStorage.getItem('autoXAK_selected_car_id')!==resolvedCarId) localStorage.removeItem('autoXAK_selected_car_name');
             }
@@ -1295,6 +1294,8 @@
         document.getElementById('cockpit-car-name').innerText=carDisplayName;
         document.getElementById('cockpit-savings-val').innerText=formatCockpitSavings(totalRub);
         document.getElementById('cockpit-trips-count').innerText=String(totalTrips);
+        document.getElementById('savings-empty-caption').hidden=totalTrips>0;
+        AutoXAKProgress.refresh('cockpit-progress',7);
         document.getElementById('cockpit-ghost-status').innerText=ghostStatus;
     }
 
@@ -1311,12 +1312,13 @@
 
     function retryGeoPermission() {
         closeGeoModals();
-        requestStartTrip(true);
+        window.alert('Разрешите геолокацию для этого сайта в настройках браузера. Запись поездки продолжается.');
     }
 
     async function requestStartTrip(forceStartOnDesktop = false) {
         if (tripActive || startingTrip || finishingTrip) return;
         if (pendingFinishedPayload) { await finishActiveTrip(); return; }
+        if(currentCityFuelRate===null) {window.alert('Укажите городской расход автомобиля в гараже для расчёта рублей.');window.location.assign('/setup?edit=1');return;}
         let permission = Promise.resolve();
         if (typeof DeviceMotionEvent === 'undefined') {
             captureMotionPermission = 'unsupported';
@@ -1403,11 +1405,11 @@
 
         setTripGlowState('green');
         document.getElementById('trip-timer-display').innerText = "00:00";
-        document.getElementById('trip-live-savings').innerText = '—';
-        document.getElementById('trip-live-savings').title = 'Для оценки экономии нужны контрольный расход и сопоставимая поездка.';
+        document.getElementById('trip-live-savings').innerText = '+0.00 ₽';
+        document.getElementById('trip-live-savings').title = 'Расчёт по выбранному автомобилю и данным поездки.';
         document.getElementById('trip-live-distance').innerText = "0 км";
         document.getElementById('trip-live-bumps').innerText = "0";
-        document.getElementById('trip-live-smooth').innerText = "—";
+        document.getElementById('trip-live-smooth').innerText = "100/100";
         document.getElementById('pill-net-status').innerHTML = navigator.onLine ? "Интернет<br>подключен" : "Интернет<br>оффлайн";
         document.getElementById('pill-gps-status').innerHTML = "GPS<br>поиск...";
 
@@ -1526,7 +1528,7 @@
                 lat: hasRecentGps ? Number(currentLat.toFixed(6)) : null,
                 lon: hasRecentGps ? Number(currentLon.toFixed(6)) : null
             });
-            document.getElementById('trip-live-smooth').innerText = '—';
+            // The server calculates the descriptive smoothness score.
 
             // Live sensor impulses are visible during a stationary device check.
             // They do not create distance, driving violations or financial credit.
@@ -1539,7 +1541,7 @@
             } else {shockQuietSince=0;}
             const rotating = Math.hypot(...['alpha','beta','gamma'].map(axis=>Number.isFinite(rot[axis])?rot[axis]:0))>45;
             const shock = Math.abs(shockFiltered)>3.5 || (Math.abs(shockFiltered)>0.8 && Math.abs(jerk)>15);
-            if (!rotating && shockArmed && shock && nowMs-lastBumpTime>700 && nowMs-tripStartTime>=120) {
+            if (hasRecentGps && currentSpeedMps >= 2.0 && !rotating && shockArmed && shock && nowMs-lastBumpTime>700 && nowMs-tripStartTime>=120) {
                 lastBumpTime = nowMs;
                 shockArmed=false;
                 liveBumpsCount++;
@@ -1551,12 +1553,12 @@
             const horizMag = Math.hypot(linX, linY);
             const signedLong = Math.abs(linX) >= Math.abs(linY) ? linX : linY;
 
-            if (hasRecentGps && currentSpeedMps > 0.8 && horizMag > 2.3 && (nowMs - lastEventTime > 1400)) {
+            if (hasRecentGps && currentSpeedMps >= 2.0 && !rotating && horizMag > 2.3 && (nowMs - lastEventTime > 1400)) {
                 lastEventTime = nowMs;
                 if (signedLong >= 0) liveHarshStarts++; else liveHarshBrakes++;
-                document.getElementById('trip-live-smooth').innerText = '—';
+                // The server calculates the descriptive smoothness score.
                 setTripGlowState('red');
-            } else if (horizMag > 1.4) {
+            } else if (hasRecentGps && currentSpeedMps >= 2.0 && !rotating && horizMag > 1.4) {
                 setTripGlowState('yellow');
             } else if (nowMs - lastEventTime > 1800) {
                 setTripGlowState('green');
@@ -1591,11 +1593,15 @@
                             const dtGps = (nowMs - prevGpsTimeMs) / 1000;
                             const segKm = haversineKm(prevGpsLat, prevGpsLon, c.latitude, c.longitude);
                             if (dtGps > 0.2 && dtGps <= 10) {
-                                if (nativeSpeed !== null && previousNativeSpeedMps !== null) {
-                                    liveDistanceKm += (nativeSpeed + previousNativeSpeedMps) * 0.5 * dtGps / 1000;
-                                } else if (segKm >= 0.003 && segKm * 1000 / dtGps <= 75) {
+                                const stationary = nativeSpeed !== null && previousNativeSpeedMps !== null && Math.max(nativeSpeed,previousNativeSpeedMps)<0.5;
+                                if (!stationary && segKm >= (nativeSpeed!==null && previousNativeSpeedMps!==null ? .0002 : .003) && segKm * 1000 / dtGps <= 75) {
                                     liveDistanceKm += segKm;
                                     derivedSpeed = segKm * 1000 / dtGps;
+                                } else if (segKm * 1000 / dtGps > 75) {
+                                    currentSpeedMps=0;lastGpsFixMs=0;
+                                    previousNativeSpeedMps=prevGpsLat=prevGpsLon=prevGpsTimeMs=null;
+                                    document.getElementById('pill-gps-status').innerHTML='GPS<br>неточный';
+                                    return;
                                 }
                             }
                         }
@@ -1614,6 +1620,7 @@
                 (error) => {
                     if (!tripActive || activeSessionId !== recordingSessionId) return;
                     captureGeoPermission = error.code===1?'denied':'unavailable';
+                    if(error.code===1 || error.code===2) document.getElementById(error.code===1?'modal-geo-denied':'modal-geo-disabled').classList.add('active');
                     currentSpeedMps = 0; lastGpsFixMs = 0;
                     previousNativeSpeedMps = prevGpsLat = prevGpsLon = prevGpsTimeMs = null;
                     document.getElementById('pill-gps-status').innerHTML = error.code===1 ? "GPS<br>нет доступа" : "GPS<br>поиск...";
@@ -1638,195 +1645,91 @@
 
     // ==================== СВАЙП ДЛЯ ЗАВЕРШЕНИЯ ====================
     (function initSwipeToStop() {
-        const container = document.getElementById('swipe-container');
-        const thumb = document.getElementById('swipe-thumb');
-        let isDragging = false;
-        let startX = 0;
-        let currentX = 0;
-
-        function getMaxSlide() {
-            return Math.max(10, container.clientWidth - thumb.clientWidth - 10);
-        }
-
-        function onStart(clientX) {
-            if (finishingTrip || (!tripActive && !pendingFinishedPayload)) return;
-            isDragging = true;
-            startX = clientX;
-            currentX = 0;
-            thumb.classList.add('dragging');
-        }
-
-        function onMove(clientX) {
-            if (!isDragging) return;
-            const maxSlide = getMaxSlide();
-            currentX = Math.max(0, Math.min(maxSlide, clientX - startX));
-            thumb.style.transform = `translateX(${currentX}px)`;
-        }
-
-        function onEnd() {
-            if (!isDragging) return;
-            isDragging = false;
-            thumb.classList.remove('dragging');
-            const maxSlide = getMaxSlide();
-            if (currentX >= maxSlide * 0.6) {
-                thumb.style.transform = `translateX(${maxSlide}px)`;
-                setTimeout(() => {
-                    thumb.style.transform = 'translateX(0px)';
-                    finishActiveTrip();
-                }, 150);
-            } else {
-                thumb.style.transform = 'translateX(0px)';
-            }
-            currentX = 0;
-        }
-
-        thumb.addEventListener('touchstart', (e) => onStart(e.touches[0].clientX), { passive: true });
-        window.addEventListener('touchmove', (e) => { if (isDragging) onMove(e.touches[0].clientX); }, { passive: true });
-        window.addEventListener('touchend', onEnd);
-
-        thumb.addEventListener('mousedown', (e) => onStart(e.clientX));
-        window.addEventListener('mousemove', (e) => { if (isDragging) onMove(e.clientX); });
-        window.addEventListener('mouseup', onEnd);
+        const container=document.getElementById('swipe-container'), thumb=document.getElementById('swipe-thumb');
+        let pointer=null, start=0, offset=0;
+        const maximum=()=>Math.max(1,container.clientWidth-thumb.clientWidth-10);
+        thumb.setAttribute('role','button');thumb.tabIndex=0;thumb.setAttribute('aria-label','Завершить поездку');
+        thumb.addEventListener('pointerdown', event=>{
+            if(pointer!==null || !event.isPrimary || event.button!==0 || finishingTrip || (!tripActive && !pendingFinishedPayload)) return;
+            event.preventDefault();pointer=event.pointerId;start=event.clientX;offset=0;
+            thumb.setPointerCapture(pointer);thumb.classList.add('dragging');
+        });
+        thumb.addEventListener('pointermove',event=>{
+            if(event.pointerId!==pointer) return;
+            offset=Math.max(0,Math.min(maximum(),event.clientX-start));thumb.style.transform=`translateX(${offset}px)`;
+        });
+        function reset(event,complete=false) {
+            if(event.pointerId!==pointer) return;
+            const finish=complete && offset>=maximum()*.85;
+            const id=pointer;pointer=null;offset=0;
+            if(thumb.hasPointerCapture(id)) thumb.releasePointerCapture(id);
+            thumb.classList.remove('dragging');thumb.style.transform='translateX(0)';
+            if(finish) finishActiveTrip();
+        }
+        thumb.addEventListener('pointerup',e=>reset(e,true));
+        thumb.addEventListener('pointercancel',e=>reset(e));
+        thumb.addEventListener('lostpointercapture',e=>reset(e));
+        thumb.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();finishActiveTrip();}});
     })();
 
     // ==================== ЗАВЕРШЕНИЕ И ОТКРЫТИЕ ЭКРАНА "ИТОГ ПОЕЗДКИ" ====================
-    async function finishActiveTrip(endReason = 'user') {
-        if (finishingTrip || (!tripActive && !pendingFinishedPayload)) return;
-        finishingTrip = true;
-        tripActive = false;
-        if (estimateController) {estimateController.abort();estimateController=null;}
-
-        clearInterval(tripTimerInterval);
-        if (motionHandler) window.removeEventListener('devicemotion', motionHandler);
-        if (geoWatchId !== null) navigator.geolocation.clearWatch(geoWatchId);
-        geoWatchId = null;
-        clearInterval(draftTimerInterval);
-        if (wakeLock) { try { await wakeLock.release(); } catch (_) {} }
-
-        const elapsedSec = Math.max(1, Math.floor((Date.now() - tripStartTime) / 1000));
-        const sessionId = activeSessionId;
-        lastFinishedSessionId = sessionId;
-        const payload = pendingFinishedPayload || {
-            session_id: sessionId,
-            user_id: recordingOwnerId,
-            car_id: recordingCarId,
-            telemetry_stream: [...telemetryStream],
-            raw_recording: rawRecording(endReason)
-        };
-        pendingFinishedPayload = payload;
-
-        try {
-            await draftSaveChain.catch(()=>{});
-            await AutoXAKOfflineStore.savePendingTrip(payload);
-        } catch (error) {
-            finishingTrip = false;
-            window.alert("console.warn('Локальный кэш переполнен, отправляем напрямую на сервер');
-        }
-        pendingFinishedPayload = null;
-        if (releaseRecordingLock) {releaseRecordingLock();releaseRecordingLock=null;}
-        document.getElementById('active-trip-screen').classList.remove('active');
-
-        // Credit only results confirmed by the calculation endpoint.
-        let savedRub = null;
-        let startsCount = liveHarshStarts;
-        let brakesCount = liveHarshBrakes;
-        let idleMins = 0;
-        let bumpsCount = liveBumpsCount;
-        let distKm = liveDistanceKm;
-        let durSec = elapsedSec;
-        let isOffline = !navigator.onLine;
-        let quality = null;
-
-        try {
-            if (!navigator.onLine) throw new Error('Offline');
-
-            const {response:res, data:result} = await AutoXAKSession.fetchJSON('/api/v1/telemetry/session', {
-                method: 'POST',
-                headers: {
-                    'Content-Type': 'application/json',
-                    ...(typeof AutoXAKSession !== 'undefined' ? AutoXAKSession.getAuthHeaders() : {})
-                },
-                body: JSON.stringify(payload)
-            },15000);
-
-            if (res.ok) {
-                quality = result.metrics?.capture_quality || null;
-                await AutoXAKOfflineStore.removePendingTrip(sessionId);
-                AutoXAKOfflineStore.rememberSyncedTrip(result, payload);
-                lastFinishedSessionId = result.session_id;
-                savedRub = Number.isFinite(result.cost_savings_rub) ? result.cost_savings_rub : null;
-                startsCount = Number.isFinite(result.harsh_accel_count) ? result.harsh_accel_count : null;
-                brakesCount = Number.isFinite(result.harsh_brake_count) ? result.harsh_brake_count : null;
-                bumpsCount = Number(result.road_anomalies_count ?? bumpsCount);
-                distKm = Number(result.distance_km ?? distKm);
-                durSec = Number(result.duration_seconds ?? durSec);
-                idleMins = Math.round(Number(result.traffic_jam_seconds ?? 0) / 60);
-                isOffline = false;
-
-                saveTripDetailsLocally(result.session_id || sessionId, {
-                    session_id: result.session_id || sessionId,
-                    hard_accel_count: startsCount,
-                    hard_brake_count: brakesCount,
-                    idle_minutes: idleMins,
-                    bumps_count: bumpsCount,
-                    distance_km: distKm,
-                    duration_seconds: durSec,
-                    total_savings_rub: savedRub,
-                    capture_quality: quality,
-                    math_status: result.metrics?.math_status || null,
-                    created_at: new Date(tripStartTime).toISOString()
-                }, false);
-            } else {
-                throw new Error('HTTP ' + res.status);
-            }
-        } catch (_) {
-            isOffline = true;
-            // The durable copy already exists. Never overwrite it with a later draft.
-            saveTripDetailsLocally(sessionId, {
-                session_id: sessionId,
-                hard_accel_count: startsCount,
-                hard_brake_count: brakesCount,
-                idle_minutes: idleMins,
-                bumps_count: bumpsCount,
-                distance_km: distKm,
-                duration_seconds: durSec,
-                total_savings_rub: savedRub,
-                capture_quality: {analysis_status:'unavailable',style_metrics_available:false},
-                created_at: new Date(tripStartTime).toISOString()
-            }, true);
-        }
-
-        document.getElementById('sum-savings-rub').innerText = Number.isFinite(savedRub) ? `${savedRub>=0?'+':''}${savedRub.toFixed(2)} ₽` : '—';
-        document.getElementById('sum-starts').innerText = startsCount===null?'—':String(startsCount);
-        document.getElementById('sum-brakes').innerText = brakesCount===null?'—':String(brakesCount);
-        document.getElementById('sum-idle').innerText = `${idleMins} мин`;
-        document.getElementById('sum-idle').parentElement.querySelector('.stat-cell-label').innerText='Малая скорость';
-        document.getElementById('sum-bumps').innerText = String(bumpsCount);
-        document.getElementById('sum-distance').innerText = formatDistance(distKm);
-        document.getElementById('sum-duration').innerText = formatDurationHoursMins(durSec);
-
-        const offlineBanner = document.getElementById('sum-offline-banner');
-        if (isOffline) {
-            offlineBanner.innerText = 'Поездка сохранена на устройстве и появится на сервере после синхронизации.';
-            offlineBanner.classList.add('visible');
-        } else {
-            offlineBanner.classList.remove('visible');
-        }
-        if (isOffline || (quality && !quality.style_metrics_available)) {
-            for (const id of ['sum-starts','sum-brakes','sum-idle']) document.getElementById(id).innerText = '—';
+    let finishedResult=null;
+    function showTripSummary(result, payload, pending) {
+        const value=(id,n,format=String)=>{document.getElementById(id).textContent=Number.isFinite(n)?format(n):'—';};
+        value('sum-savings-rub',result.cost_savings_rub,n=>`${n>=0?'+':''}${n.toFixed(2)} ₽`);
+        value('sum-starts',result.harsh_accel_count);value('sum-brakes',result.harsh_brake_count);
+        value('sum-bumps',result.road_anomalies_count);value('sum-idle',result.traffic_jam_seconds,n=>`${Math.round(n/60)} мин`);
+        value('sum-distance',result.distance_km,formatDistance);value('sum-duration',result.duration_seconds,formatDurationHoursMins);
+        value('sum-smooth',result.smooth_score,n=>`${n}/100`);
+        document.querySelector('#summary-modal .trophy-text').textContent=pending?'Ожидает синхронизации':'Выгода сохранена';
+        document.querySelector('#summary-modal .summary-sub-caption').textContent='Сэкономлено на топливе относительно лихача';
+        document.getElementById('sum-offline-banner').textContent='Поездка синхронизируется при появлении сети. Итог будет уточнён.';
+        document.getElementById('sum-offline-banner').classList.toggle('visible',pending);
+        document.getElementById('summary-modal').classList.add('active');
+        try {localStorage.setItem('autoXAK_unclaimed_summary',JSON.stringify({user_id:payload.user_id,session_id:result.session_id,result,pending}));} catch(_) {}
+    }
+    async function finishActiveTrip(endReason='user') {
+        if(finishingTrip || (!tripActive && !pendingFinishedPayload)) return;
+        finishingTrip=true;tripActive=false;closeGeoModals();
+        if(estimateController){estimateController.abort();estimateController=null;}
+        clearInterval(tripTimerInterval);clearInterval(draftTimerInterval);
+        if(motionHandler) window.removeEventListener('devicemotion',motionHandler);
+        if(geoWatchId!==null) navigator.geolocation.clearWatch(geoWatchId);
+        geoWatchId=null;
+        if(wakeLock){try{await wakeLock.release();}catch(_){}wakeLock=null;}
+        const payload=pendingFinishedPayload || {session_id:activeSessionId,user_id:recordingOwnerId,car_id:recordingCarId,
+            telemetry_stream:[...telemetryStream],raw_recording:rawRecording(endReason)};
+        pendingFinishedPayload=payload;
+        await draftSaveChain.catch(()=>{});
+        let localSaved=false, result=null;
+        try {await AutoXAKOfflineStore.savePendingTrip(payload);localSaved=true;}catch(error){console.warn('Trip local persistence failed:',error.name);}
+        if(navigator.onLine) {
+            try {
+                const reply=await AutoXAKSession.fetchJSON('/api/v1/telemetry/session',{method:'POST',
+                    headers:{'Content-Type':'application/json',...AutoXAKSession.getAuthHeaders()},body:JSON.stringify(payload)},30000);
+                if(reply.response.ok) result=reply.data;
+            }catch(_){}
+        }
+        if(!localSaved && !result) {
+            finishingTrip=false;
+            window.alert('Поездка пока только в памяти этой вкладки: не удалось сохранить её на устройстве и сервере. Не закрывайте вкладку. Восстановите сеть или освободите место и повторите свайп.');
+            return;
         }
-        if (isOffline || (quality && !quality.style_metrics_available) || distKm===0) {
-            document.getElementById('sum-bumps').innerText = motionSamples.length ? String(liveBumpsCount) : '—';
-            document.getElementById('sum-bumps').title = 'Вертикальные импульсы датчика. Без подтверждённого движения они не считаются дорожными событиями.';
-        } else {
-            document.getElementById('sum-bumps').title = 'Дорожные события по данным поездки';
+        if(releaseRecordingLock){releaseRecordingLock();releaseRecordingLock=null;}
+        pendingFinishedPayload=null;
+        if(result) {
+            // POST already credited the trip atomically. Keep no second action
+            // between the displayed receipt and its credited balance.
+            AutoXAKOfflineStore.rememberSyncedTrip(result,payload);
+            try {await AutoXAKOfflineStore.removePendingTrip(payload.session_id);} catch(_) {}
         }
-
-        document.querySelector('#summary-modal .trophy-text').innerText = Number.isFinite(savedRub)?'Выгода сохранена':'Поездка сохранена';
-        document.querySelector('#summary-modal .summary-sub-caption').innerText = Number.isFinite(savedRub)?'Сэкономлено на топливе\nотносительно контрольной поездки':'Данные поездки сохранены';
-        document.querySelector('#summary-modal .btn-lime-primary').innerText = Number.isFinite(savedRub)?'Зачислить в копилку':'Готово';
-        document.getElementById('summary-modal').classList.add('active');
-        finishingTrip = false;
+        lastFinishedSessionId=result?.session_id || payload.session_id;
+        finishedResult=result;
+        document.getElementById('active-trip-screen').classList.remove('active');
+        showTripSummary(result || {session_id:payload.session_id,cost_savings_rub:null,
+            duration_seconds:(payload.raw_recording.ended_at_ms-payload.raw_recording.started_at_ms)/1000,
+            distance_km:gpsSamples.length>1?liveDistanceKm:null},payload,!result);
+        finishingTrip=false;
     }
 
     function saveTripDetailsLocally(sid, details, isOfflineTrip = false) {
@@ -1846,30 +1749,17 @@
 
     let lastFinishedSessionId = null;
 
+    let claiming=false;
     async function claimToPiggyBank() {
-        const bkInput = document.getElementById('input-bk-consumption');
-        const bkVal = bkInput ? parseFloat(bkInput.value.replace(',', '.')) : NaN;
-        if (bkInput && bkInput.value.trim() && (!Number.isFinite(bkVal) || bkVal <= 0 || bkVal > 100)) { window.alert('Введите расход от 0 до 100 л/100 км.'); return; }
-
-        // Если водитель ввел значение БК — отправляем на бэкенд для ML
-        if (!isNaN(bkVal) && bkVal > 0 && lastFinishedSessionId) {
-            try {
-                await AutoXAKOfflineStore.savePendingControl(lastFinishedSessionId, bkVal);
-                await AutoXAKOfflineStore.syncPendingTrips();
-                // Обновляем локальную запись
-                const map = JSON.parse(localStorage.getItem('autoXAK_trip_details_map') || '{}');
-                if (map[lastFinishedSessionId]) {
-                    map[lastFinishedSessionId].board_computer_consumption_l100km = bkVal;
-                    localStorage.setItem('autoXAK_trip_details_map', JSON.stringify(map));
-                }
-            } catch (err) {
-                console.warn('[ML-Audit] Не удалось отправить значение БК на сервер (оффлайн):', err);
-            }
-        }
-
-        if (bkInput) bkInput.value = '';
-        document.getElementById('summary-modal').classList.remove('active');
-        syncCockpitWithBackend();
+        if(claiming) return;
+        claiming=true;
+        const button=document.querySelector('#summary-modal .btn-lime-primary');button.disabled=true;
+        try {
+            localStorage.removeItem('autoXAK_unclaimed_summary');
+            document.getElementById('summary-modal').classList.remove('active');
+            await syncCockpitWithBackend();
+        } catch(error) {window.alert(error.message);}
+        finally {claiming=false;button.disabled=false;}
     }
 
     window.addEventListener('online', () => {
@@ -1877,127 +1767,20 @@
     });
 
     window.addEventListener('DOMContentLoaded', async () => {
+        try {
         if (typeof AutoXAKSession !== 'undefined' && AutoXAKSession.ensureAuthenticated) {
             const session = await AutoXAKSession.ensureAuthenticated();
             if (session && session.userId) currentUserId = session.userId;
         }
+        try {
+            const summary=JSON.parse(localStorage.getItem('autoXAK_unclaimed_summary')||'null');
+            if(summary?.user_id===currentUserId){finishedResult=summary.pending?null:summary.result;lastFinishedSessionId=summary.session_id;showTripSummary(summary.result,{user_id:currentUserId},summary.pending);}
+        } catch(_){}
         await AutoXAKOfflineStore.recoverInterruptedTrips();
         await syncCockpitWithBackend();
+        } catch(error) {console.warn('Session initialization:',error.message);}
     });
 
-        // === AUTOXAK FULL REFERENCE PATCH V2 ===
-        (function() {
-            let _refPrevLat = null, _refPrevLon = null, _refPrevGpsMs = 0;
-            let _refGravX = 0.0, _refGravY = 0.0, _refGravZ = 9.81, _refGravInit = false;
-
-            // 1. Постоянная калибровка гравитации (устраняет 337 ложных неровностей в держателе)
-            window.addEventListener('devicemotion', function(e) {
-                if (typeof isDemoMode !== 'undefined' && isDemoMode) return;
-                const ag = e.accelerationIncludingGravity;
-                if (ag && ag.x !== null && ag.x !== undefined) {
-                    const rx = Number(ag.x) || 0.0;
-                    const ry = Number(ag.y) || 0.0;
-                    const rz = Number(ag.z) || 9.81;
-                    if (!_refGravInit) {
-                        _refGravX = rx; _refGravY = ry; _refGravZ = rz;
-                        _refGravInit = true;
-                    } else {
-                        const a = 0.96;
-                        _refGravX = a * _refGravX + (1 - a) * rx;
-                        _refGravY = a * _refGravY + (1 - a) * ry;
-                        _refGravZ = a * _refGravZ + (1 - a) * rz;
-                    }
-                    if (typeof gravX !== 'undefined') {
-                        gravX = _refGravX; gravY = _refGravY; gravZ = _refGravZ;
-                    }
-                    if (e.acceleration && e.acceleration.x !== null) {
-                        if (typeof linAx !== 'undefined') {
-                            linAx = Number(e.acceleration.x) || 0.0;
-                            linAy = Number(e.acceleration.y) || 0.0;
-                            linAz = Number(e.acceleration.z) || 0.0;
-                        }
-                    } else if (typeof linAx !== 'undefined') {
-                        linAx = rx - _refGravX;
-                        linAy = ry - _refGravY;
-                        linAz = rz - _refGravZ;
-                    }
-                }
-            }, { capture: true, passive: true });
-
-            // 2. Расчет скорости по координатам GPS (Haversine Fallback), чтобы экономия не замирала на 1.41р
-            if (navigator.geolocation && navigator.geolocation.watchPosition) {
-                const _origWatch = navigator.geolocation.watchPosition.bind(navigator.geolocation);
-                navigator.geolocation.watchPosition = function(successCb, errorCb, options) {
-                    return _origWatch(function(pos) {
-                        try {
-                            const nowMs = Date.now();
-                            const lat = pos.coords.latitude;
-                            const lon = pos.coords.longitude;
-                            const acc = pos.coords.accuracy || 999;
-                            let spd = pos.coords.speed;
-
-                            if ((spd === null || spd === undefined || spd === 0) && _refPrevLat !== null && acc <= 45) {
-                                const dtSec = (nowMs - _refPrevGpsMs) / 1000.0;
-                                if (dtSec >= 0.4 && dtSec <= 15.0) {
-                                    const R = 6371000.0;
-                                    const dLat = (lat - _refPrevLat) * Math.PI / 180.0;
-                                    const dLon = (lon - _refPrevLon) * Math.PI / 180.0;
-                                    const a = Math.sin(dLat / 2) ** 2 +
-                                              Math.cos(_refPrevLat * Math.PI / 180.0) * Math.cos(lat * Math.PI / 180.0) *
-                                              Math.sin(dLon / 2) ** 2;
-                                    const distM = R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1 - a)));
-                                    const calcSpd = distM / dtSec;
-                                    if (calcSpd >= 1.0 && calcSpd <= 55.0) {
-                                        spd = calcSpd;
-                                        if (typeof isRecording !== 'undefined' && isRecording && typeof liveDistanceKm !== 'undefined' && curSpeed < 0.5) {
-                                            liveDistanceKm += distM / 1000.0;
-                                        }
-                                    }
-                                }
-                            }
-                            if (acc <= 45) {
-                                _refPrevLat = lat; _refPrevLon = lon; _refPrevGpsMs = nowMs;
-                            }
-                            if (spd !== null && spd !== undefined && spd >= 0.5) {
-                                if (typeof curSpeed !== 'undefined') curSpeed = Number(spd);
-                                try {
-                                    Object.defineProperty(pos.coords, 'speed', { value: Number(spd), configurable: true });
-                                } catch (_) {}
-                            }
-                        } catch (err) {
-                            console.warn('[GPS Fallback]', err);
-                        }
-                        return successCb(pos);
-                    }, errorCb, options);
-                };
-            }
-
-            // 3. Безотказная обертка над всеми методами сохранения в AutoXAKOfflineStore
-            if (typeof AutoXAKOfflineStore !== 'undefined') {
-                const storeClone = Object.assign({}, AutoXAKOfflineStore);
-                for (const fnName of ['savePendingTrip', 'putTrip', 'saveTrip', 'enqueueTrip']) {
-                    if (typeof storeClone[fnName] === 'function') {
-                        const _origFn = storeClone[fnName].bind(AutoXAKOfflineStore);
-                        storeClone[fnName] = async function(tripPayload) {
-                            try {
-                                const slim = Object.assign({}, tripPayload);
-                                if (slim.raw_recording && Array.isArray(slim.telemetry_stream) && slim.telemetry_stream.length >= 50) {
-                                    delete slim.raw_recording;
-                                }
-                                return await _origFn(slim);
-                            } catch (err) {
-                                console.warn('[OfflineStore Fallback] Пропускаем сбой IndexedDB:', err);
-                                return { skipped_local: true, id: tripPayload && tripPayload.session_id };
-                            }
-                        };
-                    }
-                }
-                try {
-                    window.AutoXAKOfflineStore = storeClone;
-                } catch (_) {}
-            }
-        })();
-
     </script>
 </body>
 </html>
diff --git a/static/login.html b/static/login.html
index 4ca25f4..04acafc 100644
--- a/static/login.html
+++ b/static/login.html
@@ -1,16 +1,17 @@
-﻿<!DOCTYPE html>
+<!DOCTYPE html>
 <html lang="ru">
 <head>
     <meta charset="UTF-8">
-    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
+    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
     <title>autoXAK — Вход</title>
     <link rel="manifest" href="/static/manifest.json">
-    <meta name="theme-color" content="#121316">
+    <meta name="theme-color" content="#121417">
     <script src="/static/session.js"></script>
+    <script src="/static/offline_store.js"></script>
     <style>
         :root {
             --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Inter", system-ui, sans-serif;
-            --bg-root: #121316;
+            --bg-root: #121417;
             --bg-card: #1C1E22;
             --border-card: rgba(255, 255, 255, 0.08);
             --lime: #D2FA58;
@@ -43,7 +44,7 @@
 
         .mobile-container {
             width: 100%;
-            max-width: 480px;
+            max-width: 414px;
             height: 100%;
             height: 100dvh;
             background-color: var(--bg-root);
@@ -96,7 +97,7 @@
             width: 100%;
             height: 52px;
             background: var(--lime);
-            color: #121316;
+            color: #121417;
             border: none;
             border-radius: 16px;
             font-size: 15.5px;
@@ -148,12 +149,15 @@
             flex-shrink: 0;
         }
     </style>
+    <link rel="stylesheet" href="/static/app.css">
+    <link rel="stylesheet" href="/static/auth.css">
 </head>
 <body>
 
 <div class="mobile-container">
 
     <!-- Центральный логотип точь-в-точь по референсу Figma -->
+    <div id="welcome-view" class="auth-view">
     <div class="hero-section">
         <svg class="brand-icon" viewBox="0 0 180 170" fill="none" xmlns="http://www.w3.org/2000/svg">
             <!-- Полукруглая дуга спидометра (180 градусов, центр 90,86, радиус 56) -->
@@ -172,7 +176,7 @@
             <line x1="132" y1="84" x2="123" y2="84" stroke="#D2FA58" stroke-width="5.5" stroke-linecap="round"/>
 
             <!-- Стрелка спидометра и центральное кольцо -->
-            <circle cx="90" cy="84" r="7.5" stroke="#D2FA58" stroke-width="5.5" fill="#121316"/>
+            <circle cx="90" cy="84" r="7.5" stroke="#D2FA58" stroke-width="5.5" fill="#121417"/>
             <line x1="95.3" y1="78.7" x2="111.5" y2="62.5" stroke="#D2FA58" stroke-width="5.5" stroke-linecap="round"/>
 
             <!-- Ломаная стрелка снижения затрат -->
@@ -195,26 +199,96 @@
             Продолжить как гость
         </button>
 
-        <button type="button" class="btn-yandex" onclick="handleLoginWithYandex()">
-            <span class="yandex-badge">Я</span>
-            <span>Войти с Яндекс ID</span>
-        </button>
+        <button type="button" class="btn-yandex" onclick="showAuthForm('login')">Войти</button>
+        <button type="button" class="auth-link" onclick="showAuthForm('register')">Зарегистрироваться</button>
+        <p id="welcome-error" class="field-error" role="alert"></p>
     </div>
-
+    </div>
+    <section id="credentials-view" class="auth-view" hidden>
+        <button type="button" class="auth-back" onclick="showWelcome()">← Назад</button>
+        <h1 id="auth-title">Вход</h1>
+        <p id="register-description" class="auth-description" hidden>Сохраняйте историю поездок и возвращайтесь к результатам</p>
+        <form id="credentials-form" novalidate>
+            <label for="auth-login">Логин</label>
+            <input id="auth-login" name="login" autocomplete="username" autocapitalize="none" spellcheck="false" placeholder="Введите логин" aria-describedby="login-hint login-error">
+            <p id="login-hint" class="auth-hint">5–15 латинских букв</p>
+            <p id="login-error" class="field-error" role="alert"></p>
+            <label for="auth-password">Пароль</label>
+            <div class="password-wrap"><input id="auth-password" name="password" type="password" autocomplete="current-password" placeholder="Введите пароль" aria-describedby="password-hint password-error">
+                <button id="toggle-password" type="button" aria-label="Показать пароль" aria-pressed="false"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg></button></div>
+            <p id="password-hint" class="auth-hint">8–15 символов</p>
+            <p id="password-error" class="field-error" role="alert"></p>
+            <p id="auth-error" class="auth-error" role="alert" hidden></p>
+            <button id="auth-submit" class="btn-guest" type="submit">Войти</button>
+        </form>
+        <button id="auth-switch" type="button" class="auth-link" onclick="showAuthForm(authMode==='login'?'register':'login')">Нет аккаунта? Зарегистрироваться</button>
+    </section>
 </div>
 
 <script>
-    async function handleContinueAsGuest() {
-        try {
-            await AutoXAKSession.ensureAuthenticated({newGuest: true});
-            window.location.href = '/';
-        } catch (error) { window.alert(error.message); }
-    }
-
-    async function handleLoginWithYandex() {
-        try { await AutoXAKSession.loginWithYandex(); }
-        catch (error) { window.alert(error.message); }
-    }
+let authMode='login', submitting=false, blockedUntil=0;
+function showWelcome() {
+    document.getElementById('welcome-view').hidden=false;
+    document.getElementById('credentials-view').hidden=true;
+}
+function showAuthForm(mode) {
+    authMode=mode;
+    const register=mode==='register';
+    document.getElementById('welcome-view').hidden=true;
+    document.getElementById('credentials-view').hidden=false;
+    document.getElementById('auth-title').textContent=register?'Создать аккаунт':'Вход';
+    document.getElementById('register-description').hidden=!register;
+    document.getElementById('auth-submit').textContent=register?'Создать аккаунт':'Войти';
+    document.getElementById('auth-switch').textContent=register?'Уже есть аккаунт? Войти':'Нет аккаунта? Зарегистрироваться';
+    document.getElementById('auth-password').autocomplete=register?'new-password':'current-password';
+    for (const id of ['login-error','password-error','auth-error']) document.getElementById(id).textContent='';
+    document.getElementById('auth-error').hidden=true;
+}
+document.getElementById('toggle-password').addEventListener('click',()=>{
+    const input=document.getElementById('auth-password'), show=input.type==='password';
+    input.type=show?'text':'password';
+    document.getElementById('toggle-password').setAttribute('aria-pressed',String(show));
+    document.getElementById('toggle-password').setAttribute('aria-label',show?'Скрыть пароль':'Показать пароль');
+});
+async function handleContinueAsGuest() {
+    if(submitting) return;
+    submitting=true;
+    try {await AutoXAKSession.ensureAuthenticated({newGuest:true});window.location.assign('/');}
+    catch(error) {document.getElementById('welcome-error').textContent=error.message;}
+    finally {submitting=false;}
+}
+document.getElementById('credentials-form').addEventListener('submit',async event=>{
+    event.preventDefault();
+    if(submitting) return;
+    const login=document.getElementById('auth-login').value, password=document.getElementById('auth-password').value;
+    const loginError=/^[a-zA-Z]{5,15}$/.test(login)?'':'Используйте 5–15 латинских букв';
+    const passwordError=[...password].length>=8 && [...password].length<=15?'':'Пароль: 8–15 символов';
+    document.getElementById('login-error').textContent=loginError;
+    document.getElementById('password-error').textContent=passwordError;
+    if(loginError || passwordError) return;
+    const button=document.getElementById('auth-submit'), errorBox=document.getElementById('auth-error');
+    errorBox.hidden=true;
+    if(authMode==='login' && Date.now()<blockedUntil) {errorBox.textContent='Слишком много попыток. Попробуйте позже';errorBox.hidden=false;return;}
+    submitting=true;button.disabled=true;button.setAttribute('aria-busy','true');
+    const mode=authMode, previous=AutoXAKSession.getUserId();
+    const guest=AutoXAKSession.isGuest() && previous && AutoXAKSession.getToken();
+    try {
+        if (guest) await AutoXAKSession.syncPendingProfile();
+        const {response,data}=await AutoXAKSession.fetchJSON('/api/v1/auth/'+mode, {
+            method:'POST',headers:{'Content-Type':'application/json',...(guest?AutoXAKSession.getAuthHeaders():{})},
+            body:JSON.stringify({login,password,...(guest?{guest_id:previous}:{})})},15000);
+        if(!response.ok) {
+            if(mode==='login') document.getElementById('auth-password').value='';
+            if(response.status===429) {blockedUntil=Date.now()+1000*(Number(response.headers.get('Retry-After'))||300);}
+            if(response.status===409) {document.getElementById('login-error').textContent='Этот логин уже занят';return;}
+            throw new Error(typeof data?.detail==='string'?data.detail:'Не удалось выполнить вход. Повторите попытку.');
+        }
+        if(guest && previous!==data.user_id) await AutoXAKOfflineStore.reassignOwner(previous,data.user_id);
+        AutoXAKSession.adoptSession(data);
+        window.location.assign('/');
+    } catch(error) {errorBox.textContent=error.message;errorBox.hidden=false;}
+    finally {submitting=false;button.disabled=false;button.removeAttribute('aria-busy');}
+});
 </script>
 </body>
 </html>
diff --git a/static/manifest.json b/static/manifest.json
index 4805ebb..ff691ee 100644
--- a/static/manifest.json
+++ b/static/manifest.json
@@ -1,4 +1,4 @@
-﻿{
+{
   "name": "autoXAK — Бортовой помощник",
   "short_name": "autoXAK",
   "description": "Предиктивная телеметрия износа масла и экономия топлива",
diff --git a/static/offline_store.js b/static/offline_store.js
index f7f2f79..c34e966 100644
--- a/static/offline_store.js
+++ b/static/offline_store.js
@@ -1,18 +1,4 @@
-
-// Авто-сжатие пакета перед записью в IndexedDB (защита от переполнения квоты смартфона)
-function _compactTripForLocalStore(payload) {
-    if (!payload || typeof payload !== 'object') return payload;
-    const copy = Object.assign({}, payload);
-    if (copy.raw_recording && Array.isArray(copy.telemetry_stream) && copy.telemetry_stream.length >= 50) {
-        delete copy.raw_recording;
-    }
-    if (Array.isArray(copy.telemetry_stream) && copy.telemetry_stream.length > 3000) {
-        copy.telemetry_stream = copy.telemetry_stream.filter((_, idx) => idx % 2 === 0);
-    }
-    return copy;
-}
-
-﻿/** Pending trips are acknowledged only after the IndexedDB transaction commits. */
+/** Pending trips are acknowledged only after the IndexedDB transaction commits. */
 const AutoXAKOfflineStore = window.AutoXAKOfflineStore = (() => {
     const DB_NAME = 'autoXAK_storage', STORE = 'pending_trips', CONTROLS = 'pending_controls';
     let opening = null, syncing = null;
@@ -149,7 +135,7 @@ const AutoXAKOfflineStore = window.AutoXAKOfflineStore = (() => {
                 duration_seconds:result.duration_seconds, distance_km:result.distance_km,
                 total_savings_rub:result.cost_savings_rub, hard_accel_count:result.harsh_accel_count,
                 hard_brake_count:result.harsh_brake_count, bumps_count:result.road_anomalies_count,
-                idle_minutes:Math.round((result.traffic_jam_seconds || 0)/60), capture_quality:result.metrics?.capture_quality,math_status:result.metrics?.math_status};
+                smooth_score:result.smooth_score, idle_minutes:Math.round((result.traffic_jam_seconds || 0)/60), capture_quality:result.metrics?.capture_quality,math_status:result.metrics?.math_status};
             if (trip.session_id !== result.session_id) delete map[trip.session_id];
             map[result.session_id] = details;
             localStorage.setItem('autoXAK_trip_details_map', JSON.stringify(map));
diff --git a/static/progress.js b/static/progress.js
new file mode 100644
index 0000000..2c87930
--- /dev/null
+++ b/static/progress.js
@@ -0,0 +1,42 @@
+const AutoXAKProgress = (() => {
+    const revisions=new Map();
+    async function refresh(id,days) {
+        const root=document.getElementById(id), owner=AutoXAKSession.getUserId();
+        if(!root || !owner) return;
+        const revision=(revisions.get(id)||0)+1;revisions.set(id,revision);
+        root.querySelectorAll('[data-days]').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.days)===days)));
+        try {
+            const params=new URLSearchParams({days:String(days),utc_offset_minutes:String(-new Date().getTimezoneOffset())});
+            const {response,data}=await AutoXAKSession.fetchJSON(`/api/v1/users/${encodeURIComponent(owner)}/progress?${params}`,{headers:AutoXAKSession.getAuthHeaders()});
+            if(!response.ok || revisions.get(id)!==revision || AutoXAKSession.getUserId()!==owner) return;
+            const values=root.querySelector('.progress-values'), empty=root.querySelector('.progress-empty'), chart=root.querySelector('.progress-chart');
+            const available=Number.isFinite(data.average);
+            values.style.display=available?'block':'none';
+            empty.hidden=data.sufficient_data;
+            empty.textContent=available?'Недостаточно данных для динамики. Совершите поездки в другие дни.':'Недостаточно данных. Совершите поездку, чтобы увидеть прогресс.';
+            root.querySelector('.progress-number').textContent=available?String(data.average):'—';
+            const delta=root.querySelector('.progress-delta');
+            delta.textContent=Number.isFinite(data.delta)?`${data.delta>0?'+':''}${data.delta} пунктов`:'';
+            delta.className='progress-delta'+(data.delta>0?' positive':data.delta<0?' negative':'');
+            root.querySelector('.progress-caption').textContent=`Среднее за последние ${days} дней; сравнение с предыдущими ${days}`;
+            if(chart) {
+                chart.replaceChildren();chart.style.display=data.sufficient_data?'block':'none';
+                if(!data.sufficient_data) return;
+                const ns='http://www.w3.org/2000/svg';
+                const add=(tag,attrs,text)=>{const e=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text)e.textContent=text;chart.append(e);return e;};
+                for(const score of [0,50,100]) {const y=120-score;add('line',{x1:28,x2:300,y1:y,y2:y,stroke:'#363A3F'});add('text',{x:0,y:y+4,fill:'#8C919B','font-size':10},String(score));}
+                let segment=[];
+                const flush=()=>{if(segment.length>1)add('polyline',{points:segment.join(' '),fill:'none',stroke:'#D2FA58','stroke-width':2});segment=[];};
+                data.points.forEach((point,i)=>{
+                    if(!Number.isFinite(point.score)){flush();return;}
+                    const x=28+272*i/Math.max(1,data.points.length-1),y=120-point.score;
+                    segment.push(`${x},${y}`);const dot=add('circle',{cx:x,cy:y,r:3,fill:'#D2FA58'});
+                    const title=document.createElementNS(ns,'title');title.textContent=`${point.date}: ${point.score}`;dot.append(title);
+                });flush();
+                add('text',{x:28,y:143,fill:'#8C919B','font-size':10},data.period_start);
+                add('text',{x:300,y:143,fill:'#8C919B','font-size':10,'text-anchor':'end'},data.period_end);
+            }
+        } catch(_) { /* Keep the last confirmed display during a network interruption. */ }
+    }
+    return {refresh};
+})();
diff --git a/static/session.js b/static/session.js
index aa993c8..b6ba454 100644
--- a/static/session.js
+++ b/static/session.js
@@ -1,4 +1,4 @@
-﻿/** Authentication and service-worker lifecycle shared by all pages. */
+/** Authentication and service-worker lifecycle shared by all pages. */
 const AutoXAKSession = window.AutoXAKSession = (() => {
     let authentication = null;
     let profileSync = null;
@@ -15,7 +15,10 @@ const AutoXAKSession = window.AutoXAKSession = (() => {
         const timeout = setTimeout(abort, timeoutMs);
         try {
             const response = await fetch(url, {...options, signal:controller.signal});
-            const data = response.ok ? await response.json() : null;
+            const data = await response.json().catch(error=>{
+                if (controller.signal.aborted || !(error instanceof SyntaxError)) throw error;
+                return null;
+            });
             return {response, data};
         } finally {
             clearTimeout(timeout);
@@ -34,7 +37,7 @@ const AutoXAKSession = window.AutoXAKSession = (() => {
     function storeSession(data) {
         const previous = getUserId();
         if (previous && previous !== data.user_id) {
-            for (const name of ['trip_details_map', 'offline_trips', 'local_history', 'profile', 'car_id', 'car_name', 'selected_car_id', 'selected_car_name', 'pending_profile', 'fuel_price', 'fuel_pass', 'dashboard_cache']) {
+            for (const name of ['trip_details_map', 'offline_trips', 'local_history', 'profile', 'car_id', 'car_name', 'selected_car_id', 'selected_car_name', 'pending_profile', 'fuel_price', 'fuel_pass', 'dashboard_cache', 'unclaimed_summary', 'progress_cache']) {
                 localStorage.removeItem(key(name));
             }
         }
@@ -71,6 +74,7 @@ const AutoXAKSession = window.AutoXAKSession = (() => {
             if (navigator.onLine) window.location.replace('/login');
             throw new Error('Для восстановления сессии необходимо войти заново');
         }
+        if (!options.newGuest) {window.location.replace('/login');throw new Error('Выберите способ входа');}
         const {response, data} = await fetchJSON('/api/v1/auth/guest', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
         if (!response.ok) throw new Error(`Не удалось создать сессию: HTTP ${response.status}`);
         return storeSession(data);
@@ -122,7 +126,7 @@ const AutoXAKSession = window.AutoXAKSession = (() => {
     }
 
     function clearSession() {
-        for (const name of ['jwt_token', 'user_id', 'is_guest', 'user_name', 'trip_details_map', 'offline_trips', 'local_history', 'profile', 'car_id', 'car_name', 'selected_car_id', 'selected_car_name', 'pending_profile', 'fuel_price', 'fuel_pass', 'dashboard_cache']) localStorage.removeItem(key(name));
+        for (const name of ['jwt_token', 'user_id', 'is_guest', 'user_name', 'trip_details_map', 'offline_trips', 'local_history', 'profile', 'car_id', 'car_name', 'selected_car_id', 'selected_car_name', 'pending_profile', 'fuel_price', 'fuel_pass', 'dashboard_cache', 'unclaimed_summary', 'progress_cache']) localStorage.removeItem(key(name));
     }
 
     async function loginWithYandex() {
diff --git a/static/setup.html b/static/setup.html
index 0a049a6..6f30ddd 100644
--- a/static/setup.html
+++ b/static/setup.html
@@ -1,14 +1,14 @@
-﻿<!DOCTYPE html>
+<!DOCTYPE html>
 <html lang="ru">
 <head>
     <meta charset="UTF-8">
-    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
+    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
     <title>autoXAK — Гараж</title>
     <script src="/static/session.js"></script>
     <style>
         :root {
             --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Inter", system-ui, sans-serif;
-            --bg-root: #121316;
+            --bg-root: #121417;
             --bg-card: #1C1E22;
             --bg-dropdown: #202329;
             --bg-dropdown-hover: #2A2E36;
@@ -150,7 +150,7 @@
             width: 100%;
             height: 54px;
             background: var(--lime);
-            color: #121316;
+            color: #121417;
             border: none;
             border-radius: 16px;
             font-size: 15px;
@@ -474,7 +474,7 @@
             width: 100%;
             height: 100%;
             height: 100dvh;
-            background-color: var(--bg-root, var(--bg-screen, #121316));
+            background-color: var(--bg-root, var(--bg-screen, #121417));
             margin: 0;
             padding: 0;
             overflow: hidden;
@@ -483,7 +483,7 @@
 
         .mobile-container {
             width: 100%;
-            max-width: 480px;
+            max-width: 414px;
             height: 100%;
             height: 100dvh;
             max-height: none !important;
@@ -498,6 +498,7 @@
             display: none !important;
         }
     </style>
+    <link rel="stylesheet" href="/static/app.css">
 </head>
 <body>
 
@@ -553,6 +554,7 @@
 
         <!-- ВКЛАДКА 1: ИЗ РЕЕСТРА (МАКЕТ 2) -->
         <div class="tab-pane active" id="pane-registry">
+            <p id="catalog-status" class="status-message" role="status"></p>
             <!-- Марка -->
             <div class="field-group">
                 <label class="field-label">Марка</label>
@@ -598,8 +600,12 @@
                 </div>
             </div>
 
+            <div class="field-group"><label class="field-label" for="registry-fuel">Городской расход, л/100 км</label>
+                <input class="text-input-box" id="registry-fuel" type="number" min="0.1" max="100" step="0.1" inputmode="decimal" placeholder="Из паспорта автомобиля">
+                <span class="field-hint">Используется для расчётной экономии</span></div>
             <div class="bottom-cta-spacer">
                 <button class="btn-lime" type="button" onclick="saveRegistryVehicle()">Сохранить</button>
+                <button class="segment-btn" onclick="switchEditMode('manual')">Моего автомобиля нет в списке</button>
             </div>
         </div>
 
@@ -728,6 +734,9 @@
                 </div>
             </div>
 
+            <div class="field-group"><label class="field-label" for="manual-fuel">Городской расход, л/100 км</label>
+                <input class="text-input-box" id="manual-fuel" type="number" min="0.1" max="100" step="0.1" inputmode="decimal" placeholder="Из паспорта автомобиля">
+                <span class="field-hint">Используется для расчётной экономии</span></div>
             <div class="bottom-cta-spacer">
                 <button class="btn-lime" type="button" onclick="saveManualVehicle()">Сохранить</button>
             </div>
@@ -736,33 +745,24 @@
 
     <!-- Нижняя панель навигации с подписями -->
     <nav class="bottom-tab-bar" id="bottom-tab-bar">
-        <a href="/history" class="tab-link" aria-label="История поездок">
-            <div class="tab-pill">
-                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
-                    <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
-                    <path d="M3 3v5h5"/>
-                    <path d="M12 7v5l4 2"/>
-                </svg>
-            </div>
-            <span class="tab-caption">История поездок</span>
+        <a href="/history" class="tab-item" aria-label="История поездок">
+            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
+                <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
+                <path d="M3 3v5h5"/>
+                <path d="M12 7v5l4 2"/>
+            </svg>
         </a>
-        <a href="/" class="tab-link" aria-label="Главная">
-            <div class="tab-pill">
-                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
-                    <path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1V9.5z"/>
-                </svg>
-            </div>
-            <span class="tab-caption">Главная</span>
+        <a href="/" class="tab-item" aria-label="Главный экран">
+            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
+                <path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1V9.5z"/>
+            </svg>
         </a>
-        <a href="/setup" class="tab-link active" aria-label="Гараж">
-            <div class="tab-pill">
-                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
-                    <path d="M14 16H9m10 0h3v-3.15a1 1 0 0 0-.84-.99L16 11l-2.7-3.6a1 1 0 0 0-.8-.4H5.24a2 2 0 0 0-1.8 1.1l-.8 1.63A6 6 0 0 0 2 12.42V16h2"/>
-                    <circle cx="6.5" cy="16.5" r="2.5"/>
-                    <circle cx="16.5" cy="16.5" r="2.5"/>
-                </svg>
-            </div>
-            <span class="tab-caption">Гараж</span>
+        <a href="/setup" class="tab-item active" aria-label="Мой автомобиль">
+            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
+                <path d="M14 16H9m10 0h3v-3.15a1 1 0 0 0-.84-.99L16 11l-2.7-3.6a1 1 0 0 0-.8-.4H5.24a2 2 0 0 0-1.8 1.1l-.8 1.63A6 6 0 0 0 2 12.42V16h2"/>
+                <circle cx="6.5" cy="16.5" r="2.5"/>
+                <circle cx="16.5" cy="16.5" r="2.5"/>
+            </svg>
         </a>
     </nav>
 
@@ -773,190 +773,22 @@
 
     let currentUserId = localStorage.getItem('autoXAK_user_id') || '';
 
-    const FALLBACK_CATALOG = [
-        {
-            id: 'haval',
-            name: 'Haval',
-            models: [
-                {
-                    id: 'haval_jolion',
-                    name: 'Jolion',
-                    trims: [
-                        { id: 'haval_jolion_15t_4wd', badge_name: '1.5T 4WD', fuel_rate: 8.5, mass_kg: 1505 },
-                        { id: 'haval_jolion_15t_fwd', badge_name: '1.5T 2WD', fuel_rate: 8.1, mass_kg: 1425 }
-                    ]
-                },
-                {
-                    id: 'haval_f7',
-                    name: 'F7',
-                    trims: [
-                        { id: 'haval_f7_15t_4wd', badge_name: '1.5T 4WD', fuel_rate: 8.9, mass_kg: 1680 },
-                        { id: 'haval_f7_20t_4wd', badge_name: '2.0T 4WD', fuel_rate: 9.4, mass_kg: 1735 }
-                    ]
-                }
-            ]
-        },
-        {
-            id: 'chery',
-            name: 'Chery',
-            models: [
-                {
-                    id: 'chery_tiggo_7pro',
-                    name: 'Tiggo 7 Pro',
-                    trims: [
-                        { id: 'chery_tiggo_7pro', badge_name: '1.5T CVT', fuel_rate: 8.8, mass_kg: 1465 },
-                        { id: 'chery_tiggo_7pro_16t_awd', badge_name: '1.6T AWD', fuel_rate: 8.6, mass_kg: 1580 }
-                    ]
-                }
-            ]
-        },
-        {
-            id: 'geely',
-            name: 'Geely',
-            models: [
-                {
-                    id: 'geely_coolray',
-                    name: 'Coolray',
-                    trims: [
-                        { id: 'geely_coolray_15t', badge_name: '1.5T DCT', fuel_rate: 7.9, mass_kg: 1390 }
-                    ]
-                },
-                {
-                    id: 'geely_monjaro',
-                    name: 'Monjaro',
-                    trims: [
-                        { id: 'geely_monjaro_20t_4wd', badge_name: '2.0T 4WD', fuel_rate: 9.5, mass_kg: 1855 }
-                    ]
-                }
-            ]
-        },
-        {
-            id: 'skoda',
-            name: 'Skoda',
-            models: [
-                {
-                    id: 'skoda_octavia',
-                    name: 'Octavia',
-                    trims: [
-                        { id: 'skoda_octavia_14tsi', badge_name: '1.4 TSI DSG', fuel_rate: 6.9, mass_kg: 1280 },
-                        { id: 'skoda_octavia_20tsi_dsg_4wd', badge_name: '2.0 TSI 4WD', fuel_rate: 7.8, mass_kg: 1450 },
-                        { id: 'skoda_octavia_16mpi_mt', badge_name: '1.6 MPI MT', fuel_rate: 7.5, mass_kg: 1245 }
-                    ]
-                }
-            ]
-        },
-        {
-            id: 'audi',
-            name: 'Audi',
-            models: [
-                {
-                    id: 'audi_q3',
-                    name: 'Q3',
-                    trims: [
-                        { id: 'audi_q3_20tfsi_180_culb', badge_name: '2.0 TFSI quattro', fuel_rate: 8.6, mass_kg: 1590 }
-                    ]
-                }
-            ]
-        },
-        {
-            id: 'lada',
-            name: 'LADA',
-            models: [
-                {
-                    id: 'lada_vesta',
-                    name: 'Vesta',
-                    trims: [
-                        { id: 'lada_vesta_16_mt', badge_name: '1.6 MT', fuel_rate: 7.5, mass_kg: 1230 },
-                        { id: 'lada_vesta_18_cvt', badge_name: '1.8 CVT', fuel_rate: 7.8, mass_kg: 1280 }
-                    ]
-                }
-            ]
-        }
-    ];
-
     let catalogTree = [];
 
-    // ---------------- КАСТОМНЫЙ ДВИЖОК ВЫПАДАЮЩИХ СПИСКОВ ----------------
-    function closeAllCustomDropdowns() {
-        document.querySelectorAll('.split-select-wrap.open').forEach(wrap => {
-            wrap.classList.remove('open');
-        });
-    }
-
-    function buildCustomDropdownMenu(wrap) {
-        const selectEl = wrap.querySelector('select.native-select-overlay');
-        const menuEl = wrap.querySelector('.custom-dropdown-menu');
-        if (!selectEl || !menuEl) return;
-
-        menuEl.innerHTML = '';
-        const options = Array.from(selectEl.options);
-
-        options.forEach((opt, idx) => {
-            const item = document.createElement('div');
-            item.className = 'custom-dropdown-item' + (idx === selectEl.selectedIndex ? ' selected' : '');
-            item.innerHTML = `
-                <span>${opt.text}</span>
-                <svg class="custom-dropdown-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
-                    <polyline points="20 6 9 17 4 12"></polyline>
-                </svg>
-            `;
-            item.addEventListener('click', (e) => {
-                e.stopPropagation();
-                selectEl.selectedIndex = idx;
-                selectEl.value = opt.value;
-                selectEl.dispatchEvent(new Event('change'));
-                closeAllCustomDropdowns();
-            });
-            menuEl.appendChild(item);
-        });
-    }
-
-    function initCustomDropdowns() {
-        document.querySelectorAll('.split-select-wrap').forEach(wrap => {
-            wrap.addEventListener('click', (e) => {
-                e.stopPropagation();
-                const isAlreadyOpen = wrap.classList.contains('open');
-                closeAllCustomDropdowns();
-
-                if (!isAlreadyOpen) {
-                    buildCustomDropdownMenu(wrap);
-                    wrap.classList.add('open');
-                    const menuEl = wrap.querySelector('.custom-dropdown-menu');
-                    if (menuEl) {
-                        const selectedItem = menuEl.querySelector('.custom-dropdown-item.selected');
-                        if (selectedItem) {
-                            selectedItem.scrollIntoView({ block: 'nearest' });
-                        }
-                        setTimeout(() => {
-                            menuEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
-                        }, 60);
-                    }
-                }
-            });
-        });
-
-        document.addEventListener('click', () => {
-            closeAllCustomDropdowns();
-        });
-    }
-
-    // ---------------- НАВИГАЦИЯ МЕЖДУ ЭКРАНАМИ ГАРАЖА ----------------
+    // ---------------- ПЕРЕКЛЮЧЕНИЕ ЭКРАНОВ И НАТИВНЫЕ СПИСКИ ----------------
     function openEditScreen() {
-        closeAllCustomDropdowns();
         document.getElementById('screen-garage-view').classList.remove('active');
         document.getElementById('screen-garage-edit').classList.add('active');
         document.getElementById('bottom-tab-bar').classList.add('hidden');
     }
 
     function closeEditScreen() {
-        closeAllCustomDropdowns();
         document.getElementById('screen-garage-edit').classList.remove('active');
         document.getElementById('screen-garage-view').classList.add('active');
         document.getElementById('bottom-tab-bar').classList.remove('hidden');
     }
 
     function switchEditMode(mode) {
-        closeAllCustomDropdowns();
         const isRegistry = mode === 'registry';
         document.getElementById('tab-btn-registry').classList.toggle('active', isRegistry);
         document.getElementById('tab-btn-manual').classList.toggle('active', !isRegistry);
@@ -991,7 +823,7 @@
     // ---------------- ЗАГРУЗКА И ПАРСИНГ КАТАЛОГА ----------------
     function normalizeCatalogData(raw) {
         const items = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.catalog) ? raw.catalog : []);
-        if (!items.length) return FALLBACK_CATALOG;
+        if (!items.length) return [];
 
         if (items[0] && Array.isArray(items[0].models)) {
             return items.map(mk => ({
@@ -1003,19 +835,19 @@
                     trims: (md.trims || []).map(tr => ({
                         id: tr.id,
                         badge_name: cleanTrimBadge(tr.badge_name || tr.name || tr.id, mk.name, md.name),
-                        fuel_rate: Number(tr.base_city_fuel_rate_l100km || tr.fuel_city_l100km || 8.2),
-                        mass_kg: Number(tr.curb_weight_kg || 1500)
+                        fuel_rate: tr.base_city_fuel_rate_l100km ?? null,
+                        mass_kg: tr.curb_weight_kg ?? null
                     }))
                 }))
             }));
         }
 
-        const makesMap = {};
+        const makesMap = Object.create(null);
         for (const item of items) {
             const mkName = item.make || item.brand || item.name || 'Марка';
             const mdName = item.model || 'Модель';
             if (!makesMap[mkName]) {
-                makesMap[mkName] = { id: mkName.toLowerCase(), name: mkName, models: {} };
+                makesMap[mkName] = { id: mkName.toLowerCase(), name: mkName, models: Object.create(null) };
             }
             if (!makesMap[mkName].models[mdName]) {
                 makesMap[mkName].models[mdName] = { id: mdName.toLowerCase(), name: mdName, trims: [] };
@@ -1024,8 +856,8 @@
             makesMap[mkName].models[mdName].trims.push({
                 id: item.id,
                 badge_name: cleanTrimBadge(rawTrimName, mkName, mdName),
-                fuel_rate: Number(item.base_city_fuel_rate_l100km || item.fuel_city_l100km || 8.2),
-                mass_kg: Number(item.curb_weight_kg || 1500)
+                fuel_rate: item.base_city_fuel_rate_l100km ?? null,
+                mass_kg: item.curb_weight_kg ?? null
             });
         }
 
@@ -1035,7 +867,7 @@
             models: Object.values(mk.models)
         }));
 
-        return tree.length > 0 ? tree : FALLBACK_CATALOG;
+        return tree.length > 0 ? tree : [];
     }
 
     async function initCatalog() {
@@ -1044,17 +876,18 @@
             if (res.ok) {
                 catalogTree = normalizeCatalogData(raw);
             } else {
-                catalogTree = FALLBACK_CATALOG;
+                catalogTree = [];
             }
         } catch (e) {
-            catalogTree = FALLBACK_CATALOG;
+            catalogTree = [];
         }
+        document.getElementById("catalog-status").textContent = catalogTree.length ? "" : "Каталог сейчас недоступен. Можно добавить автомобиль вручную.";
         populateMakes();
     }
 
     function populateMakes() {
         const makeSel = document.getElementById('select-make');
-        makeSel.innerHTML = catalogTree.map(mk => `<option value="${mk.id}">${mk.name}</option>`).join('');
+        makeSel.replaceChildren(...catalogTree.map(mk => new Option(mk.name,mk.id)));
         onMakeChange();
     }
 
@@ -1067,7 +900,7 @@
 
         const modelSel = document.getElementById('select-model');
         const models = makeObj ? makeObj.models : [];
-        modelSel.innerHTML = models.map(md => `<option value="${md.id}">${md.name}</option>`).join('');
+        modelSel.replaceChildren(...models.map(md => new Option(md.name,md.id)));
         onModelChange();
     }
 
@@ -1081,7 +914,7 @@
 
         const trimSel = document.getElementById('select-trim');
         const trims = modelObj ? modelObj.trims : [];
-        trimSel.innerHTML = trims.map(tr => `<option value="${tr.id}">${tr.badge_name}</option>`).join('');
+        trimSel.replaceChildren(...trims.map(tr => new Option(tr.badge_name,tr.id)));
         onTrimChange();
     }
 
@@ -1089,6 +922,8 @@
         const trimSel = document.getElementById('select-trim');
         const opt = trimSel.options[trimSel.selectedIndex];
         document.getElementById('label-trim').innerText = opt ? opt.text : '';
+        const selected = catalogTree.flatMap(m=>m.models).flatMap(m=>m.trims).find(t=>t.id===trimSel.value);
+        document.getElementById('registry-fuel').value = selected?.fuel_rate ?? '';
     }
 
     function selectTrimInDropdowns(targetCarId) {
@@ -1119,6 +954,7 @@
 
         let carName = localStorage.getItem('autoXAK_car_name') || '';
         let carId = localStorage.getItem('autoXAK_car_id') || '';
+        let fuelRate = Number(localStorage.getItem('autoXAK_fuel_pass')) || null;
 
         const savedProfile = localStorage.getItem('autoXAK_profile');
         if (savedProfile) {
@@ -1138,6 +974,7 @@
                 if (!localStorage.getItem('autoXAK_pending_profile')) {
                     carName = prof.car_name || '';
                     carId = prof.car_id || '';
+                    fuelRate = prof.fuel_consumption;
                     for (const prefix of ['autoXAK_', 'autoXAK_selected_']) {
                         localStorage.setItem(prefix+'car_id', carId);
                         localStorage.setItem(prefix+'car_name', carName);
@@ -1146,15 +983,14 @@
             }
         } catch (_) {}
 
-        if (carId) {
-            selectTrimInDropdowns(carId);
-        }
+        const matchedCar = carId && selectTrimInDropdowns(carId);
+        if (matchedCar && Number.isFinite(fuelRate)) document.getElementById("registry-fuel").value=fuelRate;
 
         if (!carName) {
-            const mk = document.getElementById('label-make').innerText;
+            const mk = matchedCar ? document.getElementById('label-make').innerText : '';
             const md = document.getElementById('label-model').innerText;
             const tr = document.getElementById('label-trim').innerText;
-            carName = (mk && md) ? `${mk} ${md} ${tr}`.trim() : 'Haval Jolion 1.5T 4WD';
+            carName = (mk && md) ? `${mk} ${md} ${tr}`.trim() : 'Мой автомобиль';
         }
 
         document.getElementById('garage-current-car-name').innerText = carName;
@@ -1165,7 +1001,8 @@
         const makeName = document.getElementById('label-make').innerText.trim();
         const modelName = document.getElementById('label-model').innerText.trim();
         const trimBadge = document.getElementById('label-trim').innerText.trim();
-        const trimId = document.getElementById('select-trim').value || 'haval_jolion_15t_4wd';
+        const trimId = document.getElementById('select-trim').value;
+        if (!trimId) {window.alert('Каталог пуст. Добавьте автомобиль вручную.');return;}
 
         const fullCarName = `${makeName} ${modelName} ${trimBadge}`.replace(/\s+/g, ' ').trim();
 
@@ -1182,14 +1019,17 @@
         await persistVehicleSelection({
             car_id: trimId,
             car_name: fullCarName,
-            fuel_consumption: fuelPass
+            fuel_consumption: Number(document.getElementById("registry-fuel").value) || fuelPass
         });
     }
 
     // ---------------- СОХРАНЕНИЕ: ВРУЧНУЮ (МАКЕТ 3) ----------------
     async function saveManualVehicle() {
         const rawInputName = document.getElementById('input-custom-name').value.trim();
-        const customCarName = rawInputName.length > 0 ? rawInputName.slice(0, 12) : 'Мой автомобиль';
+        if (!rawInputName || [...rawInputName].length>12) {window.alert('Название: от 1 до 12 символов');return;}
+        const customCarName = rawInputName;
+        const declaredFuel = Number(document.getElementById('manual-fuel').value);
+        if (!Number.isFinite(declaredFuel) || declaredFuel<=0 || declaredFuel>100) {window.alert('Для расчёта рублей укажите городской расход больше 0 и до 100 л/100 км.');return;}
 
         const bodyType = document.getElementById('select-body').value;
         const massKg = parseInt(document.getElementById('select-mass').value, 10) || 1600;
@@ -1224,7 +1064,7 @@
         await persistVehicleSelection({
             car_id: synthesizedCarId,
             car_name: customCarName,
-            fuel_consumption: null,
+            fuel_consumption: declaredFuel,
             curb_weight_kg: massKg,
             engine_displacement_l: engineL,
             drivetrain_type: driveType,
@@ -1234,6 +1074,7 @@
 
     // ---------------- ЕДИНАЯ ФУНКЦИЯ СОХРАНЕНИЯ И СИНХРОНИЗАЦИИ ----------------
     async function persistVehicleSelection(vehicleData) {
+        if (!Number.isFinite(vehicleData.fuel_consumption) || vehicleData.fuel_consumption<=0 || vehicleData.fuel_consumption>100) {window.alert('Для расчёта рублей укажите городской расход больше 0 и до 100 л/100 км.');return;}
         localStorage.setItem('autoXAK_car_id', vehicleData.car_id);
         localStorage.setItem('autoXAK_car_name', vehicleData.car_name);
         localStorage.setItem('autoXAK_selected_car_id', vehicleData.car_id);
@@ -1257,16 +1098,18 @@
     }
 
     window.addEventListener('DOMContentLoaded', async () => {
+        try {
         const session = await AutoXAKSession.ensureAuthenticated();
         currentUserId = session.userId;
         await AutoXAKSession.syncPendingProfile();
-        initCustomDropdowns();
+        // Native select preserves iOS, keyboard and screen-reader access.
         await initCatalog();
         await loadUserGarageState();
 
         if (window.location.search.includes('edit=1')) {
             openEditScreen();
         }
+        } catch(error) {console.warn('Garage initialization:',error.message);}
     });
 </script>
 </body>
diff --git a/static/sw.js b/static/sw.js
index ff062ac..a2a155b 100644
--- a/static/sw.js
+++ b/static/sw.js
@@ -1,5 +1,5 @@
-const CACHE_NAME = 'autoxak-shell-v4';
-const STATIC_ASSETS = ['/', '/setup', '/history', '/login', '/static/session.js', '/static/offline_store.js', '/static/manifest.json', '/static/icon-192.png', '/static/icon-512.png'];
+const CACHE_NAME = 'autoxak-shell-v6';
+const STATIC_ASSETS = ['/', '/setup', '/history', '/login', '/static/session.js', '/static/app.css', '/static/auth.css', '/static/progress.js', '/static/offline_store.js', '/static/manifest.json', '/static/icon-192.png', '/static/icon-512.png'];
 self.addEventListener('install', event => {
     event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting()));
 });
diff --git a/test_cpa_funnel.py b/test_cpa_funnel.py
index cde84fc..e3ca92a 100644
--- a/test_cpa_funnel.py
+++ b/test_cpa_funnel.py
@@ -1,4 +1,4 @@
-﻿import json
+import json
 import ssl
 import urllib.request
 
diff --git a/tests/browser/live_metrics.cjs b/tests/browser/live_metrics.cjs
index b640f5f..7219b01 100644
--- a/tests/browser/live_metrics.cjs
+++ b/tests/browser/live_metrics.cjs
@@ -16,14 +16,14 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         const headers=await page.evaluate(()=>AutoXAKSession.getAuthHeaders());
         const owner=await page.evaluate(()=>AutoXAKSession.getUserId());
         await page.evaluate(()=>{
-            let clock=Date.now(),gps;
+            let clock=Date.now(),gps,meters=0;
             Date.now=()=>clock;
             window.__advance=ms=>{clock+=ms;};
             Object.defineProperty(DeviceMotionEvent,'requestPermission',{configurable:true,value:()=>Promise.resolve('granted')});
             Object.defineProperty(navigator,'geolocation',{configurable:true,value:{
                 watchPosition:callback=>{gps=callback;return 0;},clearWatch:()=>{}
             }});
-            window.__gps=speed=>gps({timestamp:clock,coords:{latitude:55.75,longitude:37.61,accuracy:5,speed}});
+            window.__gps=speed=>{meters+=speed;gps({timestamp:clock,coords:{latitude:55.75+meters/111194.9266,longitude:37.61,accuracy:5,speed}});};
             window.__motion=(gravity,linear=[0,0,0],rotation=0,withLinear=true)=>{
                 clock+=20;
                 const event=new Event('devicemotion');
@@ -59,16 +59,16 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
             for(let i=0;i<5;i++) __motion([0,9.81,0],[0,5,0],0,false);
             return {counts,beforeRotation,afterRotation:beforeFallback,fallback:liveBumpsCount-beforeFallback};
         });
-        assert(geometry.counts.every(value=>value.difference===1));
+        assert(geometry.counts.every(value=>value.difference===0));
         assert(geometry.counts.every(value=>value.horizontal<0.001 && Math.abs(value.vertical-5)<0.001));
         assert.equal(geometry.afterRotation,geometry.beforeRotation);
-        assert.equal(geometry.fallback,1);
-        assert.equal(await page.locator('#trip-live-savings').innerText(),'—');
+        assert.equal(geometry.fallback,0);
+        assert.equal(await page.locator('#trip-live-savings').innerText(),'+0.00 ₽');
         await page.evaluate(()=>finishActiveTrip());
-        assert.equal(await page.locator('#sum-bumps').innerText(),'5');
-        assert.equal(await page.locator('#sum-savings-rub').innerText(),'—');
+        assert.equal(await page.locator('#sum-bumps').innerText(),'0');
+        assert.equal(await page.locator('#sum-savings-rub').innerText(),'+0.00 ₽');
         assert.equal(await page.locator('#sum-offline-banner').evaluate(el=>el.classList.contains('visible')),false);
-        await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));
+        await page.evaluate(()=>claimToPiggyBank());
 
         const before=(await (await page.request.get(`${base}/api/v1/users/${owner}/dashboard`,{headers})).json());
         await start();
@@ -82,17 +82,17 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         const previewResponse=page.waitForResponse('**/api/v1/telemetry/preview');
         await page.evaluate(()=>updateLiveEstimate());
         const preview=await (await previewResponse).json();
-        assert.equal(preview.cost_savings_rub,null);
-        assert.equal(preview.math_status.monetary_credit_status,'not_verified');
-        assert.equal(await page.locator('#trip-live-savings').innerText(),'—');
+        assert(preview.cost_savings_rub>0);
+        assert.equal(preview.math_status.monetary_credit_status,'model_estimate');
+        assert.match(await page.locator('#trip-live-savings').innerText(),/^\+[0-9]+\.[0-9]{2} ₽$/);
         assert.deepEqual(await (await page.request.get(`${base}/api/v1/users/${owner}/dashboard`,{headers})).json(),before);
         await page.evaluate(()=>finishActiveTrip());
-        assert.equal(await page.locator('#sum-savings-rub').innerText(),'—');
+        assert(Number((await page.locator('#sum-savings-rub').innerText()).replace(/[+ ₽]/g,''))>0);
         const after=await (await page.request.get(`${base}/api/v1/users/${owner}/dashboard`,{headers})).json();
         assert.equal(after.total_trips,before.total_trips+1);
 
         // A response for a finished recording must not update the next trip.
-        await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));
+        await page.evaluate(()=>claimToPiggyBank());
         await start();
         await page.evaluate(()=>{__gps(10);for(let i=0;i<100;i++) __motion([0,9.81,0]);});
         let releaseOldPreview;
@@ -108,16 +108,16 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         await page.evaluate(()=>{window.__oldPreview=updateLiveEstimate();});
         await requested;
         await page.evaluate(()=>finishActiveTrip());
-        await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));
+        await page.evaluate(()=>claimToPiggyBank());
         await start();
         releaseOldPreview();
         await page.evaluate(()=>window.__oldPreview);
-        assert.equal(await page.locator('#trip-live-savings').innerText(),'—');
+        assert.equal(await page.locator('#trip-live-savings').innerText(),'+0.00 ₽');
         await page.unroute('**/api/v1/telemetry/preview');
         await page.evaluate(()=>finishActiveTrip());
 
         // Long recordings retain their raw samples while bounding preview uploads.
-        await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));
+        await page.evaluate(()=>claimToPiggyBank());
         await start();
         await page.evaluate(()=>{
             __gps(10);
@@ -132,7 +132,7 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         assert.equal(sampled.telemetry_stream.at(-1).t,await page.evaluate(()=>telemetryStream.at(-1).t));
         await page.evaluate(()=>finishActiveTrip());
         assert.deepEqual(errors,[]);
-        console.log('Live metrics: portrait/landscape/tilted impulses without GPS, rotation veto, gravity fallback, zero stationary money, hidden quality banner and non-crediting shared-model preview passed');
+        console.log('Live metrics: stationary speed veto, gravity projection, rotation veto, moving financial preview, stale replies and full long-capture retention passed');
         await context.close();
     } finally {await browser.close();}
 })().catch(error=>{console.error(error);process.exit(1);});
diff --git a/tests/browser/logic_audit.cjs b/tests/browser/logic_audit.cjs
index dc82925..792aa12 100644
--- a/tests/browser/logic_audit.cjs
+++ b/tests/browser/logic_audit.cjs
@@ -17,6 +17,9 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         await page.request.post(profileUrl,{headers:identity.headers,data:{fuel_price_rub:91.25}});
         await page.goto(base+'/setup');
         await page.waitForLoadState('networkidle');
+        await page.evaluate(()=>{openEditScreen();switchEditMode('manual');});
+        await page.fill('#input-custom-name','Testcar');
+        await page.fill('#manual-fuel','8.5');
         await page.evaluate(()=>saveManualVehicle());
         assert.equal((await (await page.request.get(profileUrl,{headers:identity.headers})).json()).fuel_price_rub,91.25);
         await page.request.post(profileUrl,{headers:identity.headers,data:{car_id:'haval_jolion_15t_4wd',car_name:'Updated elsewhere'}});
@@ -82,7 +85,8 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         // History must recover a durable trip even without the optional localStorage list.
         await page.route('**/api/v1/telemetry/session',route=>route.fulfill({status:503,body:'unavailable'}));
         await page.goto(base+'/history');
-        await page.waitForFunction(id=>loadedTrips.some(trip=>trip.session_id===id),id,{timeout:12000}).catch(async error=>{console.error(await page.evaluate(async()=>({loaded:loadedTrips,pending:await AutoXAKOfflineStore.getPendingTrips(),cache:localStorage.getItem('autoXAK_local_history')})),errors);throw error;});
+        await page.waitForFunction(()=>document.getElementById('pending-status').textContent.includes('1'));
+        assert.equal(await page.evaluate(id=>loadedTrips.some(trip=>trip.session_id===id),id),false);
         await page.unroute('**/api/v1/telemetry/session');
         await page.evaluate(()=>AutoXAKOfflineStore.syncPendingTrips());
         await page.evaluate(()=>fetchHistory());
diff --git a/tests/browser/package.json b/tests/browser/package.json
index eb886f2..5175e06 100644
--- a/tests/browser/package.json
+++ b/tests/browser/package.json
@@ -1,6 +1,6 @@
 {
   "name": "autoxak-browser-regressions",
   "private": true,
-  "scripts": {"test": "node smoke.cjs && node recording.cjs && node live_metrics.cjs && node logic_audit.cjs && node service_worker.cjs"},
+  "scripts": {"test": "node smoke.cjs && node recording.cjs && node live_metrics.cjs && node logic_audit.cjs && node service_worker.cjs && node product_epics.cjs"},
   "devDependencies": {"playwright": "1.62.1"}
 }
diff --git a/tests/browser/product_epics.cjs b/tests/browser/product_epics.cjs
new file mode 100644
index 0000000..02d2752
--- /dev/null
+++ b/tests/browser/product_epics.cjs
@@ -0,0 +1,100 @@
+const {chromium, webkit} = require('playwright');
+const assert=require('node:assert/strict');
+const fs=require('node:fs');
+const base=process.env.AUTOXAK_BASE_URL||'http://127.0.0.1:8000';
+const output=process.env.AUTOXAK_SCREENSHOTS||'/tmp/autoxak-product-screens';
+fs.mkdirSync(output,{recursive:true});
+(async()=>{
+ const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
+ try{
+  const context=await browser.newContext({viewport:{width:390,height:844}}), page=await context.newPage(), errors=[];
+  page.on('pageerror',e=>errors.push(e.message));
+  await page.addInitScript(()=>{window.__alerts=[];window.alert=s=>__alerts.push(s);});
+  await page.goto(base+'/login');
+  await page.getByRole('button',{name:'Войти',exact:true}).click();
+  await page.fill('#auth-login','ab12');await page.fill('#auth-password','short');await page.click('#auth-submit');
+  assert.match(await page.locator('#login-error').innerText(),/5–15/);
+  await page.evaluate(()=>showAuthForm('register'));
+  const login='Audit'+Math.random().toString(36).replace(/[^a-z]/g,'').slice(0,9).padEnd(8,'z');
+  await page.fill('#auth-login',login);await page.fill('#auth-password','Strong!Pass');
+  await page.click('#toggle-password');assert.equal(await page.locator('#auth-password').getAttribute('type'),'text');
+  await page.click('#auth-submit');await page.waitForURL(base+'/');
+  await page.waitForFunction(()=>typeof requestStartTrip==='function');
+  const identity=await page.evaluate(()=>({user:AutoXAKSession.getUserId(),headers:AutoXAKSession.getAuthHeaders()}));
+  for(const size of [{width:320,height:568},{width:390,height:844},{width:414,height:896},{width:768,height:1024},{width:1440,height:900},{width:844,height:390}]){
+   await page.setViewportSize(size);
+   for(const path of ['/','/setup','/history']) {
+    await page.goto(base+path);await page.waitForLoadState('networkidle');
+    const g=await page.evaluate(()=>{const box=document.querySelector('.mobile-container').getBoundingClientRect(),nav=document.querySelector('nav').getBoundingClientRect();return {width:box.width,x:box.x,overflow:document.documentElement.scrollWidth>innerWidth,navBottom:nav.bottom,h:innerHeight,navCount:document.querySelectorAll('nav a').length,bg:getComputedStyle(document.body).backgroundColor};});
+    assert.equal(g.width,Math.min(size.width,414));assert.equal(g.overflow,false);assert.equal(g.navCount,3);assert(Math.abs(g.navBottom-g.h)<2);assert.equal(g.bg,'rgb(18, 20, 23)');
+    await page.screenshot({path:`${output}/${size.width}x${size.height}-${path==='/'?'home':path.slice(1)}.png`});
+   }
+  }
+  await page.setViewportSize({width:390,height:844});await page.goto(base+'/setup?edit=1');
+  await page.waitForFunction(()=>document.getElementById('select-make').options.length>0);
+  await page.selectOption('#select-make',{label:'Haval'});
+  await page.selectOption('#select-trim','haval_jolion_15t_4wd');await page.fill('#registry-fuel','8.5');
+  await page.evaluate(()=>saveRegistryVehicle());
+  const profile=await(await page.request.get(`${base}/api/v1/users/${identity.user}/profile`,{headers:identity.headers})).json();
+  assert.equal(profile.car_id,'haval_jolion_15t_4wd');assert.equal(profile.fuel_consumption,8.5);
+  await page.goto(base+'/');await page.waitForLoadState('networkidle');
+  await page.evaluate(()=>{
+   let clock=Date.now(),gps;Date.now=()=>clock;
+   window.__permissionCalls=0;
+   Object.defineProperty(DeviceMotionEvent,'requestPermission',{configurable:true,value:()=>{__permissionCalls++;return Promise.resolve('granted');}});
+   Object.defineProperty(navigator,'geolocation',{configurable:true,value:{watchPosition:callback=>{gps=callback;return 0;},clearWatch:()=>{}}});
+   window.__record=()=>{
+    let meters=0, speed=0;
+    for(let i=0;i<=3000;i++){
+     const t=i*.02;const ax=t>=5&&t<7?3:t>=12&&t<14?-3:0;
+     speed=Math.max(0,speed+ax*.02);meters+=speed*.02;
+     if(i%50===0)gps({timestamp:clock,coords:{latitude:55.75+meters/111194.9266,longitude:37.61,accuracy:5,speed}});
+     const event=new Event('devicemotion');const z=(i>=500&&i<506)?5:0;
+     Object.defineProperties(event,{acceleration:{value:{x:ax,y:0,z}},accelerationIncludingGravity:{value:{x:ax,y:0,z:9.80665+z}},rotationRate:{value:{alpha:0,beta:0,gamma:0}},interval:{value:20}});
+     window.dispatchEvent(event);clock+=20;
+    }
+   };
+  });
+  await page.click('[onclick="requestStartTrip()"]');await page.waitForFunction(()=>tripActive);
+  await page.evaluate(()=>__record());
+  assert.equal(await page.evaluate(()=>motionSamples.length),3001);
+  assert.equal(await page.evaluate(()=>liveBumpsCount),1);
+  const slider=page.locator('#swipe-thumb'), container=page.locator('#swipe-container');
+  await slider.scrollIntoViewIfNeeded();const thumb=await slider.boundingBox(),track=await container.boundingBox();
+  await page.mouse.move(thumb.x+15,thumb.y+20);await page.mouse.down();await page.mouse.move(track.x+track.width-10,thumb.y+20,{steps:14});await page.mouse.up();
+  await page.waitForSelector('#summary-modal.active');
+  const result=await page.evaluate(()=>finishedResult);
+  assert(result && Number.isFinite(result.cost_savings_rub));assert(result.cost_savings_rub>0);
+  assert(result.harsh_accel_count>=1 && result.harsh_brake_count>=1);assert(result.road_anomalies_count>=1);
+  assert.equal(await page.locator('#sum-smooth').innerText(),`${result.smooth_score}/100`);
+  const raw=await(await page.request.get(`${base}/api/v2/trips/${result.session_id}/export`,{headers:identity.headers})).json();
+  assert.equal(raw.point_counts.motion,3001);assert.equal(raw.point_counts.gps,61);
+  const before=await(await page.request.get(`${base}/api/v1/users/${identity.user}/dashboard`,{headers:identity.headers})).json();assert.equal(before.total_trips,1);assert.equal(before.total_savings_rub,result.cost_savings_rub);
+  await page.screenshot({path:output+'/trip-summary.png'});
+  await page.click('[onclick="claimToPiggyBank()"]');await page.waitForFunction(()=>!document.getElementById('summary-modal').classList.contains('active'));
+  const after=await(await page.request.get(`${base}/api/v1/users/${identity.user}/dashboard`,{headers:identity.headers})).json();
+  assert.equal(after.total_trips,1);assert.equal(after.total_savings_rub,result.cost_savings_rub);
+  await page.goto(base+'/history');await page.waitForSelector('.trip-row-card');await page.click('.btn-trip-details');
+  assert.equal(await page.locator('#det-smooth').innerText(),`${result.smooth_score}/100`);
+  assert.deepEqual(errors,[]);console.log('Product epics: credentials, 18 responsive screens, PostgreSQL garage, 50 Hz synthetic capture, moving shock, maneuvers, pointer finish, raw retention, atomic credits, history passed');
+  await context.close();
+  // Real touch input from Chromium's device layer exercises Pointer Events and
+  // setPointerCapture, in addition to the mouse swipe above.
+  const touchContext=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
+  const touchPage=await touchContext.newPage();
+  await touchPage.addInitScript(()=>{
+   Object.defineProperty(navigator,'geolocation',{configurable:true,value:{watchPosition:()=>0,clearWatch:()=>{}}});
+   if(typeof DeviceMotionEvent!=='undefined')Object.defineProperty(DeviceMotionEvent,'requestPermission',{configurable:true,value:()=>Promise.resolve('granted')});
+  });
+  await touchPage.goto(base+'/login');await touchPage.click('.btn-guest');await touchPage.waitForURL(base+'/');
+  await touchPage.click('[onclick="requestStartTrip()"]');await touchPage.waitForFunction(()=>tripActive);
+  const touchThumb=await touchPage.locator('#swipe-thumb').boundingBox(), touchTrack=await touchPage.locator('#swipe-container').boundingBox();
+  const device=await touchContext.newCDPSession(touchPage), y=touchThumb.y+touchThumb.height/2;
+  await device.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:touchThumb.x+15,y,id:1}]});
+  await device.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:touchTrack.x+touchTrack.width-8,y,id:1}]});
+  await device.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
+  await touchPage.waitForSelector('#summary-modal.active');
+  assert.equal(await touchPage.locator('#sum-savings-rub').innerText(),'+0.00 ₽');
+  await touchContext.close();console.log('Touch device swipe: Pointer Events completion passed');
+ }finally{await browser.close();}
+})().catch(error=>{console.error(error);process.exit(1);});
diff --git a/tests/browser/recording.cjs b/tests/browser/recording.cjs
index 88c4d85..9aa0825 100644
--- a/tests/browser/recording.cjs
+++ b/tests/browser/recording.cjs
@@ -21,7 +21,7 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
 
         async function installSensors(permission='granted') {
             await page.evaluate(permission=>{
-                let clock=Date.now(),fix,error;
+                let clock=Date.now(),fix,error,meters=0;
                 window.__sensorStats={permissionCalls:0,watches:0,initialPositionCalls:0,cleared:[]};
                 Date.now=()=>clock;
                 Object.defineProperty(DeviceMotionEvent,'requestPermission',{configurable:true,value:()=>{
@@ -35,7 +35,7 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
                     clearWatch:id=>__sensorStats.cleared.push(id)
                 }});
                 window.__advance=ms=>{clock+=ms;};
-                window.__gps=(accuracy=5,speed=10)=>fix({timestamp:clock,coords:{latitude:55.75,longitude:37.61,accuracy,speed}});
+                window.__gps=(accuracy=5,speed=10)=>{meters+=10;fix({timestamp:clock,coords:{latitude:55.75+meters/111194.9266,longitude:37.61,accuracy,speed}});};
                 window.__gpsError=()=>error({code:1});
                 window.__motion=count=>{
                     for(let i=0;i<count;i++) {
@@ -60,7 +60,7 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
             return response.json();
         }
         async function closeSummary() {
-            await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));
+            await page.evaluate(()=>claimToPiggyBank());
         }
 
         await installSensors();
@@ -90,6 +90,7 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         assert.deepEqual(stats.cleared,[0,1]);
         assert.deepEqual(await page.evaluate(()=>__alerts),[]);
 
+        await closeSummary();
         await page.goto(base+'/');
         await page.waitForFunction(()=>window.AutoXAKSession && !startingTrip);
         await installSensors('denied');
@@ -113,6 +114,7 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         await closeSummary();
 
         // A failed local save must keep the original finished payload for retry.
+        await context.setOffline(true);
         await start();
         await page.evaluate(()=>{
             __gps();__advance(1000);__gps();
@@ -126,6 +128,7 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         await page.evaluate(()=>finishActiveTrip());
         assert.equal(await page.evaluate(()=>!!pendingFinishedPayload),true);
         const retryId=await page.evaluate(()=>pendingFinishedPayload.session_id);
+        await context.setOffline(false);
         // Retry through the visible swipe control, not a direct function call.
         const thumb=await page.locator('#swipe-thumb').boundingBox();
         const track=await page.locator('#swipe-container').boundingBox();
diff --git a/tests/browser/service_worker.cjs b/tests/browser/service_worker.cjs
index 43c4863..58e76be 100644
--- a/tests/browser/service_worker.cjs
+++ b/tests/browser/service_worker.cjs
@@ -7,7 +7,7 @@ const assert = require('node:assert/strict');
 (async()=>{
     const handlers={}, entries=new Map(), buckets=new Map();
     const bucket={match:async key=>entries.get(key)?.clone(),put:async(key,value)=>entries.set(key,value.clone())};
-    buckets.set('autoxak-shell-v4',bucket);
+    buckets.set('autoxak-shell-v6',bucket);
     buckets.set('autoxak-shell-v2',{});
     buckets.set('unrelated-app',{});
     let request;
@@ -54,6 +54,6 @@ const assert = require('node:assert/strict');
     let activation;
     handlers.activate({waitUntil:promise=>{activation=promise;}});
     await activation;
-    assert(buckets.has('autoxak-shell-v4') && buckets.has('unrelated-app') && !buckets.has('autoxak-shell-v2'));
+    assert(buckets.has('autoxak-shell-v6') && buckets.has('unrelated-app') && !buckets.has('autoxak-shell-v2'));
     console.log('Service worker: fresh/cached shell, 503, redirected login, stalled headers/body, query canonicalization, API bypass, cache upgrade passed');
 })().catch(error=>{console.error(error);process.exit(1);});
diff --git a/tests/browser/smoke.cjs b/tests/browser/smoke.cjs
index 0eda527..5660e19 100644
--- a/tests/browser/smoke.cjs
+++ b/tests/browser/smoke.cjs
@@ -19,6 +19,7 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
         await page.waitForFunction(() => window.AutoXAKSession && document.getElementById('select-trim').options.length > 0);
         await page.click('#tab-btn-manual');
         await page.fill('#input-custom-name','Audit Car');
+        await page.fill('#manual-fuel','8.5');
         await page.evaluate(() => saveManualVehicle());
         const response = await page.request.get(`${base}/api/v1/users/${identity.user}/profile`, {headers:identity.headers});
         const profile = await response.json();
@@ -118,7 +119,7 @@ const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
                 return {afterGps,afterTimer,emptyCount,samples:telemetryStream.length,cleared,hasAlert:!!lastAlert};
             } finally {Date.now=originalNow;window.setInterval=originalInterval;}
         });
-        assert.equal(sensors.afterGps,0.001);
+        assert(Math.abs(sensors.afterGps-0.00100075434)<1e-9);
         assert.equal(sensors.afterTimer,sensors.afterGps);
         assert.equal(sensors.emptyCount,0);
         assert.equal(sensors.samples,49);
diff --git a/tests/conftest.py b/tests/conftest.py
index e2e094a..4719768 100644
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -31,7 +31,7 @@ def synthetic_telemetry_batch() -> Dict[str, Any]:
 
     speed_mps = np.clip(15.0 * np.sin(np.pi * time_series / duration_s), 0.0, 15.0)
     accel_long = np.gradient(speed_mps, 1.0 / fs)
-    suspension_noise = 0.2 * np.random.normal(0, 1, n_samples)
+    suspension_noise = 0.2 * np.random.default_rng(812).normal(0, 1, n_samples)
 
     stream: List[Dict[str, float]] = []
     base_lat, base_lon = 55.751244, 37.618423
@@ -58,7 +58,7 @@ def synthetic_telemetry_batch() -> Dict[str, Any]:
     }
 
 
-@pytest_asyncio.fixture(scope="session", autouse=True)
+@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
 async def prepared_database():
     await initialize_schema()
     await engine.dispose()
diff --git a/tests/test_audit_regressions.py b/tests/test_audit_regressions.py
index e40a0a1..791d964 100644
--- a/tests/test_audit_regressions.py
+++ b/tests/test_audit_regressions.py
@@ -218,8 +218,7 @@ async def test_concurrent_retries_and_distinct_sessions_do_not_lose_totals(commi
     assert all(response.status_code == 200 for response in responses)
     dashboard = (await committed_client.get('/api/v1/users/' + committed_client.audit_user_id + '/dashboard')).json()
     assert dashboard['total_trips'] == 2
-    assert dashboard['total_savings_rub'] == 0
-    assert all(response.json()['cost_savings_rub'] is None for response in responses)
+    assert dashboard['total_savings_rub'] == sum(response.json()['cost_savings_rub'] for response in responses)
 
 
 async def test_recording_chunks_completion_ownership_and_streaming_export(committed_client):
@@ -269,8 +268,7 @@ async def test_guest_merge_moves_all_data_once_and_preserves_retry_identity(comm
             async with async_session_factory() as session:
                 target = await _get_or_merge_yandex_user(session, raw_yandex_id, guest_id)
                 assert target.total_trips == 1
-                assert target.total_savings_rub == 0
-                assert original['cost_savings_rub'] is None
+                assert target.total_savings_rub == original['cost_savings_rub']
         response = await client.post('/api/v1/telemetry/session', json=dict(body, user_id=ya_id), headers={'Authorization': 'Bearer ' + create_access_token(ya_id)})
         assert response.status_code == 200, response.text
         assert response.json() == original
diff --git a/tests/test_capture_recording.py b/tests/test_capture_recording.py
index 56789f7..6bd6e2f 100644
--- a/tests/test_capture_recording.py
+++ b/tests/test_capture_recording.py
@@ -25,11 +25,11 @@ async def test_gps_only_capture_is_durable_visible_and_idempotent(committed_clie
     assert first.status_code == duplicate.status_code == 200
     assert first.json() == duplicate.json()
     data = first.json()
-    assert data['status'] == 'RECORDED' and data['distance_km'] == pytest.approx(0.6)
+    assert data['status'] == 'RECORDED' and data['distance_km'] == pytest.approx(0.667169559867)
     assert data['cost_savings_rub'] is data['oil_wear_percent'] is None
     assert data['metrics']['capture_quality']['mode'] == 'gps_only'
     dashboard = (await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
-    assert dashboard['total_trips'] == 1 and dashboard['total_distance_km'] == pytest.approx(0.6)
+    assert dashboard['total_trips'] == 1 and dashboard['total_distance_km'] == pytest.approx(0.67)
     history = (await client.get(f'/api/v1/users/{client.audit_user_id}/analytics/period')).json()['trips']
     assert len(history) == 1 and history[0]['capture_quality']['analysis_status'] == 'unavailable'
     exported = (await client.get(f'/api/v2/trips/{data["session_id"]}/export')).json()
@@ -50,7 +50,7 @@ async def test_motion_only_and_empty_capture_keep_history_without_inventing_read
     result = response.json()
     assert result['status'] == 'RECORDED' and result['duration_seconds'] == 60
     assert result['distance_km'] == 0
-    assert result['cost_savings_rub'] is result['oil_wear_percent'] is None
+    assert result['cost_savings_rub']==0 and result['oil_wear_percent'] is None
     exported = (await client.get(f'/api/v2/trips/{result["session_id"]}/export')).json()
     assert exported['point_counts'] == {'gps':0, 'motion':int(has_motion)}
     if has_motion:
diff --git a/tests/test_dsp_filters.py b/tests/test_dsp_filters.py
index bed70ef..23508a0 100644
--- a/tests/test_dsp_filters.py
+++ b/tests/test_dsp_filters.py
@@ -1,4 +1,4 @@
-﻿import pytest
+import pytest
 import numpy as np
 from app.core.dsp.filters import SignalFilter
 from app.core.dsp.simplifier import PathSimplifier
diff --git a/tests/test_live_preview.py b/tests/test_live_preview.py
index 3c717f2..c92f4ad 100644
--- a/tests/test_live_preview.py
+++ b/tests/test_live_preview.py
@@ -13,8 +13,9 @@ async def test_preview_uses_final_model_without_credits_or_history(committed_cli
     preview = previews[0].json()
     assert preview == previews[1].json()
     assert preview['status'] == 'PREVIEW' and preview['provisional'] is True
-    assert preview['cost_savings_rub'] is None
-    assert preview['math_status']['monetary_credit_status']=='not_verified'
+    assert preview['cost_savings_rub'] >= 0
+    assert preview['math_status']['fuel_savings']['policy_version']=='twin-fuel-mvp-1'
+    assert preview['math_status']['monetary_credit_status']=='model_estimate'
     after = (await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
     assert before == after
     assert (await client.get(f'/api/v1/users/{client.audit_user_id}/trips')).json()['trips'] == []
@@ -60,7 +61,7 @@ async def test_stationary_sensor_shocks_do_not_generate_money(committed_client,
     response = await committed_client.post('/api/v1/telemetry/preview', json=payload)
     assert response.status_code == 200
     assert response.json()['distance_km'] == 0
-    assert response.json()['cost_savings_rub'] is None
+    assert response.json()['cost_savings_rub'] == 0
 
 
 async def test_short_vertical_impacts_survive_driving_filter(committed_client, synthetic_telemetry_batch):
diff --git a/tests/test_logic_audit.py b/tests/test_logic_audit.py
index 2859ee2..4dc684d 100644
--- a/tests/test_logic_audit.py
+++ b/tests/test_logic_audit.py
@@ -70,7 +70,8 @@ async def test_traffic_score_cannot_manufacture_oil_or_money(committed_client, s
     response = await committed_client.post('/api/v1/telemetry/preview', json=body)
     assert response.status_code == 200, response.text
     assert calls == [1]
-    assert response.json()['cost_savings_rub'] is None
+    assert response.json()['math_status']['oil_wear']['status']=='unavailable'
+    assert response.json()['math_status']['fuel_savings']['policy_version']=='twin-fuel-mvp-1'
 
 
 async def test_dsp_runs_outside_request_event_loop(committed_client, synthetic_telemetry_batch, monkeypatch):
@@ -130,7 +131,7 @@ async def test_raw_capture_with_unsupported_frequency_is_still_saved(committed_c
     assert response.status_code == 200, response.text
     assert response.json()['status'] == 'RECORDED'
     assert 'unsupported_motion_rate' in response.json()['metrics']['capture_quality']['quality_flags']
-    assert response.json()['cost_savings_rub'] is None
+    assert response.json()['cost_savings_rub'] == 0.0  # No GPS distance: strict short-trip rule.
     exported = await committed_client.get('/api/v2/trips/'+response.json()['session_id']+'/export')
     assert exported.status_code == 200
     assert exported.json()['point_counts']['motion'] == 50
diff --git a/tests/test_powershell_update.py b/tests/test_powershell_update.py
new file mode 100644
index 0000000..903e4dc
--- /dev/null
+++ b/tests/test_powershell_update.py
@@ -0,0 +1,85 @@
+"""Exercise PowerShell orchestration with a deterministic Docker CLI substitute."""
+import json
+import os
+from pathlib import Path
+import shutil
+import subprocess
+
+import pytest
+
+PWSH = os.environ.get('AUTOXAK_PWSH') or shutil.which('pwsh')
+pytestmark = pytest.mark.skipif(not PWSH, reason='PowerShell is not installed')
+
+
+def invoke(tmp_path, mode='Rebuild', failure=''):
+    root = tmp_path / 'repo'
+    (root / 'deploy').mkdir(parents=True)
+    shutil.copyfile('deploy/update.ps1', root / 'deploy/update.ps1')
+    for directory in ('app', 'static'):
+        (root / directory).mkdir()
+        (root / directory / 'example.txt').write_text('complete file')
+    for name in ('compose.production.yml', '.env.production', 'deploy/entrypoint.py', 'seed_fleet.py', 'seed_fuel_prices.py'):
+        (root / name).write_text('test fixture')
+    command = tmp_path / 'docker'
+    command.write_text('''#!/usr/bin/env python3
+import json, os, sys
+from pathlib import Path
+args=sys.argv[1:]
+with open(os.environ['DOCKER_CALLS'],'a') as out: out.write(json.dumps(args)+'\\n')
+if args[0]=='inspect':
+ print(('true' if os.environ.get('FAILURE')=='readonly' else 'false') if 'ReadonlyRootfs' in args[2] else '' if 'Config.User' in args[2] else 'sha256:previous-image')
+elif args[0]=='compose' and 'ps' in args: print('test-api' if args[-1]=='api' else 'test-postgres')
+elif args[0]=='cp' and args[1].startswith('test-'):
+ target=Path(args[2]);target.parent.mkdir(parents=True,exist_ok=True)
+ if args[1].endswith('/app') or args[1].endswith('/static'): target.mkdir(exist_ok=True)
+ else: target.write_bytes(b'PGDMP-validated-dump')
+elif args[0]=='exec' and 'python' in args and os.environ.get('FAILURE')=='health': sys.exit(1)
+elif args[0]=='compose' and 'build' in args and os.environ.get('FAILURE')=='build': sys.exit(9)
+''')
+    command.chmod(0o755)
+    log = tmp_path / 'calls.jsonl'
+    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'],
+               DOCKER_CALLS=str(log), FAILURE=failure,
+               XDG_CACHE_HOME=str(tmp_path/'cache'), XDG_CONFIG_HOME=str(tmp_path/'config'), XDG_DATA_HOME=str(tmp_path/'data'))
+    reply = subprocess.run([PWSH,'-NoProfile','-File',str(root/'deploy/update.ps1'),'-Mode',mode,'-HealthTimeout','5'],env=env,capture_output=True,text=True,timeout=30)
+    calls = [json.loads(line) for line in log.read_text().splitlines()]
+    return reply, calls, root
+
+
+def test_powershell_rebuild_backs_up_binary_dump_before_restarting(tmp_path):
+    reply,calls,root=invoke(tmp_path)
+    assert reply.returncode==0,reply.stdout+reply.stderr
+    dump=next(root.glob('backups/*/database.dump'))
+    assert dump.read_bytes()==b'PGDMP-validated-dump'
+    backup=next(i for i,c in enumerate(calls) if c[0]=='cp' and c[1].endswith('.dump'))
+    build=next(i for i,c in enumerate(calls) if 'build' in c)
+    up=next(i for i,c in enumerate(calls) if 'up' in c)
+    assert backup<build<up
+    assert not any('down' in c for c in calls)
+
+
+def test_powershell_copy_refuses_read_only_production_before_mutation(tmp_path):
+    reply,calls,root=invoke(tmp_path,'Copy','readonly')
+    assert reply.returncode!=0
+    assert 'read-only' in reply.stderr
+    assert not any(c[0]=='cp' or 'stop' in c or 'build' in c for c in calls)
+
+
+def test_powershell_copy_includes_new_modules_and_restarts(tmp_path):
+    reply,calls,_=invoke(tmp_path,'Copy')
+    assert reply.returncode==0,reply.stdout+reply.stderr
+    copies=[c for c in calls if c[0]=='cp' and c[-1].startswith('test-api:')]
+    assert {c[-1] for c in copies}=={'test-api:/app/'+p for p in ('app','static','deploy/entrypoint.py','seed_fleet.py','seed_fuel_prices.py')}
+    stop=next(i for i,c in enumerate(calls) if 'stop' in c)
+    first_copy=next(i for i,c in enumerate(calls) if c in copies)
+    start=next(i for i,c in enumerate(calls) if 'start' in c)
+    assert stop<first_copy<start
+
+
+def test_powershell_failed_readiness_restores_image_without_restoring_database(tmp_path):
+    reply,calls,root=invoke(tmp_path,failure='health')
+    assert reply.returncode!=0
+    rollback=next(root.glob('backups/*/rollback.json'))
+    assert json.loads(rollback.read_text())['services']['api']['image']=='sha256:previous-image'
+    assert any('--no-build' in c for c in calls)
+    assert not any('pg_restore' in c for c in calls if c[0]=='exec' and len(c)>2 and c[2]!='sh')
diff --git a/tests/test_product_epics.py b/tests/test_product_epics.py
new file mode 100644
index 0000000..8a36fbd
--- /dev/null
+++ b/tests/test_product_epics.py
@@ -0,0 +1,167 @@
+import asyncio
+from copy import deepcopy
+from datetime import datetime, timedelta, timezone
+from types import SimpleNamespace
+import uuid
+
+import numpy as np
+import pytest
+from sqlalchemy import delete, select
+
+from app.core.database import async_session_factory
+from app.core.security import create_access_token
+from app.models.db_models import User, Trip, TripTelemetryRecord, LoginAttempt
+from app.services.progress import summarize_progress
+from app.services.twin_engine import TwinEngine
+
+
+def credentials():
+    # Letters only, including the random portion.
+    return {"login": "Test" + uuid.uuid4().hex[:10].translate(str.maketrans('0123456789','klmnopqrst')),
+            "password": "Abc!12345"}
+
+
+@pytest.mark.parametrize('login,password', [('abcd','Abc!12345'), ('a'*16,'Abc!12345'), ('abc12','Abc!12345'), ('логин','Abc!12345'), ('Abcde','1234567'), ('Abcde','x'*16)])
+async def test_credentials_reject_invalid_inputs(async_client, login, password):
+    for route in ('login','register'):
+        assert (await async_client.post('/api/v1/auth/'+route,json={'login':login,'password':password})).status_code == 422
+
+
+async def test_registration_preserves_guest_and_login_four_attempt_lock(committed_client):
+    client=committed_client
+    data=credentials()
+    request=dict(data,guest_id=client.audit_user_id)
+    response=await client.post('/api/v1/auth/register',json=request)
+    assert response.status_code==200, response.text
+    assert response.json()['user_id']==client.audit_user_id
+    assert response.json()['is_guest'] is False
+    # Same proven guest can recover a lost registration response.
+    assert (await client.post('/api/v1/auth/register',json=request)).json()['user_id']==client.audit_user_id
+    duplicate=await client.post('/api/v1/auth/register',json=data,headers={'Authorization':''})
+    assert duplicate.status_code==409
+    for attempt in range(4):
+        invalid=await client.post('/api/v1/auth/login',json=dict(data,password='abc!12345'),headers={'Authorization':''})
+        assert invalid.status_code==(429 if attempt==3 else 401)
+    locked=await client.post('/api/v1/auth/login',json=data,headers={'Authorization':''})
+    assert locked.status_code==429 and 0<int(locked.headers['Retry-After'])<=300
+    import hashlib
+    key=hashlib.sha256(data['login'].lower().encode()).hexdigest()
+    async with async_session_factory() as db:
+        attempt=await db.get(LoginAttempt,key)
+        attempt.blocked_until=datetime.now(timezone.utc)-timedelta(seconds=1)
+        attempt.window_started_at=datetime.now(timezone.utc)-timedelta(minutes=6)
+        await db.commit()
+    accepted=await client.post('/api/v1/auth/login',json=data,headers={'Authorization':''})
+    assert accepted.status_code==200
+
+
+async def test_login_merges_guest_atomically_once_and_revokes_old_identity(committed_client, synthetic_telemetry_batch):
+    client=committed_client; data=credentials()
+    await client.post('/api/v1/auth/register',json=dict(data,guest_id=client.audit_user_id))
+    guest=(await client.post('/api/v1/auth/guest',json={},headers={'Authorization':''})).json()
+    headers={'Authorization':'Bearer '+guest['token']}
+    try:
+        trip=await client.post('/api/v1/telemetry/session',json=dict(synthetic_telemetry_batch,user_id=guest['user_id']),headers=headers)
+        assert trip.status_code==200
+        request=dict(data,guest_id=guest['user_id'])
+        replies=await asyncio.gather(*[client.post('/api/v1/auth/login',json=request,headers=headers) for _ in range(2)])
+        assert [r.status_code for r in replies]==[200,200]
+        dash=(await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
+        assert dash['total_trips']==1
+        assert dash['total_savings_rub']==trip.json()['cost_savings_rub']
+        assert (await client.get('/api/v1/auth/me',headers=headers)).status_code==401
+        forged=await client.post('/api/v1/auth/login',json=request,headers={'Authorization':''})
+        assert forged.status_code==403
+    finally:
+        async with async_session_factory() as db:
+            await db.execute(delete(User).where(User.id==guest['user_id']));await db.commit()
+
+
+@pytest.mark.parametrize('fraction,expected', [(0,40.62),(.1,27.08),(.3,0),(.9,0)])
+def test_approved_multiplier_policy_exact_examples(fraction,expected):
+    profile=SimpleNamespace(base_city_fuel_rate_l100km=8.5)
+    ax=np.r_[np.full(int(100*fraction),1.81), np.zeros(100-int(100*fraction))]
+    result=TwinEngine(profile=profile,fuel_price_rub=72.4).evaluate_financial_delta(distance_km=10,user_ax=ax)
+    assert result['total_savings_rub']==expected
+    assert result['k_user']==pytest.approx(1+fraction*2.2)
+    assert result['causal_savings_verified'] is False
+    assert result['policy_version']=='twin-fuel-mvp-1'
+    assert TwinEngine().evaluate_financial_delta(distance_km=.01,user_ax=[])['total_savings_rub']==0
+
+
+async def test_full_array_and_concurrent_post_replays_credit_exactly_once(committed_client, synthetic_telemetry_batch):
+    client=committed_client
+    body=dict(deepcopy(synthetic_telemetry_batch),user_id=client.audit_user_id)
+    result=await client.post('/api/v1/telemetry/session',json=body)
+    assert result.status_code==200, result.text
+    receipt=result.json();sid=receipt['session_id']
+    before=(await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
+    assert before['total_savings_rub']==receipt['cost_savings_rub'] and before['total_trips']==1
+    assert len((await client.get(f'/api/v1/users/{client.audit_user_id}/trips')).json()['trips'])==1
+    async with async_session_factory() as db:
+        stored=await db.get(TripTelemetryRecord,sid)
+        assert len(stored.payload)==len(body['telemetry_stream'])
+        assert [p['t'] for p in stored.payload]==[p['t'] for p in body['telemetry_stream']]
+    claims=await asyncio.gather(*[client.post('/api/v1/telemetry/session',json=body) for _ in range(4)])
+    assert all(r.status_code==200 for r in claims)
+    assert all(r.json()==receipt for r in claims)
+    after=(await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
+    assert after['total_trips']==1 and after['total_savings_rub']==receipt['cost_savings_rub']
+    assert (await client.post('/api/v1/telemetry/session',json=body)).json()==receipt
+    assert len((await client.get(f'/api/v1/users/{client.audit_user_id}/trips')).json()['trips'])==1
+    assert (await client.post('/api/v1/telemetry/session',json=body,headers={'Authorization':'Bearer '+create_access_token('missing')})).status_code==401
+
+
+def test_progress_ignores_legacy_scores_and_separates_calendar_periods():
+    now=datetime(2026,11,1,12,tzinfo=timezone.utc)
+    def trip(days,score,policy=True):
+        return SimpleNamespace(created_at=now-timedelta(days=days),calculation_result={
+            'smooth_score':score,'metrics':{'math_status':{'style_score_policy':'smooth-sample-fraction-1' if policy else 'old'}}})
+    result=summarize_progress([trip(0,90),trip(1,80),trip(8,70),trip(9,60),trip(0,100,False)],7,180,now)
+    assert result['average']==85 and result['previous_average']==65 and result['delta']==20
+    assert result['trip_count']==2 and result['sufficient_data']
+    assert len(result['points'])==7 and sum(p['score'] is not None for p in result['points'])==2
+    assert summarize_progress([trip(0,90)],7,0,now)['delta'] is None
+
+
+def test_shock_at_speed_boundary_does_not_create_derivative_event():
+    ax=np.zeros(1000);z=np.full(1000,.6)
+    eligible=np.arange(1000)>500
+    assert TwinEngine.filter_road_anomalies(ax,z,dt=.02,eligible_mask=eligible)[1]==0
+    z[600:610]=5
+    assert TwinEngine.filter_road_anomalies(ax,z,dt=.02,eligible_mask=eligible)[1]==1
+    assert TwinEngine.filter_road_anomalies(ax,z,dt=.02,eligible_mask=np.zeros(1000,bool))[1]==0
+
+
+@pytest.mark.parametrize('distance', [0, .001, .01])
+def test_short_trip_never_earns_money_or_liters(distance):
+    result=TwinEngine().evaluate_financial_delta(distance_km=distance,user_ax=[])
+    assert result['fuel_savings_rub']==result['total_savings_rub']==result['saved_fuel_liters']==0.0
+    assert result['n_total']==1
+
+
+def test_dynamic_stress_threshold_is_strict_and_uses_full_sample_fraction():
+    profile=SimpleNamespace(base_city_fuel_rate_l100km=8.5)
+    result=TwinEngine(profile=profile).evaluate_financial_delta(distance_km=10,user_ax=np.r_[np.full(90,1.8),np.full(10,1.800001)])
+    assert result['n_hard']==10 and result['n_total']==100
+    assert result['price_rub_per_l']==72.4 and result['total_savings_rub']==27.08
+    assert result['saved_fuel_liters']==pytest.approx(.374)
+
+
+@pytest.mark.parametrize('distance,ax', [(float('nan'),[0]),(-1,[0]),(1,[float('inf')]),(1,[[0]])])
+def test_invalid_financial_observations_are_rejected(distance,ax):
+    with pytest.raises(ValueError):
+        TwinEngine().evaluate_financial_delta(distance_km=distance,user_ax=ax)
+
+
+async def test_two_distinct_sessions_add_server_receipts_atomically(committed_client, synthetic_telemetry_batch):
+    client=committed_client
+    bodies=[dict(synthetic_telemetry_batch,user_id=client.audit_user_id,session_id=str(uuid.uuid4())) for _ in range(2)]
+    responses=await asyncio.gather(*[client.post('/api/v1/telemetry/session',json=body) for body in bodies])
+    assert all(response.status_code==200 for response in responses)
+    dashboard=(await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
+    assert dashboard['total_trips']==2
+    assert dashboard['total_savings_rub']==round(sum(response.json()['cost_savings_rub'] for response in responses),2)
+    async with async_session_factory() as db:
+        user=await db.get(User,client.audit_user_id)
+        assert user.total_fuel_saved_liters==pytest.approx(sum(response.json()['fuel_saved_liters'] for response in responses))
diff --git a/tests/test_reference_math.py b/tests/test_reference_math.py
index e5398fb..f625dd4 100644
--- a/tests/test_reference_math.py
+++ b/tests/test_reference_math.py
@@ -345,7 +345,7 @@ async def test_unavailable_oil_and_money_do_not_change_stored_balances(
 ):
     client = committed_client
     before = (await client.get(f"/api/v1/users/{client.audit_user_id}/profile")).json()
-    payload = dict(synthetic_telemetry_batch, user_id=client.audit_user_id)
+    payload = dict(synthetic_telemetry_batch, user_id=client.audit_user_id, car_id="unknown_without_consumption")
     result = await client.post("/api/v1/telemetry/session", json=payload)
     assert result.status_code == 200, result.text
     assert (
@@ -526,7 +526,7 @@ async def test_reference_evidence_is_bound_to_recording_and_saved_atomically(
     assert evaluation["observed_fuel_l"] == pytest.approx(0.01)
     assert evaluation["fuel_comparison"]["delta_cost_rub"] == 0.7
     assert evaluation["fuel_comparison"]["causal_savings_verified"] is False
-    assert data["cost_savings_rub"] is None
+    assert data["cost_savings_rub"] == 0.0  # Coincident GPS fixes cannot earn a fuel credit.
     exported = (
         await committed_client.get("/api/v2/trips/" + data["session_id"] + "/export")
     ).json()
````
