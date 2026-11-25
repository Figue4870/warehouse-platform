import {
  busy, createApi, emptyState, escapeHtml, fmt, formData, icon, loadingTable, openDialog, render, renderLogin,
  renderShell, session, setCount, setCurrent, table, toast, TRANSFER_STATE_NAMES,
} from "./ui/ui.js";

const root = document.getElementById("root");
const api = createApi("/api", () => logout(true));
const central = createApi("/central", () => logout(true));

const state = { warehouseId: null, health: null, products: [], view: null };

const MOVEMENT_TYPES = {
  RECEIPT: { label: "Entrada", hint: "Compra o recepción: suma al disponible.", party: "Proveedor" },
  SHIPMENT: { label: "Salida", hint: "Venta o despacho: resta del disponible.", party: "Cliente" },
  RETURN: { label: "Devolución", hint: "Mercancía que regresa: suma al disponible.", party: "Cliente" },
  RESERVATION: { label: "Reserva", hint: "Aparta unidades: pasan de disponible a reservado.", party: null },
  RELEASE: { label: "Liberación", hint: "Devuelve unidades reservadas al disponible.", party: null },
  ADJUSTMENT: { label: "Ajuste", hint: "Corrige un desfase: positivo suma, negativo resta.", party: null },
};

const ALERT_NAMES = { OUT_OF_STOCK: "Agotado", DISCREPANCY: "Desfase reportado", INCONSISTENCY: "Desfase detectado" };
const REQUEST_STATUS = {
  PENDING: ["Pendiente", "pill-warn"], FORWARDED: ["En la central", "pill-info"],
  APPROVED: ["Aprobada", "pill-ok"], REJECTED: ["Rechazada", "pill-bad"],
};
const REPORT_NAMES = { STOCK_VALUATION: "Inventario valorizado", MOVEMENTS_SUMMARY: "Resumen de movimientos", LOW_STOCK: "Poco disponible" };
const TRANSFER_STATUS = { PROCESSING: "pill-info", COMPLETED: "pill-ok", FAILED: "pill-bad", COMPENSATED: "pill-warn" };

const NAV = {
  REGISTRADOR: [
    { id: "movements", label: "Registrar movimiento", icon: "inbound" },
    { id: "inventory", label: "Inventario", icon: "box" },
    { id: "requests", label: "Solicitudes", icon: "clipboard" },
    { id: "alerts", label: "Alertas", icon: "bell" },
  ],
  ADMIN_BODEGA: [
    { id: "inventory", label: "Inventario", icon: "box" },
    { id: "movements", label: "Registrar movimiento", icon: "inbound" },
    { id: "requests", label: "Solicitudes", icon: "clipboard" },
    { id: "alerts", label: "Alertas", icon: "bell" },
    { id: "catalog", label: "Catálogo", icon: "book" },
    { id: "transfers", label: "Transferencias", icon: "truck" },
    { id: "reports", label: "Reportes", icon: "chart" },
  ],
  READ_ONLY: [
    { id: "inventory", label: "Inventario", icon: "box" },
    { id: "alerts", label: "Alertas", icon: "bell" },
    { id: "reports", label: "Reportes", icon: "chart" },
  ],
};

const user = () => session.read()?.user;
const isAdmin = () => user()?.role === "ADMIN_BODEGA";
const canOperate = () => ["ADMIN_BODEGA", "REGISTRADOR"].includes(user()?.role);

// sesión
async function start() {
  try {
    state.health = await api("/health");
    state.warehouseId = state.health.warehouse_id;
  } catch {
    state.health = null;
  }
  document.title = state.warehouseId ? `Bodega ${state.warehouseId}` : "Bodega";
  const current = session.read();
  if (current?.token && current.warehouseId === state.warehouseId) return showApp();
  session.clear();
  renderLogin(root, {
    title: state.warehouseId ? `Bodega ${state.warehouseId}` : "Bodega",
    note: "Usa tu usuario corporativo.",
    async onSubmit({ username, password }) {
      const result = await central("/auth/login", { method: "POST", body: { username, password } });
      const u = result.user;
      if (["ADMIN_BODEGA", "REGISTRADOR"].includes(u.role) && u.warehouse_id !== state.warehouseId) {
        throw new Error(`Tu usuario pertenece a ${u.warehouse_id}; esta es la bodega ${state.warehouseId}`);
      }
      session.write({ token: result.access_token, user: u, warehouseId: state.warehouseId });
      showApp();
    },
  });
}

