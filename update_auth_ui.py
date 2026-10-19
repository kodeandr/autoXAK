import re

path = "static/index.html"
with open(path, "r", encoding="utf-8") as f:
    html = f.read()

NEW_AUTH_OVERLAY = """<!-- Оверлей авторизации: Яндекс ID + Гостевой режим (без ПДн) -->
    <div id="auth-flow-overlay" class="auth-overlay" style="display: none; position: fixed; inset: 0; z-index: 9999; background: rgba(5, 7, 12, 0.94); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); align-items: center; justify-content: center; padding: 20px;">
        <div class="auth-card" style="width: 100%; max-width: 380px; background: #111622; border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 24px; padding: 28px 22px; box-shadow: 0 24px 60px rgba(0, 0, 0, 0.75); text-align: center; position: relative;">
            <button type="button" onclick="AuthFlow.close()" aria-label="Закрыть" style="position: absolute; top: 14px; right: 14px; width: 32px; height: 32px; border-radius: 50%; border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.6); cursor: pointer; font-size: 16px; display: flex; align-items: center; justify-content: center;">✕</button>

            <div style="width: 56px; height: 56px; border-radius: 18px; background: rgba(163, 230, 53, 0.12); border: 1px solid rgba(163, 230, 53, 0.35); display: inline-flex; align-items: center; justify-content: center; font-size: 26px; margin-bottom: 14px;">
                🚗
            </div>

            <h2 style="margin: 0 0 6px; font-size: 22px; font-weight: 800; color: #FFFFFF; letter-spacing: -0.02em;">autoXAK</h2>
            <p id="auth-subtitle-text" style="margin: 0 0 22px; font-size: 13px; color: rgba(255, 255, 255, 0.62); line-height: 1.45;">
                Синхронизация копилки сбережений и гаража через обезличенный токен
            </p>

            <!-- Блок для входа (Гость или неавторизованный) -->
            <div id="auth-guest-actions">
                <button type="button" onclick="AuthFlow.loginWithYandex()" style="width: 100%; padding: 14px 16px; background: #000000; border: 1px solid rgba(255, 255, 255, 0.22); border-radius: 14px; color: #FFFFFF; font-weight: 700; font-size: 15px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 12px; transition: transform 0.15s ease;">
                    <span style="display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; background: #FC3F1D; color: #FFFFFF; border-radius: 50%; font-weight: 900; font-size: 14px; font-family: sans-serif;">Я</span>
                    <span>Войти с Яндекс ID</span>
                </button>

                <button type="button" onclick="AuthFlow.continueAsGuest()" style="width: 100%; padding: 14px 16px; background: rgba(163, 230, 53, 0.14); border: 1px solid rgba(163, 230, 53, 0.4); border-radius: 14px; color: #A3E635; font-weight: 700; font-size: 14px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px;">
                    <span>⚡ Продолжить как гость</span>
                </button>
            </div>

            <!-- Блок для уже авторизованного через Яндекс ID пользователя -->
            <div id="auth-yandex-profile" style="display: none;">
                <div style="background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 14px; padding: 14px; margin-bottom: 14px; text-align: left;">
                    <div style="font-size: 11px; color: rgba(255,255,255,0.45); text-transform: uppercase; margin-bottom: 4px;">Статус сессии</div>
                    <div style="font-size: 14px; font-weight: 700; color: #A3E635; margin-bottom: 4px;">✓ Яндекс ID подключен</div>
                    <div id="auth-profile-id-label" style="font-size: 12px; color: rgba(255,255,255,0.55); font-family: monospace;">ID: ya_...</div>
                </div>
                <button type="button" onclick="AuthFlow.logout()" style="width: 100%; padding: 12px 16px; background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 14px; color: #F87171; font-weight: 700; font-size: 13px; cursor: pointer;">
                    Выйти в гостевой режим
                </button>
            </div>

            <div style="margin-top: 16px; font-size: 11px; color: rgba(255, 255, 255, 0.42); line-height: 1.4;">
                Авторизация выполняется по обезличенному хеш-ключу. ФИО, почта и номер телефона не запрашиваются и не сохраняются (вне контура ПДн 152-ФЗ).
            </div>
        </div>
    </div>"""

