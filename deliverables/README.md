# Файлы для скачивания и независимого анализа

Снимок реализации: `4325f10419b0a870742c873ef86d369865ce3ba1`.
Ветка `docs/audit-downloads` содержит файлы для передачи; `main` и ветка
`fix/product-epics-audit` при их размещении не изменялись.

- [autoXAK-4325f10.zip](https://github.com/kodeandr/autoXAK/raw/refs/heads/docs/audit-downloads/deliverables/autoXAK-4325f10.zip)
- [autoXAK_CHANGES_FULL_4325f10.md](https://github.com/kodeandr/autoXAK/raw/refs/heads/docs/audit-downloads/deliverables/autoXAK_CHANGES_FULL_4325f10.md)
- [autoXAK_CHANGES_REVIEW_4325f10.md](https://github.com/kodeandr/autoXAK/raw/refs/heads/docs/audit-downloads/deliverables/autoXAK_CHANGES_REVIEW_4325f10.md)

ZIP включает полный исходный проект и оба Markdown-отчёта.
Если прямая ссылка не начинает загрузку, откройте файл в GitHub и выберите **Download raw file**. Для закрытого репозитория нужно войти в свой GitHub-аккаунт.

## SHA-256

```text
967635ef7d4bb9be9343c4a6845fdcc4c2b8dca4f1828c42106c2557e103df67  autoXAK-4325f10.zip
fbd2570a9b762bf16efa93fba8595a39ecb41069851a46fe25b802ca37949384  autoXAK_CHANGES_FULL_4325f10.md
d4696b592d3cddcce6c525b14249dc50f988b3c846009472edea71732fa69155  autoXAK_CHANGES_REVIEW_4325f10.md
```


## Запись демо без автомобиля

- [Скачать архив со скриптом и примером видео](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo.zip).
- [Скачать MP4](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo.mp4).
- [Инструкция запуска](DEMO_DOWNLOAD.md).

Данные и поездка синтетические; расчёты выполнены настоящим backend локальной копии. Скрипт создаёт нового гостя и одну поездку на выбранном адресе.


### Короткое демо: 20 секунд

- [Скачать MP4](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo-20s.mp4).
- Обновлённый архив `autoxak-demo.zip` включает режим `-Short` и готовый пример.
- Полная синтетическая поездка: 20 минут, 13,6358 км, 55,12 ₽ по backend; ролик смонтирован до 20 секунд. Полные 60 001 IMU-отсчёт и 1 201 GPS-точка сохранены и проверены.


### Демо со входом и историей прогресса

[Скачать MP4: вход, поездка и графики за 7/30 дней](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo-progress-20s.mp4). Длительность 20 секунд. 59 предварительных синтетических поездок рассчитаны API; новая поездка становится 60-й. После поездки: среднее за 7 дней 90,4 (+4 пункта), за 30 дней 85,2 (+11,3 пункта). Скрипты и примеры включены в обновлённый `autoxak-demo.zip`; приватные учётные данные не публикуются.


### Версия с полным стартом и завершением

[Скачать MP4, 32 секунды](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo-progress-smooth-32s.mp4). Сохранены вход, итоги, копилка и финальные графики; расширены непрерывные участки старта и завершения поездки. Данные и расчёты те же, повторная запись не выполнялась. В архив добавлен `edit-smooth.cjs` для повторения монтажа.


### Динамичное демо с полноценной историей: 25 секунд

[Скачать актуальное MP4](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo-dynamic-25s.mp4). Вход `demouser`, посимвольный ввод логина/пароля, полный старт/свайп, итоги, копилка, графики 7/30 дней. Ожидания сокращены монтажом. История: 59 поездок по 20 минут (706,66 км), 1 731,22 ₽; новая поездка 55,12 ₽, всего 1 786,34 ₽. Это синтетический прогон через штатный API. Обновлённый архив содержит актуальные скрипты и эти результаты; прежние короткие минутные истории из архива исключены.


### Вариативная поездка и история — актуальный ролик

[Скачать MP4, 25 секунд](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo-varied-25s.mp4). Сохранены все сцены предыдущей версии. Итоги: 3 разгона, 4 торможения, 7 неровностей, индекс 97/100, 13,02 км и 50,72 ₽. На графиках — рассчитанные из синтетической истории подъёмы и снижения. Общая копилка после поездки: 1 826,20 ₽. В обновлённом архиве находятся соответствующие скрипты и отчёты.


### Обзор приложения: история, гараж и движение

[Скачать новый ролик MP4, 25 секунд](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo-app-tour-25s.mp4). В начало добавлены история с прокруткой и выбор автомобиля в гараже; после старта показан растущий счётчик метров. Статичный финальный участок заменён кратким графиком и прокруткой новых поездок. Данные предыдущей вариативной поездки и остальные сцены сохранены. В архиве доступен режим `-Tour`.


### Перераспределённый темп обзора — 25 секунд

[Скачать MP4 с обновлённым темпом](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo-balanced-25s.mp4). История показана около 3 секунд, гараж — 6 секунд, старт — 1,6 секунды. Раннее движение сокращено до 2,5 секунды и выбрано после настоящего ненулевого предпросмотра: +0,38 ₽ на 116 метрах. Остальные сцены сохранены, результат поездки — 13,02 км и +50,72 ₽. Ожидания сокращены монтажом; код приложения и расчёты не менялись. В архив добавлены режим `-Balanced`, соответствующие скрипты и проверенные отчёты.


### Скриншоты на синтетических данных

[Скачать комплект PNG](https://raw.githubusercontent.com/kodeandr/autoXAK/docs/audit-downloads/deliverables/autoxak-demo-screenshots.zip): история с графиком, список поездок, активная поездка и итоговый отчёт. Исходные снимки браузера, 414 × 896 пикселей, без сжатия видео и редактирования показателей. Данные совпадают с последним роликом: 13,02 км, +50,72 ₽, 3 разгона, 4 торможения, 7 неровностей; после поездки — 60 поездок и 1 826,20 ₽ в копилке. [Описание файлов](autoxak-demo-screenshots/README.md).
