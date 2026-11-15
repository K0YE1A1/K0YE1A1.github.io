// ========================================================
// KOYEL HUB WEB PANEL — JAVASCRIPT FRONTEND
// ========================================================

let authToken = localStorage.getItem("koyel_auth_token") || "";
let wsConnection = null;
let currentChatFolder = null;
let selectedDateFile = null;

// Инициализация при загрузке
document.addEventListener("DOMContentLoaded", () => {
  if (authToken) {
    verifySession();
  } else {
    showLoginModal();
  }
});

// Toast Notifications
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerText = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// 1. АВТОРИЗАЦИЯ
function showLoginModal() {
  document.getElementById("login-modal").classList.remove("hidden");
  document.getElementById("app").classList.add("hidden");
}

function hideLoginModal() {
  document.getElementById("login-modal").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
}

async function handleLogin(e) {
  e.preventDefault();
  const pass = document.getElementById("admin-password").value.trim();
  const errBox = document.getElementById("login-error");
  const btn = document.getElementById("btn-login");

  btn.disabled = true;
  errBox.classList.add("hidden");

  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pass })
    });
    const data = await res.json();

    if (res.ok && data.success) {
      authToken = data.token;
      localStorage.setItem("koyel_auth_token", authToken);
      hideLoginModal();
      initApp();
      showToast("Вход выполнен успешно!", "success");
    } else {
      errBox.innerText = data.detail || "Неверный пароль!";
      errBox.classList.remove("hidden");
    }
  } catch (err) {
    errBox.innerText = "Ошибка соединения с сервером";
    errBox.classList.remove("hidden");
  } finally {
    btn.disabled = false;
  }
}

