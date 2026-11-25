// Utilidades de interfaz compartidas: sesión, llamadas a la API, componentes y formato.

const ICONS = {
  box: '<path d="M21 8 12 3 3 8v8l9 5 9-5V8Z"/><path d="m3 8 9 5 9-5"/><path d="M12 13v8"/>',
  inbound: '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M4 20h16"/>',
  outbound: '<path d="M12 20V9"/><path d="m7 14 5-5 5 5"/><path d="M4 4h16"/>',
  clipboard: '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4h6v3H9z"/><path d="M9 12h6M9 16h4"/>',
  bell: '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2Z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  book: '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3V4Z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>',
  truck: '<path d="M3 6h11v10H3z"/><path d="M14 9h4l3 3v4h-7"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
  chart: '<path d="M4 20V4"/><path d="M4 20h16"/><path d="M8 16v-5M12 16V8M16 16v-3"/>',
  building: '<path d="M4 21V5l8-2v18"/><path d="M12 9h8v12"/><path d="M8 8v.01M8 12v.01M8 16v.01M16 13v.01M16 17v.01"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6"/>',
  logout: '<path d="M15 4h4v16h-4"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h10"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  check: '<path d="m5 12 4.5 4.5L19 7"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/>',
  file: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6M10 17h6"/>',
  alert: '<path d="M12 4 2.8 19.5h18.4L12 4Z"/><path d="M12 10v4M12 17v.01"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
  activity: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  download: '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
  upload: '<path d="M12 20V9"/><path d="m7 14 5-5 5 5"/><path d="M5 4h14"/>',
};

export function icon(name, extraClass = "") {
  return `<svg class="icon ${extraClass}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Sesión guardada en el navegador (solo esta pestaña)
const STORAGE_KEY = "wcp.session";

export const session = {
  read() {
    try {
      return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
    } catch {
      return null;
    }
  },
  write(data) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch {
      /* sin almacenamiento: la sesión dura lo que la página */
    }
  },
  clear() {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* nada que borrar */
    }
  },
};

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

function detailText(detail) {
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join(". ");
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return detail || "Error inesperado";
}

// Cliente HTTP: agrega el token y convierte los errores en mensajes legibles
export function createApi(baseUrl, onUnauthorized) {
  return async function api(path, { method = "GET", body, headers = {}, raw = false } = {}) {
    const current = session.read();
    const finalHeaders = { ...headers };
    if (current?.token) finalHeaders.Authorization = `Bearer ${current.token}`;
    const multipart = body instanceof FormData;
    if (body !== undefined && !multipart) finalHeaders["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(`${baseUrl}${path}`, { method, headers: finalHeaders, body: body === undefined || multipart ? body : JSON.stringify(body) });
    } catch {
      throw new ApiError(0, "No hay conexión con el servidor");
    }
    if (response.status === 401 && onUnauthorized) onUnauthorized();
    if (raw) return response;
    const text = await response.text();
    const data = text ? JSON.parse(text) : null;
    if (!response.ok) throw new ApiError(response.status, detailText(data?.detail));
    return data;
  };
}

// Descarga un archivo protegido por el token (un enlace normal no lleva el encabezado Authorization)
export async function downloadFile(api, path, filename) {
  const response = await api(path, { raw: true });
  if (!response.ok) throw new ApiError(response.status, "No se pudo descargar el archivo");
  const url = URL.createObjectURL(await response.blob());
  const link = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Construcción de vistas
export function render(target, html) {
  target.innerHTML = html;
  return target;
}

export function emptyState(message) {
  return `<div class="empty">${escapeHtml(message)}</div>`;
}

export function skeletonRows(columns, rows = 4) {
  const cell = '<td><span class="skeleton"></span></td>';
  return Array.from({ length: rows }, () => `<tr>${cell.repeat(columns)}</tr>`).join("");
}

export function table({ columns, rows, empty = "Sin datos", rowAttrs = () => "" }) {
  const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${escapeHtml(c.label)}</th>`).join("");
  const body = rows.length
    ? rows.map((row) => `<tr ${rowAttrs(row)}>${columns.map((c) => `<td class="${c.num ? "num" : ""}">${c.render ? c.render(row) : escapeHtml(row[c.key])}</td>`).join("")}</tr>`).join("")
    : `<tr><td colspan="${columns.length}">${emptyState(empty)}</td></tr>`;
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

export function loadingTable(columns, rows = 4) {
  const head = columns.map((label) => `<th>${escapeHtml(label)}</th>`).join("");
  return `<div class="table-wrap" aria-busy="true"><table><thead><tr>${head}</tr></thead><tbody>${skeletonRows(columns.length, rows)}</tbody></table></div>`;
}

// Botón con estado de carga mientras dura la operación
export async function busy(button, task) {
  if (!button) return task();
  button.setAttribute("aria-busy", "true");
  button.disabled = true;
  try {
    return await task();
  } finally {
    button.removeAttribute("aria-busy");
    button.disabled = false;
  }
}

export function toast(message, kind = "ok") {
  let region = document.querySelector(".toasts");
  if (!region) {
    region = document.createElement("div");
    region.className = "toasts";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    document.body.append(region);
  }
  const item = document.createElement("div");
  item.className = `toast ${kind === "bad" ? "toast-bad" : ""}`;
  item.innerHTML = `${icon(kind === "bad" ? "alert" : "check")}<span>${escapeHtml(message)}</span>`;
  region.append(item);
  setTimeout(() => {
    item.classList.add("leaving");
    item.addEventListener("animationend", () => item.remove(), { once: true });
  }, kind === "bad" ? 6000 : 3500);
}

