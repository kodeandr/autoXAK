# Тестовая среда на текущем сервере HOSTKEY

Адрес: `https://staging.autoxak.ru`. Отдельный checkout `/opt/autoxak-staging`,
Compose project `autoxak-staging`, порт `127.0.0.1:18000`, PostgreSQL с томом
`autoxak-staging_pgdata`, собственные Redis, журналы, ключ JWT и пароль БД.
Production продолжает использовать свой каталог, порт 8000 и прежние тома.
Файлы production и его данные не копируются в тестовую среду.

Приложение и дизайн те же. Данные и сессии разделены адресом и базой.
Разрешения геопозиции/движения для staging браузер запросит отдельно;
разрешения боевого домена на другой адрес не распространяются.
Внешний 2GIS отключён в пользу нейтрального fallback. Гостевой вход работает;
для Яндекса нужна отдельная OAuth-регистрация с callback
`https://staging.autoxak.ru/api/v1/auth/yandex/callback` и тестовые ключи
в `.env.staging`. Примерные цены из seed — тестовые, не реальные измерения.

## Первый запуск

1. Создайте DNS A-запись `staging.autoxak.ru` → `185.149.145.234`. IPv6 AAAA
   добавляйте только при настроенном IPv6 на сервере.
2. В SSH от root выполните:

```bash
git clone --branch feat/production-deployment https://github.com/kodeandr/autoXAK.git /opt/autoxak-staging
cd /opt/autoxak-staging
bash deploy/staging.sh init staging.autoxak.ru
bash deploy/staging.sh start
bash deploy/staging.sh seed
bash deploy/staging.sh check
```

Проверьте отсутствие конфликта сети `172.29.251.0/24` с другими Docker/VPN
сетями. При конфликте задайте согласованные `PROXY_SUBNET`, `PROXY_GATEWAY`,
`PROXY_API_IP` и `PROXY_TRUSTED_IPS` в `.env.staging` до запуска. Основной
порт 18000 всегда привязан только к localhost. Имена проекта и тома фиксированы.
Не подставляйте `.env.production` и не меняйте имя тома на production.

3. Подготовьте отдельный виртуальный хост Nginx для HTTP challenge:

```bash
install -d -m 755 /var/www/autoxak-staging-acme
install -m 644 .staging/nginx-http.conf /etc/nginx/sites-available/autoxak-staging
ln -s /etc/nginx/sites-available/autoxak-staging /etc/nginx/sites-enabled/autoxak-staging
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/autoxak-staging-acme -d staging.autoxak.ru
```

Это команды первого подключения, существующий сайт с таким именем не заменяйте
без проверки. Переходите к следующему шагу после успешного выпуска сертификата.
Certbot использует уже настроенный на сервере аккаунт либо запросит email.

4. Подключите HTTPS и вход тестировщика:

```bash
install -o root -g www-data -m 640 .staging/htpasswd /etc/nginx/autoxak-staging.htpasswd
install -m 600 .staging/nginx.conf /etc/nginx/sites-available/autoxak-staging
nginx -t && systemctl reload nginx
```

На этом Ubuntu Nginx обычно использует `www-data`; если в `nginx.conf`
указан другой пользователь, дайте ему доступ к htpasswd вместо этой группы.
Содержимое `.staging/nginx.conf` приватное: там случайный токен gate-cookie.
При ошибке `nginx -t` не выполняйте reload: верните HTTP-кандидат и разберите
ошибку. Другие виртуальные хосты и действующие сертификаты не заменяются.

Откройте staging с телефона. Логин `tester`, случайный пароль находится
в `.staging/access.txt` (посмотрите через `cat` в своей SSH-сессии; не
публикуйте файл). После входа gate-cookie на сутки разрешает доступ,
а Bearer-авторизация приложения продолжает работать независимо.
Cookie Secure/HttpOnly/SameSite=Strict, host-only. `/_staging/access`
выдаёт cookie только после успешного Basic Auth
и ответа upstream 200; 401 не выдаёт cookie. Пароль и токен сохраняйте
при обновлениях. Изменение только пароля не отзывает уже выданные cookie:
для отзыва нужен новый gate-token в map и Set-Cookie с проверкой/reload Nginx.
Заголовок noindex запрещает индексацию, но не заменяет пароль.

## Как проверять изменения

В тестовом checkout выбирайте нужную ветку/коммит и запускайте
`bash deploy/staging.sh start`: он собирает образ текущих файлов. Для
получения новых коммитов текущей отслеживаемой ветки с резервной копией БД:

```bash
cd /opt/autoxak-staging
bash deploy/staging.sh update
```

Проверьте на телефоне вход, гараж, разрешения, импульсы датчиков, короткую
поездку, предварительную экономию, сохранение и историю. Затем проверьте
офлайн-сохранение и последующую синхронизацию. Для проверки движения
используйте закреплённый смартфон. Не очищайте данные браузера с неотправленными
поездками. После приёмки переносите проверенный коммит в production отдельно;
обновление staging само production не публикует.

Другие действия: `status`, `logs`, `backup`, `stop`. `stop` сохраняет тестовые
данные и не останавливает production. `seed` нужен при первом запуске,
автоматически при каждом обновлении он не выполняется.

## Ресурсы и ограничения

На VPS 2 ГБ это годится для ручных проверок при малой нагрузке. API staging
ограничен 768 MiB/1 CPU, PostgreSQL 256 MiB, Redis 128 MiB (данные 64 MiB).
Это потолки потребления, не заранее занятая память. Проверяйте `free -h`,
`docker stats --no-stream`, OOM и задержки обоих экземпляров после запуска.
Большая запись или нагрузочный тест могут упереться в эти пределы. Нагрузочные
испытания, проверку нехватки RAM и сетевые эксперименты проводите на отдельном
сервере: ядро, диск и физическая сеть текущего VPS общие для обеих сред.
Не меняйте offload/MTU/firewall на общем хосте ради staging.

Стабильность production и работа staging должны быть проверены после
фактического подключения DNS/TLS. Конфигурация сама не создаёт DNS-запись
и не предоставляет облачному помощнику SSH-доступ.