function logout(expired = false) {
  session.clear();
  if (expired) toast("Tu sesión terminó. Vuelve a iniciar sesión.", "bad");
  start();
}

function navFor(role) {
  return NAV[role] || NAV.READ_ONLY;
}

function showApp() {
  const nav = navFor(user().role);
  const view = renderShell(root, {
    brand: `Bodega ${state.warehouseId}`,
    brandNote: systemLine(),
    nav,
    user: user(),
    onNavigate: (id) => go(id),
    onLogout: () => logout(),
  });
  state.viewEl = view;
  go(nav[0].id);
  refreshCounts();
}

function systemLine() {
  const h = state.health;
  if (!h) return "Sin conexión con la API";
  return `Instancia ${h.instance || "—"} · BD ${h.database} · Redis ${h.redis}`;
}

async function refreshCounts() {
  try {
    const alerts = await api("/alerts?pending=true&limit=200");
    setCount(root, "alerts", alerts.length);
    if (isAdmin()) {
      const pending = await api("/requests?status=PENDING");
      setCount(root, "requests", pending.length);
    }
  } catch {
    /* los contadores son una ayuda: si fallan, la página sigue */
  }
}

const VIEWS = {};

function go(id, params = {}) {
  state.view = id;
  setCurrent(root, id);
  VIEWS[id](state.viewEl, params);
}

async function loadProducts() {
  state.products = await api("/inventory?limit=2000");
  return state.products;
}

function productLabel(p) {
  return `${p.sku} · ${p.name}`;
}

// Registrar movimiento
VIEWS.movements = async (el, { productId } = {}) => {
  if (!canOperate()) return go("inventory");
  const types = Object.entries(MOVEMENT_TYPES).filter(([type]) => type !== "ADJUSTMENT" || isAdmin());
  render(el, `
    <div class="page-head"><div><h1>Registrar entrada o salida</h1>
      <p>Escanea o escribe el SKU. El saldo se actualiza al instante y el historial queda con tu usuario.</p></div></div>
    <div class="grid-2">
      <form class="panel stack" id="movement-form" novalidate>
        <label class="field"><span>Producto</span>
          <input class="input-lg" name="product" list="product-options" placeholder="SKU o nombre" autocomplete="off" required>
          <datalist id="product-options"></datalist>
        </label>
        <div class="field"><span>Tipo de movimiento</span>
          <div class="segmented" role="group" aria-label="Tipo de movimiento">
            ${types.map(([type, t], i) => `<button type="button" data-type="${type}" aria-pressed="${i === 0}">${t.label}</button>`).join("")}
          </div>
          <div class="type-hint" data-hint></div>
        </div>
        <div class="field-row">
          <label class="field"><span>Cantidad</span><input class="input-lg num" name="quantity" type="number" step="1" required></label>
          <label class="field"><span>Lote</span><input class="input-lg" name="lot" list="lot-options" maxlength="100" autocomplete="off"><datalist id="lot-options"></datalist></label>
        </div>
        <div class="field-row">
          <label class="field"><span>Precio unitario</span><input name="unit_price" type="number" min="0" step="0.01"><small data-price-hint></small></label>
          <label class="field" data-party><span data-party-label>Proveedor</span><input name="counterparty" maxlength="200"></label>
        </div>
        <label class="field"><span>Referencia</span><input name="reference" maxlength="200" placeholder="Factura, guía o motivo"></label>
        <div class="cluster"><button class="btn btn-primary btn-lg" type="submit">${icon("check")}Registrar movimiento</button></div>
      </form>
      <section class="panel stack" id="product-panel" aria-live="polite">${emptyState("Elige un producto para ver su saldo, sus lotes y sus últimos movimientos.")}</section>
    </div>`);

  const form = el.querySelector("#movement-form");
  let movementType = types[0][0];
  let selected = null;

  const setType = (type) => {
    movementType = type;
    form.querySelectorAll("[data-type]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.type === type)));
    const info = MOVEMENT_TYPES[type];
    form.querySelector("[data-hint]").textContent = info.hint;
    form.querySelector("[data-party]").hidden = !info.party;
    form.querySelector("[data-party-label]").textContent = info.party || "";
    form.quantity.min = type === "ADJUSTMENT" ? "" : "1";
    updatePriceHint();
  };
  const updatePriceHint = () => {
    const hint = form.querySelector("[data-price-hint]");
    if (!selected) return (hint.textContent = "");
    hint.textContent = movementType === "SHIPMENT" ? `Por defecto: ${fmt.money(selected.retail_price)}` : "";
  };
  form.querySelectorAll("[data-type]").forEach((b) => b.addEventListener("click", () => setType(b.dataset.type)));
  setType(movementType);

  const products = await loadProducts();
  form.querySelector("#product-options").innerHTML = products.map((p) => `<option value="${escapeHtml(productLabel(p))}">`).join("");

  const resolve = (text) => {
    const value = text.trim().toLowerCase();
    return products.find((p) => productLabel(p).toLowerCase() === value || p.sku.toLowerCase() === value) || null;
  };
  const pick = async (product) => {
    selected = product;
    form.product.setAttribute("aria-invalid", String(!product && form.product.value !== ""));
    updatePriceHint();
    if (product) await showProduct(el.querySelector("#product-panel"), product.product_id, form);
  };
  form.product.addEventListener("change", () => pick(resolve(form.product.value)));
  form.product.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      pick(resolve(form.product.value));
      form.quantity.focus();
    }
  });
  if (productId) {
    const product = products.find((p) => p.product_id === productId);
    if (product) {
      form.product.value = productLabel(product);
      await pick(product);
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selected) selected = resolve(form.product.value);
    if (!selected) {
      form.product.setAttribute("aria-invalid", "true");
      return toast("Elige un producto de la lista", "bad");
    }
    const data = formData(form);
    const body = { product_id: selected.product_id, type: movementType, quantity: Number(data.quantity || 0) };
    for (const key of ["lot", "reference", "counterparty"]) if (data[key]) body[key] = data[key];
    if (data.unit_price) body.unit_price = data.unit_price;
    try {
      const result = await busy(form.querySelector('[type="submit"]'), () => api("/inventory/movements", { method: "POST", body }));
      toast(`${MOVEMENT_TYPES[movementType].label} de ${fmt.int(Math.abs(body.quantity))} registrada. Disponible: ${fmt.int(result.quantity_after)}`);
      if (result.alerts_generated.includes("OUT_OF_STOCK")) toast(`${selected.sku} quedó agotado; se avisó a la administración`, "bad");
      form.quantity.value = "";
      form.reference.value = "";
      await showProduct(el.querySelector("#product-panel"), selected.product_id, form, true);
      refreshCounts();
    } catch (err) {
      toast(err.message, "bad");
    }
  });
};