NEW_AUTH_SCRIPT = """<script id="autoxak-yandex-auth-controller">
(function() {
    function updateHeaderBadge(userId, isGuest) {
        const badgeText = document.getElementById('user-badge-text') || document.getElementById('auth-status-label');
        if (badgeText) {
            badgeText.textContent = isGuest ? 'Гость' : 'Яндекс ID';
        }
        if (typeof window.updateUserBadge === 'function') {
            try {
                window.updateUserBadge({
                    user_id: userId,
                    name: isGuest ? 'Гость' : 'Яндекс ID',
                    is_guest: isGuest
                });
            } catch (_) {}
        }
    }

    const YandexAuthController = {
        open() {
            const overlay = document.getElementById('auth-flow-overlay');
            if (!overlay) return;
            const userId = localStorage.getItem('autoXAK_user_id') || '';
            const isGuest = localStorage.getItem('autoXAK_is_guest') !== 'false';

            const guestActions = document.getElementById('auth-guest-actions');
            const yandexProfile = document.getElementById('auth-yandex-profile');
            const idLabel = document.getElementById('auth-profile-id-label');

            if (!isGuest && userId.startsWith('ya_')) {
                if (guestActions) guestActions.style.display = 'none';
                if (yandexProfile) yandexProfile.style.display = 'block';
                if (idLabel) idLabel.textContent = 'Токен: ' + userId;
            } else {
                if (guestActions) guestActions.style.display = 'block';
                if (yandexProfile) yandexProfile.style.display = 'none';
            }
            overlay.style.display = 'flex';
        },

        close() {
            const overlay = document.getElementById('auth-flow-overlay');
            if (overlay) overlay.style.display = 'none';
        },

        showScreen() {
            this.open();
        },

        loginWithYandex() {
            const existingId = localStorage.getItem('autoXAK_user_id') || '';
            window.location.href = '/api/v1/auth/yandex/login?guest_id=' + encodeURIComponent(existingId);
        },

        async continueAsGuest() {
            const existingId = localStorage.getItem('autoXAK_user_id');
            if (existingId) {
                window.currentUserId = existingId;
                this.close();
                updateHeaderBadge(existingId, localStorage.getItem('autoXAK_is_guest') !== 'false');
                if (typeof window.fetchDashboard === 'function') await window.fetchDashboard();
                return;
            }
            try {
                const res = await fetch('/api/v1/auth/guest', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                if (!res.ok) throw new Error('Не удалось создать гостевую сессию');
                const data = await res.json();
                localStorage.setItem('autoXAK_user_id', data.user_id);
                localStorage.setItem('autoXAK_jwt_token', data.token);
                localStorage.setItem('autoXAK_is_guest', 'true');
                localStorage.setItem('autoXAK_user_name', 'Гость');
                window.currentUserId = data.user_id;
                if (typeof window.applySession === 'function') {
                    try { window.applySession(data); } catch (_) {}
                }
                updateHeaderBadge(data.user_id, true);
                this.close();
                if (typeof window.fetchDashboard === 'function') await window.fetchDashboard();
            } catch (e) {
                console.error('[AuthFlow] Ошибка создания гостевой сессии:', e);
                this.close();
            }
        },

        async logout() {
            localStorage.removeItem('autoXAK_user_id');
            localStorage.removeItem('autoXAK_jwt_token');
            localStorage.removeItem('autoXAK_is_guest');
            localStorage.removeItem('autoXAK_user_name');
            localStorage.removeItem('autoXAK_auth_provider');
            await this.continueAsGuest();
        },

        startGuestSession() {
            return this.continueAsGuest();
        }
    };

    window.AuthFlow = YandexAuthController;
    window.AuthManager = YandexAuthController;

    window.addEventListener('DOMContentLoaded', () => {
        const params = new URLSearchParams(window.location.search);
        if (params.has('auth_error')) {
            console.warn('[Яндекс ID] Отмена или ошибка входа:', params.get('auth_error'));
            window.history.replaceState({}, document.title, '/');
        }
        const uid = localStorage.getItem('autoXAK_user_id');
        const isGuest = localStorage.getItem('autoXAK_is_guest') !== 'false';
        if (uid) {
            updateHeaderBadge(uid, isGuest);
        }
    });
})();
</script>"""

# 1. Заменяем старый HTML-блок #auth-flow-overlay или #auth-modal, либо вставляем после <body>
overlay_replaced = False
for target_id in ("auth-flow-overlay", "auth-modal"):
    marker = f'id="{target_id}"'
    pos = html.find(marker)
    if pos != -1:
        div_start = html.rfind("<div", 0, pos)
        if div_start != -1:
            # Находим парный закрывающий </div> для контейнера модального окна
            idx = div_start
            depth = 0
            while idx < len(html):
                next_open = html.find("<div", idx)
                next_close = html.find("</div>", idx)
                if next_close == -1:
                    break
                if next_open != -1 and next_open < next_close:
                    depth += 1
                    idx = next_open + 4
                else:
                    depth -= 1
                    idx = next_close + 6
                    if depth == 0:
                        html = html[:div_start] + NEW_AUTH_OVERLAY + html[idx:]
                        overlay_replaced = True
                        break
        if overlay_replaced:
            break

if not overlay_replaced:
    body_pos = html.find("<body")
    if body_pos != -1:
        body_end = html.find(">", body_pos) + 1
        html = html[:body_end] + "\n" + NEW_AUTH_OVERLAY + "\n" + html[body_end:]

# 2. Внедряем или обновляем скрипт контроллера перед </body>
html = re.sub(
    r'<script id="autoxak-yandex-auth-controller">[\s\S]*?</script>',
    "",
    html,
)
html = html.replace("</body>", NEW_AUTH_SCRIPT + "\n</body>")

with open(path, "w", encoding="utf-8") as f:
    f.write(html)

print("УСПЕХ: static/index.html обновлен под Яндекс ID без обычной регистрации!")