async function verifySession() {
  try {
    const res = await fetch("/api/auth/status", {
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    if (res.ok) {
      hideLoginModal();
      initApp();
    } else {
      handleLogout();
    }
  } catch (e) {
    handleLogout();
  }
}

function handleLogout() {
  authToken = "";
  localStorage.removeItem("koyel_auth_token");
  if (wsConnection) {
    wsConnection.close();
  }
  showLoginModal();
  showToast("Сессия завершена", "info");
}

// 2. ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ
function initApp() {
  connectWebSocket();
  pollStatus();
  setInterval(pollStatus, 3000);
  loadStats();
  loadHistory();
  loadSettings();
}

// 3. ТАБЫ
function switchTab(tabId) {
  document.querySelectorAll(".nav-tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.tab === tabId);
  });
  document.querySelectorAll(".tab-pane").forEach(pane => {
    pane.classList.toggle("active", pane.id === tabId);
  });

  if (tabId === "tab-stats") loadStats();
  if (tabId === "tab-history") loadHistory();
  if (tabId === "tab-settings") loadSettings();
}

// 4. WEBSOCKET ЛОГИ
function connectWebSocket() {
  if (wsConnection && wsConnection.readyState === WebSocket.OPEN) return;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/logs?token=${encodeURIComponent(authToken)}`;

  wsConnection = new WebSocket(wsUrl);

  wsConnection.onmessage = (event) => {
    const term = document.getElementById("terminal-output");
    const line = document.createElement("div");
    line.className = "term-line";

    const text = event.data;
    if (text.includes("❌") || text.includes("Ошибка") || text.includes("Error")) {
      line.classList.add("error");
    } else if (text.includes("✅") || text.includes("готов к работе") || text.includes("запущен")) {
      line.classList.add("success");
    } else if (text.includes("⚠️") || text.includes("Предупреждение")) {
      line.classList.add("warning");
    } else if (text.includes("🚀") || text.includes("ℹ️")) {
      line.classList.add("info");
    }

    line.innerText = text;
    term.appendChild(line);

    if (document.getElementById("chk-autoscroll").checked) {
      term.scrollTop = term.scrollHeight;
    }
  };

  wsConnection.onclose = () => {
    setTimeout(connectWebSocket, 4000);
  };
}

function clearTerminal() {
  document.getElementById("terminal-output").innerHTML = '<div class="term-line info">[Система]: Терминал очищен.</div>';
}

function copyTerminal() {
  const text = document.getElementById("terminal-output").innerText;
  navigator.clipboard.writeText(text).then(() => {
    showToast("Логи скопированы в буфер обмена!", "success");
  });
}

// 5. ПОЛУЧЕНИЕ СТАТУСА БОТА
async function pollStatus() {
  if (!authToken) return;
  try {
    const res = await fetch("/api/status", {
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    if (!res.ok) return;
    const data = await res.json();

    // Обновление бейджей статуса бота
    const botBadge = document.getElementById("badge-bot-status");
    const txtBot = document.getElementById("txt-bot-status");
    const cardDs = document.getElementById("card-ds-status");

    if (data.is_running) {
      botBadge.className = "status-pill status-online";
      txtBot.innerText = "🟢 Онлайн";
      cardDs.innerText = "🟢 Работает (Онлайн)";
      cardDs.className = "text-success";
    } else {
      botBadge.className = "status-pill status-offline";
      txtBot.innerText = "🔴 Остановлен";
      cardDs.innerText = "🔴 Остановлен";
      cardDs.className = "text-danger";
    }

    // Режим блокировки
    const blockBadge = document.getElementById("badge-block-status");
    const txtBlock = document.getElementById("txt-block-status");
    const cardBlock = document.getElementById("card-block-mode");
    const btnBlock = document.getElementById("btn-toggle-block");

    if (data.is_blocked) {
      blockBadge.className = "status-pill status-blocked";
      txtBlock.innerText = "🛑 Блокирован";
      cardBlock.innerText = "🛑 Заблокирован (/block)";
      cardBlock.className = "text-danger";
      btnBlock.innerText = "🔓 Разблок";
      btnBlock.className = "btn btn-success btn-sm";
    } else {
      blockBadge.className = "status-pill status-active";
      txtBlock.innerText = "🔓 Активен";
      cardBlock.innerText = "🔓 Доступ открыт";
      cardBlock.className = "text-success";
      btnBlock.innerText = "🔒 Блок";
      btnBlock.className = "btn btn-secondary btn-sm";
    }
  } catch (e) {
    console.error("Status poll error:", e);
  }
}

// 6. УПРАВЛЕНИЕ ПРОЦЕССОМ БОТА
async function botAction(action) {
  try {
    const res = await fetch(`/api/bot/${action}`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || `Команда ${action} выполнена`, "success");
      pollStatus();
    } else {
      showToast(data.detail || "Ошибка выполнения команды", "error");
    }
  } catch (e) {
    showToast("Сбой связи с сервером", "error");
  }
}

async function toggleBlock() {
  try {
    const res = await fetch("/api/bot/block/toggle", {
      method: "POST",
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, "success");
      pollStatus();
    } else {
      showToast(data.detail || "Ошибка переключения блокировки", "error");
    }
  } catch (e) {
    showToast("Сбой связи", "error");
  }
}

// 7. K0YE1 CONTROLL ФУНКЦИИ
async function deployAuth() {
  if (!confirm("Вы уверены? Канал #верефикация будет очищен и развернута новая панель аутентификации.")) return;
  showToast("Развертывание верификации в Discord...", "info");
  try {
    const res = await fetch("/api/controll/auth-deploy", {
      method: "POST",
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast("✅ Верификация успешно развернута в канале!", "success");
      appendAuditLog(`[Успех]: Развернута верификация в канале #верефикация`);
    } else {
      showToast(data.detail || data.message || "Ошибка развертывания", "error");
    }
  } catch (e) {
    showToast("Сбой запроса: " + e, "error");
  }
}

async function sendChannelMessage() {
  const cid = document.getElementById("inp-channel-id").value.trim();
  const text = document.getElementById("inp-channel-text").value.trim();
  const isEmbed = document.getElementById("chk-channel-embed").checked;
  const title = document.getElementById("inp-channel-title").value.trim();

  if (!cid || !text) {
    showToast("Укажите ID канала и текст сообщения!", "warning");
    return;
  }

  showToast("Отправка сообщения в Discord...", "info");
  try {
    const res = await fetch("/api/controll/channel-send", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${authToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        channel_id: cid,
        message: text,
        is_embed: isEmbed,
        title: title
      })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast("✅ Сообщение успешно отправлено!", "success");
      appendAuditLog(`[Отправка]: Сообщение отправлено в канал ID ${cid}`);
    } else {
      showToast(data.detail || "Ошибка отправки в канал", "error");
    }
  } catch (e) {
    showToast("Сбой отправки: " + e, "error");
  }
}