async function showProduct(panel, productId, form, highlight = false) {
  const product = state.products.find((p) => p.product_id === productId);
  panel.innerHTML = `<div class="product-picked"><h2>${escapeHtml(product?.name || "")}</h2><span class="mono subtle">${escapeHtml(product?.sku || "")}</span></div>${loadingTable(["Fecha", "Tipo", "Cantidad"], 3)}`;
  const response = await api(`/inventory/${productId}`, { raw: true });
  const stock = await response.json();
  const cache = response.headers.get("X-Cache");
  const [lots, movements] = await Promise.all([api(`/inventory/${productId}/lots`), api(`/inventory/${productId}/movements?limit=8`)]);
  if (form) form.querySelector("#lot-options").innerHTML = lots.map((l) => `<option value="${escapeHtml(l.lot)}">`).join("");
  const cacheText = { HIT: "Leído de la caché (HIT)", MISS: "Leído de la base de datos (MISS) y guardado en caché", BYPASS: "Caché no disponible: leído de la base de datos" }[cache] || "";
  panel.innerHTML = `
    <div class="product-picked"><h2>${escapeHtml(product?.name || "")}</h2><span class="mono subtle">${escapeHtml(product?.sku || "")}</span></div>
    <div class="stock-figure">
      <div class="kpi"><span class="kpi-label">Disponible</span><span class="kpi-value">${fmt.int(stock.quantity)}</span></div>
      <div class="kpi"><span class="kpi-label">Reservado</span><span class="kpi-value">${fmt.int(stock.reserved_quantity)}</span></div>
    </div>
    <div class="cache-line">${icon("layers", "icon-sm")}<span>${cacheText}</span></div>
    ${lots.length ? `<div><div class="section-title">Lotes</div>${lots.map((l) => `<div class="cluster"><span class="mono">${escapeHtml(l.lot)}</span><span class="num muted">${fmt.int(l.quantity)} u.</span></div>`).join("")}</div>` : ""}
    <div><div class="section-title">Últimos movimientos</div>
    ${table({
      columns: [
        { label: "Fecha", render: (m) => `<span class="nowrap">${fmt.ago(m.created_at)}</span><div class="subtle">${escapeHtml(m.created_by || "")}</div>` },
        { label: "Tipo", render: (m) => escapeHtml(MOVEMENT_TYPES[m.type]?.label || m.type) },
        { label: "Cantidad", num: true, render: (m) => fmt.int(m.quantity) },
        { label: "Saldo", num: true, render: (m) => fmt.int(m.quantity_after) },
      ],
      rows: movements,
      empty: "Sin movimientos todavía",
      rowAttrs: (m) => (highlight && m === movements[0] ? 'class="flash"' : ""),
    })}</div>`;
}