export function formData(form) {
  const data = {};
  for (const [key, value] of new FormData(form).entries()) {
    const text = String(value).trim();
    if (text !== "") data[key] = text;
  }
  return data;
}

export function openDialog({ title, body, submitLabel = "Guardar", onSubmit }) {
  const dialog = document.createElement("dialog");
  dialog.innerHTML = `<form method="dialog" class="dialog-body">
      <h2>${escapeHtml(title)}</h2>
      <div class="stack">${body}</div>
      <p class="notice notice-bad" data-error hidden></p>
      <div class="dialog-actions">
        <button type="button" class="btn btn-ghost" data-cancel>Cancelar</button>
        <button type="submit" class="btn btn-primary">${escapeHtml(submitLabel)}</button>
      </div>
    </form>`;
  document.body.append(dialog);
  const form = dialog.querySelector("form");
  const error = dialog.querySelector("[data-error]");
  dialog.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => dialog.remove());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    try {
      await busy(form.querySelector('[type="submit"]'), () => onSubmit(formData(form), form));
      dialog.close();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });
  dialog.showModal();
  dialog.querySelector("input, select, textarea")?.focus();
  return dialog;
}

// Formato
const intFormat = new Intl.NumberFormat("es-CO");
const moneyFormat = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });

export const fmt = {
  int: (value) => (value === null || value === undefined ? "—" : intFormat.format(Number(value))),
  money: (value) => (value === null || value === undefined || value === "" ? "—" : moneyFormat.format(Number(value))),
  date: (value) => (value ? new Date(value).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" }) : "—"),
  ago(value) {
    if (!value) return "nunca";
    const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
    if (seconds < 60) return `hace ${Math.max(seconds, 0)} s`;
    if (seconds < 3600) return `hace ${Math.round(seconds / 60)} min`;
    if (seconds < 86400) return `hace ${Math.round(seconds / 3600)} h`;
    return fmt.date(value);
  },
};

export const TRANSFER_STATE_NAMES = {
  REQUESTED: "Solicitada",
  RESERVING_SOURCE: "Reservando en origen",
  SOURCE_RESERVED: "Reservada en origen",
  SHIPPING: "Con Servientrega",
  IN_TRANSIT: "Recibiendo en destino",
  DESTINATION_RECEIVED: "Recibida en destino",
  COMPLETED: "Completada",
  FAILED: "Fallida",
  COMPENSATING: "Deshaciendo",
  RESERVATION_RELEASED: "Reserva liberada",
};

export const ROLE_NAMES = {
  ADMIN_GLOBAL: "Administración global",
  LIDER: "Líder",
  ADMIN_BODEGA: "Administración de bodega",
  REGISTRADOR: "Registro de entradas y salidas",
  SERVICE: "Servicio",
};

export function renderShell(root, { brand, brandNote, nav, user, onNavigate, onLogout }) {
  root.innerHTML = `<div class="app">
      <aside class="sidebar">
        <div class="brand"><span class="brand-name">${escapeHtml(brand)}</span><span class="subtle">${escapeHtml(brandNote)}</span></div>
        <nav class="nav" aria-label="Secciones">
          ${nav.map((item) => `<button type="button" data-view="${item.id}">${icon(item.icon)}<span>${escapeHtml(item.label)}</span><span class="count" data-count="${item.id}" hidden></span></button>`).join("")}
        </nav>
        <div class="sidebar-footer">
          <div><div>${escapeHtml(user.name || user.username)}</div>${(user.name || "") === ROLE_NAMES[user.role] ? "" : `<div class="subtle">${escapeHtml(ROLE_NAMES[user.role] || user.role)}</div>`}</div>
          <button type="button" class="btn btn-ghost btn-sm" data-logout>${icon("logout", "icon-sm")}Cerrar sesión</button>
        </div>
      </aside>
      <main class="main" id="view"></main>
    </div>`;
  root.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => onNavigate(button.dataset.view)));
  root.querySelector("[data-logout]").addEventListener("click", onLogout);
  return root.querySelector("#view");
}

export function setCurrent(root, viewId) {
  root.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-current", b.dataset.view === viewId ? "page" : "false"));
}

export function setCount(root, viewId, count) {
  const badge = root.querySelector(`[data-count="${viewId}"]`);
  if (!badge) return;
  badge.hidden = !count;
  badge.textContent = count > 99 ? "99+" : String(count);
}

export function renderLogin(root, { title, note, onSubmit }) {
  root.innerHTML = `<div class="login"><div class="login-card">
      <div><h1>${escapeHtml(title)}</h1><p class="muted">${escapeHtml(note)}</p></div>
      <form class="panel" novalidate>
        <label class="field"><span>Usuario</span><input name="username" autocomplete="username" required></label>
        <label class="field"><span>Contraseña</span><input name="password" type="password" autocomplete="current-password" required></label>
        <p class="notice notice-bad" data-error hidden></p>
        <button class="btn btn-primary btn-lg" type="submit">Iniciar sesión</button>
      </form>
    </div></div>`;
  const form = root.querySelector("form");
  const error = root.querySelector("[data-error]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    const data = formData(form);
    if (!data.username || !data.password) {
      error.textContent = "Escribe tu usuario y tu contraseña";
      error.hidden = false;
      return;
    }
    try {
      await busy(form.querySelector("button"), () => onSubmit(data));
    } catch (err) {
      error.textContent = err.status === 401 ? "Usuario o contraseña incorrectos" : err.message;
      error.hidden = false;
    }
  });
  form.querySelector("input").focus();
}