async function deployTickets() {
  const cid = document.getElementById("inp-ticket-channel").value.trim();
  const title = document.getElementById("inp-ticket-title").value.trim();
  const desc = document.getElementById("inp-ticket-desc").value.trim();
  const btn = document.getElementById("inp-ticket-btn").value.trim();

  if (!cid || !title) {
    showToast("Заполните параметры тикет-панели!", "warning");
    return;
  }

  showToast("Публикация тикет-панели...", "info");
  try {
    const res = await fetch("/api/controll/tickets-deploy", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${authToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        channel_id: cid,
        title: title,
        description: desc,
        button_text: btn
      })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast("✅ Панель тикетов опубликована!", "success");
      appendAuditLog(`[Тикеты]: Панель опубликована в канал ${cid}`);
    } else {
      showToast(data.detail || "Ошибка публикации тикетов", "error");
    }
  } catch (e) {
    showToast("Сбой публикации: " + e, "error");
  }
}

// Koyel News
function onNewsCatChange() {
  const cat = document.getElementById("sel-news-cat").value;
  const placeSel = document.getElementById("sel-news-place");
  placeSel.innerHTML = "";
  if (cat === "Roblox") {
    placeSel.innerHTML = '<option value="a Desert" selected>a Desert</option>';
  } else {
    placeSel.innerHTML = '<option value="Общее" selected>Общее</option>';
  }
}

async function generateNews() {
  const cat = document.getElementById("sel-news-cat").value;
  const plc = document.getElementById("sel-news-place").value;
  const comment = document.getElementById("inp-news-comment").value.trim();
  const btn = document.getElementById("btn-gen-news");

  btn.disabled = true;
  btn.innerText = "⏳ Генерация Luna AI...";
  showToast("ИИ создает новостной пост...", "info");

  try {
    const res = await fetch("/api/controll/news-generate", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${authToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        category: cat,
        place: plc,
        comment: comment
      })
    });
    const data = await res.json();
    if (res.ok && data.text) {
      document.getElementById("txt-news-preview").value = data.text;
      showToast("✅ Новость успешно сгенерирована!", "success");
      appendAuditLog(`[Новости]: Сгенерирован пост для ${cat} (${plc})`);
    } else {
      showToast(data.detail || "Ошибка генерации новости", "error");
    }
  } catch (e) {
    showToast("Сбой генерации: " + e, "error");
  } finally {
    btn.disabled = false;
    btn.innerText = "🤖 Сгенерировать через ИИ";
  }
}

async function publishNews() {
  const text = document.getElementById("txt-news-preview").value.trim();
  const cid = document.getElementById("inp-news-channel").value.trim();
  const cat = document.getElementById("sel-news-cat").value;
  const plc = document.getElementById("sel-news-place").value;

  if (!text) {
    showToast("Сначала сгенерируйте или введите текст новости!", "warning");
    return;
  }

  const btn = document.getElementById("btn-pub-news");
  btn.disabled = true;
  btn.innerText = "⏳ Публикация...";

  try {
    const res = await fetch("/api/controll/news-publish", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${authToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        channel_id: cid,
        text: text,
        category: cat,
        place: plc
      })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast("📢 Новость опубликована в канал #новости!", "success");
      appendAuditLog(`[Новости]: Опубликован пост в канал ${cid}`);
    } else {
      showToast(data.detail || "Ошибка публикации новости", "error");
    }
  } catch (e) {
    showToast("Сбой публикации: " + e, "error");
  } finally {
    btn.disabled = false;
    btn.innerText = "📢 Опубликовать в #новости";
  }
}

function pasteToInput(inputId) {
  navigator.clipboard.readText().then(text => {
    const el = document.getElementById(inputId);
    el.value = text;
    showToast("Вставлено из буфера обмена", "info");
  }).catch(() => {
    showToast("Разрешите браузеру доступ к буферу обмена!", "warning");
  });
}

function clearInput(inputId) {
  document.getElementById(inputId).value = "";
}