// Inventario
VIEWS.inventory = async (el) => {
  render(el, `
    <div class="page-head"><div><h1>Inventario de ${escapeHtml(state.warehouseId)}</h1><p data-summary>Cargando…</p></div>
      <label class="field" style="min-width:260px"><span class="visually-hidden">Buscar</span><input type="search" placeholder="Buscar por SKU o nombre" data-search></label></div>
    <div data-table>${loadingTable(["SKU", "Producto", "Categoría", "Disponible", "Reservado", "Estado"], 6)}</div>`);
  const products = await loadProducts();
  const draw = (filter = "") => {
    const text = filter.trim().toLowerCase();
    const rows = products.filter((p) => !text || p.sku.toLowerCase().includes(text) || p.name.toLowerCase().includes(text));
    el.querySelector("[data-table]").innerHTML = table({
      columns: [
        { label: "SKU", render: (p) => `<span class="mono">${escapeHtml(p.sku)}</span>` },
        { label: "Producto", key: "name" },
        { label: "Categoría", render: (p) => escapeHtml(p.category || "—") },
        { label: "Disponible", num: true, render: (p) => fmt.int(p.quantity) },
        { label: "Reservado", num: true, render: (p) => fmt.int(p.reserved_quantity) },
        { label: "Estado", render: (p) => (p.quantity === 0 ? '<span class="pill pill-bad">Agotado</span>' : p.quantity <= 5 ? '<span class="pill pill-warn">Bajo</span>' : "") },
      ],
      rows,
      empty: text ? "Ningún producto coincide con la búsqueda" : "La bodega todavía no tiene productos en su catálogo",
      rowAttrs: (p) => (canOperate() ? `data-clickable data-product="${p.product_id}"` : ""),
    });
    el.querySelectorAll("[data-product]").forEach((row) => row.addEventListener("click", () => go("movements", { productId: Number(row.dataset.product) })));
  };
  const units = products.reduce((sum, p) => sum + p.quantity, 0);
  const out = products.filter((p) => p.quantity === 0).length;
  el.querySelector("[data-summary]").textContent = `${fmt.int(products.length)} productos · ${fmt.int(units)} unidades disponibles · ${fmt.int(out)} agotados`;
  draw();
  el.querySelector("[data-search]").addEventListener("input", (event) => draw(event.target.value));
};

// Solicitudes
VIEWS.requests = async (el) => {
  render(el, `
    <div class="page-head"><div><h1>${isAdmin() ? "Solicitudes por revisar" : "Mis solicitudes"}</h1>
      <p>${isAdmin() ? "Los ajustes aprobados se aplican al inventario; los productos nuevos pasan a la administración global." : "Reporta desfases entre el sistema y la estantería, o pide un producto que no está en el catálogo."}</p></div>
      <div class="cluster">${canOperate() ? `<button class="btn" data-new="NEW_PRODUCT">${icon("plus")}Producto nuevo</button>
      <button class="btn btn-primary" data-new="ADJUSTMENT">${icon("alert")}Reportar desfase</button>` : ""}</div></div>
    <div data-table>${loadingTable(["Solicitud", "Detalle", "Estado", "Creada"], 5)}</div>`);
  el.querySelectorAll("[data-new]").forEach((b) => b.addEventListener("click", () => (b.dataset.new === "ADJUSTMENT" ? adjustmentDialog(el) : newProductDialog(el))));
  await drawRequests(el);
};

