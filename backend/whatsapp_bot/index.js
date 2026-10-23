'use strict';

const path = require('node:path');

const dotenv = require('dotenv');
const qrcode = require('qrcode-terminal');
const { Client, LocalAuth } = require('whatsapp-web.js');

dotenv.config({
  path: path.resolve(__dirname, '..', '..', '.env'),
  override: false,
  quiet: true,
});

function env(name, fallback) {
  const value = process.env[name];
  return value === undefined || value === '' ? fallback : String(value);
}

function envInt(name, fallback, minimum = 1) {
  const parsed = Number.parseInt(env(name, String(fallback)), 10);
  return Number.isFinite(parsed) && parsed >= minimum ? parsed : fallback;
}

function envBool(name, fallback) {
  const value = env(name, fallback ? 'true' : 'false').trim().toLowerCase();
  if (['1', 'true', 'yes', 'si', 'on'].includes(value)) return true;
  if (['0', 'false', 'no', 'off'].includes(value)) return false;
  return fallback;
}

function deriveImageApiUrl(value) {
  try {
    const url = new URL(value);
    if (/\/analizar\/?$/.test(url.pathname)) {
      url.pathname = url.pathname.replace(/\/analizar\/?$/, '/analizar-imagen');
    } else {
      url.pathname = '/api/analizar-imagen';
    }
    url.search = '';
    url.hash = '';
    return url.toString();
  } catch {
    return 'http://127.0.0.1:8000/api/analizar-imagen';
  }
}

function chatKey(value) {
  return String(value || '').replace(/@.*$/, '').replace(/\D/g, '');
}

function parseAllowedChats(value) {
  return new Set(
    String(value || '')
      .split(',')
      .map((item) => chatKey(item.trim()))
      .filter(Boolean),
  );
}

function truncate(text, limit) {
  const value = String(text || '');
  return value.length <= limit ? value : `${value.slice(0, limit)}...`;
}

const API_URL = env(
  'WHATSAPP_ANALYSIS_API_URL',
  env('ANALYSIS_API_URL', 'http://127.0.0.1:8000/api/analizar'),
).trim();
const IMAGE_API_URL = env(
  'WHATSAPP_IMAGE_API_URL',
  deriveImageApiUrl(API_URL),
).trim();
const API_TIMEOUT_MS =
  Math.max(
    1,
    Number.parseFloat(
      env('WHATSAPP_API_TIMEOUT', env('ANALYSIS_API_TIMEOUT', '120')),
    ) || 120,
  ) * 1000;
const MAX_TEXT_CHARS = envInt('WHATSAPP_MAX_TEXT_CHARS', 50_000, 1);
const MAX_MEDIA_BYTES = envInt('WHATSAPP_MAX_MEDIA_MB', 10, 1) * 1024 * 1024;
const SESSION_NAME = env('WHATSAPP_SESSION_NAME', 'fraude-bot').trim();
const PAIRING_PHONE = env('WHATSAPP_PAIRING_PHONE', '')
  .trim()
  .replace(/\D/g, '');
const AUTH_PATH = path.resolve(
  __dirname,
  env('WHATSAPP_AUTH_PATH', '.wwebjs_auth'),
);
const GROUPS_ENABLED = envBool('WHATSAPP_GROUPS_ENABLED', false);
const GROUP_TRIGGER = env('WHATSAPP_GROUP_TRIGGER', '!analizar').trim();
const ALLOWED_CHATS = parseAllowedChats(env('WHATSAPP_ALLOWED_CHATS', ''));
const COOLDOWN_MS = envInt('WHATSAPP_COOLDOWN_SECONDS', 3, 0) * 1000;
const HEADLESS = envBool('WHATSAPP_HEADLESS', true);
const EXECUTABLE_PATH = env('PUPPETEER_EXECUTABLE_PATH', '').trim();
const MESSAGE_LIMIT = 4000;

const RISK_LABELS = {
  high: 'ALTO',
  review: 'REVISAR',
  low: 'BAJO',
};

const recentMessages = new Map();