async function testLogSignal() {
  try {
    const res = await fetch("/api/controll/test-signal", {
      method: "POST",
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast("Тестовый сигнал отправлен в #логи-koyel-ai!", "success");
      appendAuditLog(`[Тест]: Тестовый сигнал отправлен в #логи-koyel-ai`);
    } else {
      showToast(data.detail || "Ошибка отправки сигнала", "error");
    }
  } catch (e) {
    showToast("Сбой теста", "error");
  }
}

async function testProfanity() {
  const text = document.getElementById("inp-test-prof").value.trim();
  const resLbl = document.getElementById("lbl-prof-result");
  if (!text) {
    resLbl.innerText = "Введите текст!";
    resLbl.className = "prof-result text-warning";
    return;
  }
  try {
    const res = await fetch("/api/controll/test-profanity", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${authToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ text: text })
    });
    const data = await res.json();
    if (res.ok) {
      resLbl.innerText = `Найдено: ${data.count} | ${data.count > 10 ? 'Превышение лимита!' : 'В норме'}`;
      resLbl.className = `prof-result ${data.count > 10 ? 'text-danger' : 'text-success'}`;
      appendAuditLog(`[Тест матов]: '${text}' -> найдено ${data.count}`);
    }
  } catch (e) {
    resLbl.innerText = "Ошибка проверки";
  }
}

function appendAuditLog(text) {
  const viewer = document.getElementById("audit-log-view");
  const row = document.createElement("div");
  row.className = "log-entry";
  row.innerText = `[${new Date().toLocaleTimeString()}]: ${text}`;
  viewer.prepend(row);
}