async function drawRequests(el) {
  const rows = await api("/requests?limit=200");
  el.querySelector("[data-table]").innerHTML = table({
    columns: [
      { label: "Solicitud", render: (r) => `<span class="mono">${escapeHtml(r.code)}</span>` },
      { label: "Detalle", render: requestDetail },
      { label: "Estado", render: (r) => `<span class="pill ${REQUEST_STATUS[r.status][1]}">${REQUEST_STATUS[r.status][0]}</span>${r.decision_note ? `<div class="subtle">${escapeHtml(r.decision_note)}</div>` : ""}` },
      { label: "Creada", render: (r) => `${escapeHtml(r.created_by)}<div class="subtle">${fmt.ago(r.created_at)}</div>` },
      ...(isAdmin() ? [{ label: "", render: (r) => (r.status === "PENDING" ? `<div class="cluster"><button class="btn btn-sm btn-primary" data-approve="${r.id}">Aprobar</button><button class="btn btn-sm btn-ghost" data-reject="${r.id}">Rechazar</button></div>` : "") }] : []),
    ],
    rows,
    empty: "No hay solicitudes",
  });
  el.querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", () => decide(el, b, "approve")));
  el.querySelectorAll("[data-reject]").forEach((b) => b.addEventListener("click", () => decide(el, b, "reject")));
  if (isAdmin()) setCount(root, "requests", rows.filter((r) => r.status === "PENDING").length);
}

function requestDetail(r) {
  const d = r.details;
  if (r.type === "ADJUSTMENT") {
    const sign = d.difference > 0 ? "+" : "";
    return `<strong>Desfase en <span class="mono">${escapeHtml(d.sku)}</span></strong>
      <div class="subtle">Sistema ${fmt.int(d.system_quantity)}, conteo ${fmt.int(d.counted_quantity)} (${sign}${fmt.int(d.difference)}). ${escapeHtml(d.reason)}</div>`;
  }
  return `<strong>Producto nuevo: ${escapeHtml(d.name)}</strong><div class="subtle">${escapeHtml([d.category, d.unit, d.reason].filter(Boolean).join(" · "))}</div>`;
}

async function decide(el, button, action) {
  try {
    await busy(button, () => api(`/requests/${button.dataset[action === "approve" ? "approve" : "reject"]}/${action}`, { method: "POST", body: {} }));
    toast(action === "approve" ? "Solicitud aprobada" : "Solicitud rechazada");
    await drawRequests(el);
  } catch (err) {
    toast(err.message, "bad");
  }
}

async function adjustmentDialog(el) {
  const products = state.products.length ? state.products : await loadProducts();
  openDialog({
    title: "Reportar un desfase",
    submitLabel: "Enviar a la administración",
    body: `
      <label class="field"><span>Producto</span><select name="product_id" required>${products.map((p) => `<option value="${p.product_id}">${escapeHtml(productLabel(p))} — sistema: ${fmt.int(p.quantity)}</option>`).join("")}</select></label>
      <div class="field-row">
        <label class="field"><span>Unidades contadas</span><input name="counted_quantity" type="number" min="0" step="1" required></label>
        <label class="field"><span>Lote</span><input name="lot" maxlength="100"></label>
      </div>
      <label class="field"><span>Qué encontraste</span><textarea name="reason" minlength="3" maxlength="500" required></textarea></label>`,
    async onSubmit(data) {
      await api("/requests", { method: "POST", body: { type: "ADJUSTMENT", product_id: Number(data.product_id), counted_quantity: Number(data.counted_quantity), lot: data.lot, reason: data.reason } });
      toast("Desfase reportado. La administración de la bodega ya recibió la alerta.");
      await drawRequests(el);
      refreshCounts();
    },
  });
}

function newProductDialog(el) {
  openDialog({
    title: "Pedir un producto nuevo",
    submitLabel: "Enviar solicitud",
    body: `
      <label class="field"><span>Nombre</span><input name="name" maxlength="200" required></label>
      <div class="field-row">
        <label class="field"><span>Categoría</span><input name="category" maxlength="100"></label>
        <label class="field"><span>Unidad</span><input name="unit" maxlength="30" value="UNIDAD" required></label>
      </div>
      <label class="field"><span>Proveedor sugerido</span><input name="supplier" maxlength="200"></label>
      <label class="field"><span>Por qué se necesita</span><textarea name="reason" minlength="3" maxlength="500" required></textarea></label>`,
    async onSubmit(data) {
      await api("/requests", { method: "POST", body: { type: "NEW_PRODUCT", ...data } });
      toast("Solicitud enviada");
      await drawRequests(el);
    },
  });
}