class ApiError extends Error {
  constructor(status, detail) {
    super(`La API respondió ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

function log(message) {
  console.log(`${new Date().toISOString()} ${message}`);
}

async function readApiResponse(response) {
  const raw = await response.text();
  let payload = null;
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    let detail = raw.slice(0, 300);
    if (payload && payload.detail !== undefined) {
      detail =
        typeof payload.detail === 'string'
          ? payload.detail
          : JSON.stringify(payload.detail);
    }
    throw new ApiError(response.status, detail);
  }

  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('La API devolvió una respuesta no válida.');
  }
  return payload;
}

async function request(url, options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    return await readApiResponse(response);
  } finally {
    clearTimeout(timer);
  }
}

async function analyzeText(text) {
  return request(API_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ texto: text }),
  });
}

async function analyzeImage({ buffer, mimetype, filename }, caption) {
  const form = new FormData();
  form.append(
    'imagen',
    new Blob([new Uint8Array(buffer)], { type: mimetype || 'image/jpeg' }),
    filename || 'imagen.jpg',
  );
  if (caption && caption.trim()) {
    form.append('texto', caption.trim());
  }
  return request(IMAGE_API_URL, { method: 'POST', body: form });
}

function describeError(error) {
  if (error instanceof ApiError) {
    if (error.status === 413) {
      return 'El archivo es demasiado grande para analizarlo.';
    }
    if (error.status === 415) {
      return error.detail || 'El archivo no es compatible.';
    }
    if (error.status === 422) {
      return `No pude analizar el contenido. ${error.detail || ''}`.trim();
    }
    if (error.status === 503) {
      return `El modelo no está disponible en este momento. ${error.detail || ''}`.trim();
    }
    return `El servicio de análisis respondió con el código ${error.status}.`;
  }
  if (error && error.name === 'AbortError') {
    return 'El análisis tardó demasiado. Inténtalo nuevamente.';
  }
  return 'No pude conectar con el servicio de análisis. Verifica que la API esté en ejecución.';
}

function riskLabel(value) {
  return RISK_LABELS[String(value || '').toLowerCase()] || 'NO CONCLUYENTE';
}

function formatResult(result, fromImage = false) {
  const lines = ['*Resultado del análisis*', ''];
  lines.push(`*Veredicto:* ${result.veredicto || 'NO DISPONIBLE'}`);

  if (result.es_fraude === true) {
    lines.push('El mensaje presenta señales de fraude.');
  } else if (result.es_fraude === false) {
    lines.push('No se detectaron señales claras de fraude.');
  } else {
    lines.push('El análisis no es concluyente.');
  }

  if (result.veredicto_mensaje || result.veredicto_url) {
    lines.push(
      '',
      `*Mensaje:* ${result.veredicto_mensaje || 'No evaluado'}`,
      `*Enlaces:* ${result.veredicto_url || 'No evaluados'}`,
    );
  }

  const urls = Array.isArray(result.analisis_urls) ? result.analisis_urls : [];
  if (urls.length) {
    lines.push('', '*Enlaces analizados:*');
    for (const item of urls) {
      if (!item || typeof item !== 'object') continue;
      const url = item.url || item.normalized_url || '(URL sin datos)';
      let detail = `- ${url}: ${riskLabel(item.risk_level)}`;
      if (typeof item.phishing_probability === 'number') {
        detail += ` (probabilidad ${(item.phishing_probability * 100).toFixed(1)}%)`;
      }
      if (item.error) {
        detail += ` - Error: ${item.error}`;
      }
      lines.push(detail);
    }
  }

  if (fromImage) {
    if (Array.isArray(result.qr_detectados) && result.qr_detectados.length) {
      lines.push('', '*Códigos QR detectados:*');
      for (const content of result.qr_detectados) {
        lines.push(`- ${content}`);
      }
    }
    if (result.texto_extraido) {
      lines.push(
        '',
        '*Texto leído de la imagen:*',
        truncate(result.texto_extraido, 1200),
      );
    }
    if (Array.isArray(result.advertencias) && result.advertencias.length) {
      lines.push('', '*Avisos:*');
      for (const warning of result.advertencias) {
        lines.push(`- ${warning}`);
      }
    }
  }

  const raw = String(result.respuesta_cruda || '').trim();
  if (raw) {
    lines.push('', '*Detalle del modelo:*', truncate(raw, 1500));
  }

  lines.push('', 'No abras enlaces sospechosos ni compartas datos personales.');
  return lines.join('\n');
}

function splitMessage(text, limit = MESSAGE_LIMIT) {
  if (text.length <= limit) return [text];

  const parts = [];
  let current = '';
  for (const line of text.split('\n')) {
    let segment = line;
    while (segment.length > limit) {
      if (current) {
        parts.push(current);
        current = '';
      }
      parts.push(segment.slice(0, limit));
      segment = segment.slice(limit);
    }
    const candidate = current ? `${current}\n${segment}` : segment;
    if (candidate.length > limit) {
      if (current) parts.push(current);
      current = segment;
    } else {
      current = candidate;
    }
  }
  if (current) parts.push(current);
  return parts;
}

async function replyLong(message, text) {
  for (const part of splitMessage(text)) {
    await message.reply(part);
  }
}

function startTyping(chat) {
  const tick = () => {
    chat.sendStateTyping().catch(() => {});
  };
  tick();
  return { chat, timer: setInterval(tick, 8000) };
}

async function stopTyping(state) {
  clearInterval(state.timer);
  try {
    await state.chat.clearState();
  } catch {
    // La sesión pudo cerrarse durante el análisis.
  }
}

function isAllowed(message) {
  if (ALLOWED_CHATS.size === 0) return true;
  const candidates = [
    message.from,
    message.author,
    message.id && message.id.remote,
    message.id && message.id.participant,
  ];
  return candidates.some((value) => ALLOWED_CHATS.has(chatKey(value)));
}

function isBotMentioned(message) {
  const botId = chatKey(client.info && client.info.wid && client.info.wid._serialized);
  if (!botId) return false;
  return (message.mentionedIds || []).some((id) => chatKey(id) === botId);
}

function stripMentions(text) {
  return String(text || '')
    .replace(/@\u2068[^\u2069]*\u2069/g, ' ')
    .replace(/@\d+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function isThrottled(key) {
  if (!COOLDOWN_MS) return false;
  const now = Date.now();
  const previous = recentMessages.get(key) || 0;
  if (now - previous < COOLDOWN_MS) return true;
  recentMessages.set(key, now);
  return false;
}

async function downloadMedia(message) {
  const media = await message.downloadMedia();
  if (!media || !media.data) {
    throw new Error('WhatsApp no entregó el archivo adjunto.');
  }
  const buffer = Buffer.from(media.data, 'base64');
  if (buffer.length > MAX_MEDIA_BYTES) {
    const limitMb = Math.round(MAX_MEDIA_BYTES / (1024 * 1024));
    throw new ApiError(413, `El archivo supera los ${limitMb} MB.`);
  }
  return {
    buffer,
    mimetype: media.mimetype || 'application/octet-stream',
    filename: media.filename || undefined,
  };
}

async function analyzeDocument(media, caption) {
  if (media.mimetype.startsWith('image/')) {
    const result = await analyzeImage(media, caption);
    return formatResult(result, true);
  }
  if (
    media.mimetype.startsWith('text/') ||
    ['application/json', 'application/xml', 'application/csv'].includes(
      media.mimetype,
    )
  ) {
    const text = media.buffer.toString('utf8').replace(/\u0000/g, '').trim();
    if (!text) {
      throw new ApiError(422, 'El documento está vacío.');
    }
    const body = `${caption ? `${caption.trim()}\n\n` : ''}${text}`;
    const result = await analyzeText(body.slice(0, MAX_TEXT_CHARS));
    return formatResult(result);
  }
  throw new ApiError(
    415,
    `Por ahora no analizo documentos ${media.mimetype}. Envía texto, una imagen o un archivo de texto.`,
  );
}

async function handleMessage(message) {
  if (message.fromMe || message.isStatus) return;
  if (message.from === 'status@broadcast') return;
  if (['revoked', 'e2e_notification', 'gp2', 'notification_template'].includes(message.type)) {
    return;
  }
  if (!isAllowed(message)) return;

  const chat = await message.getChat();
  let text = String(message.body || '').trim();

  if (chat.isGroup) {
    if (!GROUPS_ENABLED) return;
    const triggered =
      GROUP_TRIGGER &&
      text.toLowerCase().startsWith(GROUP_TRIGGER.toLowerCase());
    if (!triggered && !isBotMentioned(message)) return;
    if (triggered) {
      text = text.slice(GROUP_TRIGGER.length).trim();
    }
    text = stripMentions(text);
  }

  const sender = chatKey(message.author || message.from);
  if (isThrottled(sender)) return;

  const typing = startTyping(chat);
  try {
    if (message.hasMedia) {
      const media = await downloadMedia(message);
      if (message.type === 'image' || message.type === 'sticker') {
        const result = await analyzeImage(media, text);
        await replyLong(message, formatResult(result, true));
      } else if (message.type === 'document') {
        await replyLong(message, await analyzeDocument(media, text));
      } else if (['audio', 'ptt', 'video'].includes(message.type)) {
        await replyLong(
          message,
          'Por ahora no puedo analizar audios ni videos. ' +
            'Reenvía una captura de pantalla o escribe el texto del mensaje.',
        );
      } else {
        await replyLong(
          message,
          'Por ahora solo analizo texto, imágenes y documentos de texto.',
        );
      }
      return;
    }

    if (!text) return;
    if (text.length > MAX_TEXT_CHARS) {
      await replyLong(
        message,
        `El mensaje es demasiado largo. El límite es de ${MAX_TEXT_CHARS} caracteres.`,
      );
      return;
    }

    const result = await analyzeText(text);
    log(`Análisis de texto completado: ${result.veredicto || 'sin veredicto'}`);
    await replyLong(message, formatResult(result));
  } catch (error) {
    console.error(
      `${new Date().toISOString()} Error al analizar un mensaje:`,
      error instanceof ApiError ? `${error.status} ${error.detail}` : error.message,
    );
    await replyLong(message, describeError(error)).catch(() => {});
  } finally {
    await stopTyping(typing);
  }
}

const clientOptions = {
  authStrategy: new LocalAuth({
    clientId: SESSION_NAME,
    dataPath: AUTH_PATH,
  }),
  puppeteer: {
    headless: HEADLESS,
    executablePath: EXECUTABLE_PATH || undefined,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--disable-software-rasterizer',
    ],
  },
};

if (PAIRING_PHONE) {
  clientOptions.pairWithPhoneNumber = {
    phoneNumber: PAIRING_PHONE,
    showNotification: true,
    intervalMs: 180000,
  };
}

const client = new Client(clientOptions);

client.on('qr', (qr) => {
  if (PAIRING_PHONE) return;
  log('Escanea el código QR con WhatsApp > Dispositivos vinculados:');
  qrcode.generate(qr, { small: true });
});

client.on('code', (code) => {
  log('Vinculación sin QR. En WhatsApp abre Dispositivos vinculados >');
  log('Vincular con número de teléfono e ingresa este código:');
  log(`  ${code}`);
});

client.on('authenticated', () => {
  log('Sesión de WhatsApp autenticada.');
});

client.on('auth_failure', (message) => {
  console.error(`Falló la autenticación de WhatsApp: ${message}`);
  process.exitCode = 1;
});

client.on('ready', () => {
  const info = client.info || {};
  log(`Bot de WhatsApp listo como ${info.pushname || 'cuenta vinculada'}.`);
  log(`Vinculación: ${PAIRING_PHONE ? 'código de teléfono' : 'QR'}`);
  log(`API de texto: ${API_URL}`);
  log(`API de imágenes: ${IMAGE_API_URL}`);
});

client.on('disconnected', (reason) => {
  console.error(`WhatsApp se desconectó: ${reason}`);
  process.exit(1);
});

client.on('message', (message) => {
  handleMessage(message).catch((error) => {
    console.error(
      `${new Date().toISOString()} Error no controlado:`,
      error && error.message ? error.message : error,
    );
  });
});

process.on('unhandledRejection', (reason) => {
  console.error(`${new Date().toISOString()} Promesa rechazada:`, reason);
});

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  log(`Cerrando el bot (${signal})...`);
  try {
    await client.destroy();
  } catch {
    // La sesión ya pudo haberse cerrado.
  }
  process.exit(0);
}

process.on('SIGINT', () => {
  shutdown('SIGINT');
});
process.on('SIGTERM', () => {
  shutdown('SIGTERM');
});

log('Iniciando el bot de WhatsApp...');
client.initialize().catch((error) => {
  console.error('No se pudo iniciar WhatsApp:', error);
  process.exit(1);
});