// 8. СТАТИСТИКА И ТОП
async function loadStats() {
  if (!authToken) return;
  try {
    const res = await fetch("/api/stats", {
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    if (!res.ok) return;
    const data = await res.json();

    // Карточка токенов
    const t = data.tokens || {};
    document.getElementById("stat-tokens-body").innerHTML = `
      <div class="stat-row"><span>Всего затрачено:</span><strong>${(t.total_tokens || 0).toLocaleString()} токенов</strong></div>
      <div class="stat-row"><span>Токены промптов:</span><span>${(t.prompt_tokens || 0).toLocaleString()}</span></div>
      <div class="stat-row"><span>Токены ответов:</span><span>${(t.completion_tokens || 0).toLocaleString()}</span></div>
      <div class="stat-row"><span>Запросов выполнено:</span><span>${(t.requests || 0).toLocaleString()}</span></div>
    `;

    // Карточка моделей
    const u = data.usage || {};
    document.getElementById("stat-models-body").innerHTML = `
      <div class="stat-row"><span>Всего вызовов:</span><strong>${(u.total_uses || 0).toLocaleString()}</strong></div>
      <div class="stat-row"><span>GPT-6 Luna:</span><span>${(u.luna_uses || 0).toLocaleString()}</span></div>
      <div class="stat-row"><span>GPT Images 1080P:</span><span>${(u.images_uses || 0).toLocaleString()}</span></div>
      <div class="stat-row"><span>Voice AI (Озвучка):</span><strong class="text-highlight">${(u.voice_uses || 0).toLocaleString()}</strong></div>
    `;

    // Лидерборды
    renderLeaderboard("leader-luna-list", data.top_luna || [], "запросов");
    renderLeaderboard("leader-img-list", data.top_img || [], "генераций");
    renderLeaderboard("leader-voice-list", data.top_voice || [], "вызовов");

  } catch (e) {
    console.error("Stats error:", e);
  }
}

function renderLeaderboard(containerId, list, unit) {
  const el = document.getElementById(containerId);
  if (!list || list.length === 0) {
    el.innerHTML = '<div class="empty-hint">Пока нет данных</div>';
    return;
  }
  el.innerHTML = list.map((item, idx) => `
    <div class="leader-item">
      <div><span class="rank">#${idx + 1}</span><strong>${item[0]}</strong></div>
      <span>${item[1]} ${unit}</span>
    </div>
  `).join("");
}

// 9. ИСТОРИЯ ЧАТОВ
async function loadHistory() {
  if (!authToken) return;
  try {
    const res = await fetch("/api/history", {
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    if (!res.ok) return;
    const folders = await res.json();

    const listEl = document.getElementById("chats-list");
    if (!folders || folders.length === 0) {
      listEl.innerHTML = '<div class="empty-hint">История пока пуста</div>';
      return;
    }

    listEl.innerHTML = folders.map(f => `
      <div class="folder-item ${f === currentChatFolder ? 'active' : ''}" onclick="selectChatFolder('${f}')">
        💬 ${f}
      </div>
    `).join("");

  } catch (e) {
    console.error("History load error:", e);
  }
}

async function selectChatFolder(folder) {
  currentChatFolder = folder;
  document.querySelectorAll("#chats-list .folder-item").forEach(item => {
    item.classList.toggle("active", item.innerText.includes(folder));
  });

  try {
    const res = await fetch(`/api/history/dates?folder=${encodeURIComponent(folder)}`, {
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    const files = await res.json();

    const datesEl = document.getElementById("dates-list");
    if (!files || files.length === 0) {
      datesEl.innerHTML = '<div class="empty-hint">Нет файлов дат</div>';
      return;
    }

    datesEl.innerHTML = files.map(file => `
      <div class="folder-item" onclick="loadChatFile('${folder}', '${file}')">
        📅 ${file}
      </div>
    `).join("");

  } catch (e) {
    console.error("Dates load error:", e);
  }
}

async function loadChatFile(folder, file) {
  document.querySelectorAll("#dates-list .folder-item").forEach(item => {
    item.classList.toggle("active", item.innerText.includes(file));
  });

  document.getElementById("history-file-title").innerText = `Чат: ${folder} | Файл: ${file}`;
  const contentEl = document.getElementById("history-content");
  contentEl.innerText = "Загрузка переписки...";

  try {
    const res = await fetch(`/api/history/file?folder=${encodeURIComponent(folder)}&file=${encodeURIComponent(file)}`, {
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    const data = await res.json();
    contentEl.innerText = data.content || "Файл пуст.";
  } catch (e) {
    contentEl.innerText = "Ошибка чтения файла: " + e;
  }
}

function copyHistoryText() {
  const text = document.getElementById("history-content").innerText;
  navigator.clipboard.writeText(text).then(() => {
    showToast("Переписка скопирована!", "success");
  });
}

// 10. НАСТРОЙКИ
async function loadSettings() {
  if (!authToken) return;
  try {
    const res = await fetch("/api/config", {
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    if (!res.ok) return;
    const cfg = await res.json();

    document.getElementById("cfg-openai-key").value = cfg.OPENAI_API_KEY || "";
    document.getElementById("cfg-openai-url").value = cfg.OPENAI_BASE_URL || "https://api.aitunnel.ru/v1";
    document.getElementById("cfg-openai-model").value = cfg.OPENAI_MODEL || "gpt-6-luna";
    document.getElementById("cfg-image-model").value = cfg.IMAGE_MODEL || "gpt-image-2.6-sunburst";
    document.getElementById("cfg-discord-token").value = cfg.DISCORD_BOT_TOKEN || "";
    document.getElementById("cfg-admin-pass").value = cfg.ADMIN_PASSWORD || "05122013";
  } catch (e) {
    console.error("Config load error:", e);
  }
}

async function saveSettings(e) {
  e.preventDefault();
  const payload = {
    OPENAI_API_KEY: document.getElementById("cfg-openai-key").value.trim(),
    OPENAI_BASE_URL: document.getElementById("cfg-openai-url").value.trim(),
    OPENAI_MODEL: document.getElementById("cfg-openai-model").value.trim(),
    IMAGE_MODEL: document.getElementById("cfg-image-model").value.trim(),
    DISCORD_BOT_TOKEN: document.getElementById("cfg-discord-token").value.trim(),
    ADMIN_PASSWORD: document.getElementById("cfg-admin-pass").value.trim(),
  };

  showToast("Сохранение настроек...", "info");
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${authToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast("✅ Настройки успешно сохранены в Api_key.txt!", "success");
    } else {
      showToast(data.detail || "Ошибка сохранения", "error");
    }
  } catch (e) {
    showToast("Сбой сохранения: " + e, "error");
  }
}