// Alertas
VIEWS.alerts = async (el) => {
  render(el, `
    <div class="page-head"><div><h1>Alertas de inventario</h1><p>Agotados y desfases, tanto los que reporta el personal como los que el sistema detecta solo.</p></div>
      <div class="segmented" role="group" aria-label="Filtro"><button type="button" data-filter="pending" aria-pressed="true">Sin revisar</button><button type="button" data-filter="all" aria-pressed="false">Todas</button></div></div>
    <div class="panel" data-list>${loadingTable(["Alerta"], 4)}</div>`);
  let pendingOnly = true;
  const draw = async () => {
    const alerts = await api(`/alerts?limit=200${pendingOnly ? "&pending=true" : ""}`);
    el.querySelector("[data-list]").innerHTML = alerts.length
      ? `<div class="list">${alerts.map((a) => `
        <div class="list-item ${a.acknowledged_at ? "" : "unread"}">
          <span class="${a.type === "OUT_OF_STOCK" ? "status status-warn" : "status status-bad"}"></span>
          <div><div class="list-title">${escapeHtml(ALERT_NAMES[a.type] || a.type)}</div><div class="muted">${escapeHtml(a.message)}</div>
            <div class="subtle">${fmt.ago(a.created_at)}${a.notified_at ? " · notificada" : ""}${a.acknowledged_by ? ` · revisada por ${escapeHtml(a.acknowledged_by)}` : ""}</div></div>
          ${isAdmin() && !a.acknowledged_at ? `<button class="btn btn-sm" data-ack="${a.id}">Marcar revisada</button>` : "<span></span>"}
        </div>`).join("")}</div>`
      : emptyState(pendingOnly ? "No hay alertas sin revisar" : "No hay alertas");
    el.querySelectorAll("[data-ack]").forEach((b) => b.addEventListener("click", async () => {
      await busy(b, () => api(`/alerts/${b.dataset.ack}/ack`, { method: "POST" }));
      await draw();
      refreshCounts();
    }));
  };
  el.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
    pendingOnly = b.dataset.filter === "pending";
    el.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    draw();
  }));
  await draw();
};

// Catálogo (administración de bodega)
VIEWS.catalog = async (el) => {
  render(el, `
    <div class="page-head"><div><h1>Catálogo de la bodega</h1><p>Los productos los define la administración global; aquí eliges cuáles maneja esta bodega, con su lote y sus precios.</p></div>
      <label class="field" style="min-width:260px"><span class="visually-hidden">Buscar</span><input type="search" placeholder="Buscar en el catálogo universal" data-search></label></div>
    <div data-table>${loadingTable(["SKU", "Producto", "Proveedor", "Precio sugerido", ""], 6)}</div>`);
  const draw = async (search = "") => {
    const rows = await api(`/catalog/universal${search ? `?search=${encodeURIComponent(search)}` : ""}`);
    el.querySelector("[data-table]").innerHTML = table({
      columns: [
        { label: "SKU", render: (p) => `<span class="mono">${escapeHtml(p.sku)}</span>` },
        { label: "Producto", render: (p) => `${escapeHtml(p.name)}${p.aliases.length ? `<div class="subtle">${escapeHtml(p.aliases.join(", "))}</div>` : ""}` },
        { label: "Proveedor", render: (p) => escapeHtml(p.supplier || "—") },
        { label: "Precio sugerido", num: true, render: (p) => fmt.money(p.retail_price) },
        { label: "", render: (p) => (p.local_product_id ? '<span class="subtle">En el catálogo</span>' : `<button class="btn btn-sm btn-primary" data-add="${escapeHtml(p.sku)}">Agregar</button>`) },
      ],
      rows,
      empty: "El catálogo universal todavía no llega a esta bodega",
    });
    el.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => addToCatalog(rows.find((p) => p.sku === b.dataset.add), () => draw(search))));
  };
  let timer;
  el.querySelector("[data-search]").addEventListener("input", (event) => {
    clearTimeout(timer);
    timer = setTimeout(() => draw(event.target.value), 250);
  });
  await draw();
};

function addToCatalog(product, done) {
  openDialog({
    title: `Agregar ${product.name}`,
    submitLabel: "Agregar al catálogo",
    body: `
      <label class="field"><span>Lote</span><input name="lot" maxlength="100"></label>
      <div class="field-row">
        <label class="field"><span>Costo del proveedor</span><input name="supplier_cost" type="number" min="0" step="0.01" value="${escapeHtml(product.supplier_cost ?? "")}"></label>
        <label class="field"><span>Precio en tienda</span><input name="retail_price" type="number" min="0" step="0.01" value="${escapeHtml(product.retail_price ?? "")}"></label>
        <label class="field"><span>Precio mayorista</span><input name="wholesale_price" type="number" min="0" step="0.01" value="${escapeHtml(product.wholesale_price ?? "")}"></label>
      </div>`,
    async onSubmit(data) {
      await api("/catalog/local", { method: "POST", body: { sku: product.sku, ...data } });
      toast(`${product.sku} ya está en el catálogo de ${state.warehouseId}`);
      done();
    },
  });
}

// Transferencias (las orquesta la central)
VIEWS.transfers = async (el) => {
  render(el, `
    <div class="page-head"><div><h1>Transferencias con otras bodegas</h1><p>La bodega de origen aparta las unidades, el transporte las mueve y la de destino las recibe. Si un paso falla, se deshace solo.</p></div></div>
    <div class="grid-2">
      <div data-table>${loadingTable(["Transferencia", "Producto", "Ruta", "Estado"], 5)}</div>
      <form class="panel stack" data-form>
        <h2>Nueva transferencia</h2>
        <div class="segmented" role="group" aria-label="Dirección"><button type="button" data-dir="in" aria-pressed="true">Pedir a otra bodega</button><button type="button" data-dir="out" aria-pressed="false">Enviar a otra bodega</button></div>
        <label class="field"><span>Producto</span><select name="sku" required></select></label>
        <label class="field"><span data-other-label>Bodega de origen</span><select name="other" required></select></label>
        <label class="field"><span>Cantidad</span><input name="quantity" type="number" min="1" step="1" required></label>
        <button class="btn btn-primary" type="submit">${icon("truck")}Solicitar transferencia</button>
      </form>
    </div>`);
  const form = el.querySelector("[data-form]");
  let direction = "in";
  form.querySelectorAll("[data-dir]").forEach((b) => b.addEventListener("click", () => {
    direction = b.dataset.dir;
    form.querySelectorAll("[data-dir]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    form.querySelector("[data-other-label]").textContent = direction === "in" ? "Bodega de origen" : "Bodega de destino";
  }));
  const [products, warehouses] = await Promise.all([loadProducts(), central("/warehouses")]);
  form.sku.innerHTML = products.map((p) => `<option value="${escapeHtml(p.sku)}">${escapeHtml(productLabel(p))}</option>`).join("");
  form.other.innerHTML = warehouses.filter((w) => w.warehouse_id !== state.warehouseId)
    .map((w) => `<option value="${escapeHtml(w.warehouse_id)}">${escapeHtml(w.warehouse_id)}${w.name ? ` · ${escapeHtml(w.name)}` : ""}${w.status !== "ONLINE" ? " (sin respuesta)" : ""}</option>`).join("");
  const draw = async () => {
    const transfers = (await central("/transfers?limit=100")).filter((t) => [t.source_warehouse_id, t.destination_warehouse_id].includes(state.warehouseId));
    el.querySelector("[data-table]").innerHTML = table({
      columns: [
        { label: "Transferencia", render: (t) => `<span class="mono">${escapeHtml(t.transfer_id)}</span><div class="subtle">${fmt.ago(t.created_at)}</div>` },
        { label: "Producto", render: (t) => `<span class="mono">${escapeHtml(t.sku)}</span> <span class="num">× ${fmt.int(t.quantity)}</span>` },
        { label: "Ruta", render: (t) => `${escapeHtml(t.source_warehouse_id)} → ${escapeHtml(t.destination_warehouse_id)}` },
        { label: "Estado", render: (t) => `<span class="pill ${TRANSFER_STATUS[t.status] || ""}">${escapeHtml(TRANSFER_STATE_NAMES[t.state] || t.state)}</span><div class="subtle">${escapeHtml(t.note || "")}</div>` },
      ],
      rows: transfers,
      empty: "Esta bodega no tiene transferencias",
    });
  };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formData(form);
    const body = {
      sku: data.sku, quantity: Number(data.quantity),
      source_warehouse_id: direction === "in" ? data.other : state.warehouseId,
      destination_warehouse_id: direction === "in" ? state.warehouseId : data.other,
    };
    try {
      const transfer = await busy(form.querySelector('[type="submit"]'), () => central("/transfers", { method: "POST", body }));
      toast(`${transfer.transfer_id} en curso`);
      form.quantity.value = "";
      await draw();
    } catch (err) {
      toast(err.message, "bad");
    }
  });
  await draw();
};

// Reportes
VIEWS.reports = async (el) => {
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  render(el, `
    <div class="page-head"><div><h1>Compras y ventas de ${escapeHtml(state.warehouseId)}</h1><p>Historial de entradas por compra y salidas por venta, con el precio registrado en cada movimiento.</p></div>
      <form class="cluster" data-range><input type="date" name="from" value="${monthAgo}"><input type="date" name="to" value="${today}"><button class="btn" type="submit">${icon("refresh")}Actualizar</button></form></div>
    <div class="grid-4" data-kpis></div>
    <div class="stack-lg" style="margin-top:var(--space-5)">
      <div data-table>${loadingTable(["Fecha", "Tipo", "Producto", "Cantidad", "Total"], 6)}</div>
      <section class="panel stack"><div class="panel-head"><h2>Reportes para descargar</h2>
        <div class="cluster"><select data-report><option value="STOCK_VALUATION">Inventario valorizado</option><option value="MOVEMENTS_SUMMARY">Resumen de movimientos</option><option value="LOW_STOCK">Productos con poco disponible</option></select>
        <button class="btn btn-primary" data-run>Generar</button></div></div>
        <div data-jobs></div></section>
    </div>`);
  const range = el.querySelector("[data-range]");
  const drawHistory = async () => {
    const { from, to } = formData(range);
    const history = await api(`/inventory/transactions?from=${from}&to=${to}`);
    el.querySelector("[data-kpis]").innerHTML = [
      ["Unidades compradas", fmt.int(history.purchases_units)], ["Valor de compras", fmt.money(history.purchases_total)],
      ["Unidades vendidas", fmt.int(history.sales_units)], ["Valor de ventas", fmt.money(history.sales_total)],
    ].map(([label, value]) => `<div class="panel kpi"><span class="kpi-label">${label}</span><span class="kpi-value" style="font-size:var(--text-2xl)">${value}</span></div>`).join("");
    el.querySelector("[data-table]").innerHTML = table({
      columns: [
        { label: "Fecha", render: (r) => `<span class="nowrap">${fmt.date(r.created_at)}</span>` },
        { label: "Tipo", render: (r) => (r.type === "RECEIPT" ? "Compra" : "Venta") },
        { label: "Producto", render: (r) => `<span class="mono">${escapeHtml(r.sku)}</span> ${escapeHtml(r.product_name)}` },
        { label: "Contraparte", render: (r) => escapeHtml(r.counterparty || "—") },
        { label: "Cantidad", num: true, render: (r) => fmt.int(r.quantity) },
        { label: "Precio", num: true, render: (r) => fmt.money(r.unit_price) },
        { label: "Total", num: true, render: (r) => fmt.money(r.total) },
      ],
      rows: history.rows,
      empty: "Sin compras ni ventas en este rango",
    });
  };
  const drawJobs = async () => {
    const jobs = await api("/reports/jobs?limit=8");
    el.querySelector("[data-jobs]").innerHTML = table({
      columns: [
        { label: "Trabajo", render: (j) => `<span class="mono">${escapeHtml(j.id)}</span>` },
        { label: "Tipo", render: (j) => escapeHtml(REPORT_NAMES[j.report_type] || j.report_type) },
        { label: "Estado", render: (j) => `<span class="pill ${j.status === "COMPLETED" ? "pill-ok" : j.status === "FAILED" ? "pill-bad" : "pill-info"}">${escapeHtml(j.status)}</span>` },
        { label: "", render: (j) => (j.status === "COMPLETED" ? `<a href="/api/reports/jobs/${encodeURIComponent(j.id)}/download?format=csv" data-download="${escapeHtml(j.id)}">CSV</a>` : "") },
      ],
      rows: jobs,
      empty: "Todavía no se ha generado ningún reporte",
    });
    el.querySelectorAll("[data-download]").forEach((a) => a.addEventListener("click", (event) => download(event, a)));
    return jobs;
  };
  range.addEventListener("submit", (event) => {
    event.preventDefault();
    drawHistory();
  });
  el.querySelector("[data-run]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    await busy(button, async () => {
      const accepted = await api("/reports", { method: "POST", body: { type: el.querySelector("[data-report]").value, params: { date_from: monthAgo, date_to: today } } });
      toast("Reporte en preparación; aparecerá en la lista cuando termine");
      for (let i = 0; i < 20; i += 1) {
        const jobs = await drawJobs();
        if (jobs.find((j) => j.id === accepted.job_id && ["COMPLETED", "FAILED"].includes(j.status))) break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    }).catch((err) => toast(err.message, "bad"));
  });
  await Promise.all([drawHistory(), drawJobs()]);
};

async function download(event, link) {
  event.preventDefault();
  const response = await api(link.getAttribute("href").replace("/api", ""), { raw: true });
  if (!response.ok) return toast("No se pudo descargar el reporte", "bad");
  const url = URL.createObjectURL(await response.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: `${link.dataset.download}.csv` });
  a.click();
  URL.revokeObjectURL(url);
}

start();
