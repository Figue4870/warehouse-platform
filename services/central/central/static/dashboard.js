import {
  busy, createApi, emptyState, escapeHtml, fmt, formData, icon, loadingTable, openDialog, render, renderLogin,
  renderShell, ROLE_NAMES, session, setCount, setCurrent, table, toast, TRANSFER_STATE_NAMES,
} from "/ui/ui.js";

const root = document.getElementById("root");
const api = createApi("", () => logout(true));
const state = { viewEl: null };

const NAV = {
  ADMIN_GLOBAL: [
    { id: "warehouses", label: "Bodegas", icon: "building" },
    { id: "overview", label: "Tablero", icon: "chart" },
    { id: "catalog", label: "Catálogo universal", icon: "book" },
    { id: "requests", label: "Solicitudes", icon: "clipboard" },
    { id: "users", label: "Usuarios", icon: "users" },
    { id: "notifications", label: "Notificaciones", icon: "bell" },
    { id: "transfers", label: "Transferencias", icon: "truck" },
    { id: "inventory", label: "Inventario global", icon: "box" },
    { id: "athena", label: "Reportes con Athena", icon: "layers" },
  ],
  LIDER: [
    { id: "overview", label: "Tablero", icon: "chart" },
    { id: "inventory", label: "Inventario global", icon: "box" },
    { id: "athena", label: "Reportes con Athena", icon: "layers" },
    { id: "transfers", label: "Transferencias", icon: "truck" },
    { id: "notifications", label: "Notificaciones", icon: "bell" },
  ],
  OTHER: [
    { id: "inventory", label: "Inventario global", icon: "box" },
    { id: "transfers", label: "Transferencias", icon: "truck" },
  ],
};

const STATUS = { ONLINE: ["En línea", "status-ok"], OFFLINE: ["Sin respuesta", "status-bad"], UNKNOWN: ["Sin contacto aún", "status-idle"] };
const TRANSFER_PILL = { PROCESSING: "pill-info", COMPLETED: "pill-ok", FAILED: "pill-bad" };
const SEVERITY = { INFO: "status-info", WARNING: "status-warn", CRITICAL: "status-bad" };

const user = () => session.read()?.user;
const isGlobal = () => user()?.role === "ADMIN_GLOBAL";

function start() {
  const current = session.read();
  if (current?.token) return showApp();
  renderLogin(root, {
    title: "Plataforma Central",
    note: "Administración global y líderes. El personal de bodega entra por la página de su bodega.",
    async onSubmit({ username, password }) {
      const result = await api("/auth/login", { method: "POST", body: { username, password } });
      session.write({ token: result.access_token, user: result.user });
      showApp();
    },
  });
}

function logout(expired = false) {
  session.clear();
  if (expired) toast("Tu sesión terminó. Vuelve a iniciar sesión.", "bad");
  start();
}

function showApp() {
  const nav = NAV[user().role] || NAV.OTHER;
  state.viewEl = renderShell(root, {
    brand: "Plataforma Central",
    brandNote: "Todas las bodegas",
    nav,
    user: user(),
    onNavigate: (id) => go(id),
    onLogout: () => logout(),
  });
  go(nav[0].id);
  refreshCounts();
  setInterval(refreshCounts, 30000);
}

async function refreshCounts() {
  try {
    if (["ADMIN_GLOBAL", "LIDER"].includes(user()?.role)) setCount(root, "notifications", (await api("/notifications?unread=true&limit=1")).unread);
    if (isGlobal()) setCount(root, "requests", (await api("/product-requests?status=PENDING")).length);
  } catch {
    /* los contadores son una ayuda */
  }
}

const VIEWS = {};

function go(id) {
  setCurrent(root, id);
  VIEWS[id](state.viewEl);
}

function head(title, note, actions = "") {
  return `<div class="page-head"><div><h1>${escapeHtml(title)}</h1>${note ? `<p>${note}</p>` : ""}</div><div class="cluster">${actions}</div></div>`;
}

// Bodegas
VIEWS.warehouses = async (el) => {
  render(el, `${head("Bodegas", "", `<button class="btn btn-primary" data-register>${icon("plus")}Registrar bodega</button>`)}
    <div class="grid-3" data-kpis></div>
    <div style="margin-top:var(--space-5)" data-table>${loadingTable(["Bodega", "Estado", "Última respuesta", "Eventos"], 4)}</div>`);
  el.querySelector("[data-register]").addEventListener("click", () => registerWarehouse(el));
  const [statuses, registry] = await Promise.all([api("/warehouses"), api("/admin/warehouses")]);
  const info = Object.fromEntries(registry.map((w) => [w.warehouse_id, w]));
  const online = statuses.filter((w) => w.status === "ONLINE").length;
  const down = statuses.filter((w) => w.status === "OFFLINE");
  el.querySelector(".page-head h1").textContent = `Bodegas: ${online} de ${statuses.length} en línea`;
  el.querySelector("[data-kpis]").innerHTML = [
    ["En línea", fmt.int(online), "responden al heartbeat cifrado"],
    ["Sin respuesta", fmt.int(down.length), down.map((w) => w.warehouse_id).join(", ") || "ninguna"],
    ["Eventos por recibir", fmt.int(statuses.reduce((s, w) => s + (w.events_missing || 0), 0)), "se recuperan solos"],
  ].map(([label, value, note]) => `<div class="panel kpi"><span class="kpi-label">${label}</span><span class="kpi-value">${value}</span><span class="kpi-note">${escapeHtml(note)}</span></div>`).join("");
  el.querySelector("[data-table]").innerHTML = table({
    columns: [
      { label: "Bodega", render: (w) => `<span class="mono">${escapeHtml(w.warehouse_id)}</span> ${escapeHtml(info[w.warehouse_id]?.name || "")}<div class="subtle">${escapeHtml([info[w.warehouse_id]?.location, info[w.warehouse_id]?.nit && `NIT ${info[w.warehouse_id].nit}`].filter(Boolean).join(" · "))}</div>` },
      { label: "Estado", render: (w) => `<span class="status ${STATUS[w.status][1]}">${STATUS[w.status][0]}</span><div class="subtle">${escapeHtml(w.note)}</div>` },
      { label: "Última respuesta", render: (w) => `${fmt.ago(w.last_heartbeat_at)}<div class="subtle">${escapeHtml(w.heartbeat?.answered_by || "")}</div>` },
      { label: "Rechazos", num: true, render: (w) => fmt.int(w.heartbeat?.rejected_total || 0) },
      { label: "Por recibir", num: true, render: (w) => fmt.int(w.events_missing || 0) },
    ],
    rows: statuses,
    empty: "Todavía no hay bodegas",
  });
};

function registerWarehouse(el) {
  openDialog({
    title: "Registrar una bodega",
    submitLabel: "Registrar y aprovisionar",
    body: `
      <div class="field-row">
        <label class="field"><span>NIT</span><input name="nit" pattern="\\d{6,12}(-\\d)?" placeholder="900123456-7" required></label>
        <label class="field"><span>Capacidad (unidades)</span><input name="capacity_units" type="number" min="1" required></label>
      </div>
      <label class="field"><span>Nombre</span><input name="name" minlength="3" maxlength="200" required></label>
      <div class="field-row">
        <label class="field"><span>Ciudad</span><input name="location" maxlength="120" required></label>
        <label class="field"><span>Dirección</span><input name="address" maxlength="250" required></label>
      </div>
      <label class="field"><span>Descripción</span><textarea name="description" maxlength="1000"></textarea></label>`,
    async onSubmit(data) {
      const result = await api("/admin/warehouses", { method: "POST", body: { ...data, capacity_units: Number(data.capacity_units) } });
      showProvisioning(result);
      VIEWS.warehouses(el);
    },
  });
}

function showProvisioning(result) {
  const dialog = document.createElement("dialog");
  dialog.innerHTML = `<div class="dialog-body">
      <h2>${escapeHtml(result.warehouse_id)} quedó registrada</h2>
      <p class="muted">Ya tiene su cola de comandos <span class="mono">${escapeHtml(result.command_queue)}</span> con ${fmt.int(result.catalog_products_queued)} productos del catálogo y su clave de heartbeat. Falta levantar su infraestructura con este archivo.</p>
      ${result.initial_credentials.length ? `<div><div class="section-title">Usuarios iniciales (la contraseña se muestra una sola vez)</div>
        ${table({ columns: [{ label: "Usuario", render: (c) => `<span class="mono">${escapeHtml(c.username)}</span>` }, { label: "Rol", render: (c) => escapeHtml(ROLE_NAMES[c.role] || c.role) }, { label: "Contraseña temporal", render: (c) => `<span class="mono">${escapeHtml(c.temporary_password)}</span>` }], rows: result.initial_credentials })}</div>` : ""}
      <label class="field"><span>infrastructure/docker/components/${escapeHtml(result.warehouse_id.toLowerCase().replace("-", "_"))}/.env</span><textarea class="mono" rows="8" readonly>${escapeHtml(result.env_file)}</textarea></label>
      <label class="field"><span>En AWS, agregar a <span class="mono">warehouses</span> en terraform.tfvars</span><input class="mono" readonly value="${escapeHtml(result.terraform_entry)}"></label>
      <div class="dialog-actions"><button class="btn btn-primary" data-close>Listo</button></div></div>`;
  document.body.append(dialog);
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
}

// Tablero (líder)
VIEWS.overview = async (el) => {
  render(el, `${head("Cómo va la operación", "Los datos llegan de las bodegas en segundos; el último movimiento puede tardar un momento en aparecer.")}
    <div class="grid-4" data-kpis>${'<div class="panel"><span class="skeleton"></span></div>'.repeat(4)}</div>
    <div class="grid-2" style="margin-top:var(--space-5)">
      <section class="panel"><div class="panel-head"><h2>Ventas de los últimos 14 días</h2><span class="subtle">unidades</span></div><div data-sales></div></section>
      <section class="panel"><div class="panel-head"><h2>Más vendidos</h2><span class="subtle">30 días</span></div><div data-top></div></section>
    </div>
    <section style="margin-top:var(--space-5)"><div class="panel-head"><h2>Existencias por bodega</h2></div><div data-stock>${loadingTable(["SKU", "Producto", "Total"], 5)}</div></section>`);
  const [summary, sales, top, stock, warehouses] = await Promise.all([
    api("/global/summary"), api("/reports/sales-by-day?days=14"), api("/reports/top-products?days=30&limit=8"),
    api("/reports/stock-by-warehouse"), api("/warehouses"),
  ]);
  const value = stock.rows.reduce((s, r) => s + Number(r.retail_value || 0), 0);
  const soldUnits = sales.reduce((s, r) => s + r.units, 0);
  el.querySelector("[data-kpis]").innerHTML = [
    ["Unidades en bodega", fmt.int(summary.total_quantity), `${fmt.int(summary.total_reserved)} reservadas`],
    ["Valor a precio de tienda", fmt.money(value), `${fmt.int(summary.skus)} productos`],
    ["Vendidas en 14 días", fmt.int(soldUnits), fmt.money(sales.reduce((s, r) => s + Number(r.sales_value || 0), 0))],
    ["Bodegas en línea", `${warehouses.filter((w) => w.status === "ONLINE").length}/${warehouses.length}`, warehouses.filter((w) => w.stale).map((w) => w.warehouse_id).join(", ") || "todas al día"],
  ].map(([label, v, note]) => `<div class="panel kpi"><span class="kpi-label">${label}</span><span class="kpi-value" style="font-size:var(--text-2xl)">${v}</span><span class="kpi-note">${escapeHtml(note)}</span></div>`).join("");

  const byDay = {};
  sales.forEach((r) => (byDay[r.day] = (byDay[r.day] || 0) + r.units));
  const days = Object.entries(byDay).slice(-14);
  const max = Math.max(1, ...days.map(([, u]) => u));
  el.querySelector("[data-sales]").innerHTML = days.length
    ? `<div class="bars">${days.map(([day, units]) => `<div class="bar-row"><span class="subtle">${new Date(`${day}T12:00:00`).toLocaleDateString("es-CO", { weekday: "short", day: "numeric", month: "short" })}</span><div class="bar-track"><div class="bar-fill" style="width:${(units / max) * 100}%"></div></div><span class="num">${fmt.int(units)}</span></div>`).join("")}</div>`
    : emptyState("Sin ventas en este periodo");
  el.querySelector("[data-top]").innerHTML = top.length
    ? `<div class="list">${top.map((p, i) => `<div class="list-item"><span class="num subtle">${i + 1}</span><div><div class="list-title">${escapeHtml(p.name || p.sku)}</div><div class="subtle mono">${escapeHtml(p.sku)}</div></div><span class="num">${fmt.int(p.units_sold)}</span></div>`).join("")}</div>`
    : emptyState("Sin ventas en este periodo");
  el.querySelector("[data-stock]").innerHTML = table({
    columns: [
      { label: "SKU", render: (r) => `<span class="mono">${escapeHtml(r.sku)}</span>` },
      { label: "Producto", render: (r) => escapeHtml(r.name || "—") },
      ...stock.warehouses.map((w) => ({ label: w, num: true, render: (r) => fmt.int(r.by_warehouse[w] ?? 0) })),
      { label: "Total", num: true, render: (r) => `<strong>${fmt.int(r.total_on_hand)}</strong>` },
      { label: "Valor", num: true, render: (r) => fmt.money(r.retail_value) },
    ],
    rows: stock.rows,
    empty: "Las bodegas todavía no han enviado inventario",
  });
};

// Inventario global
VIEWS.inventory = async (el) => {
  render(el, `${head("Inventario de todas las bodegas", "Busca un SKU para ver cuánto tiene cada bodega y qué tan reciente es el dato.",
    `<form class="cluster" data-search><input name="sku" placeholder="SKU" class="mono" required><button class="btn btn-primary" type="submit">${icon("search")}Consultar</button></form>`)}
    <div data-detail></div>
    <div style="margin-top:var(--space-5)" data-table>${loadingTable(["SKU", "Producto", "Total"], 6)}</div>`);
  el.querySelector("[data-search]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const { sku } = formData(event.currentTarget);
    await busy(event.currentTarget.querySelector("button"), () => showSku(el, sku)).catch((err) => toast(err.message, "bad"));
  });
  const data = await api("/global/inventory?limit=500");
  el.querySelector("[data-table]").innerHTML = table({
    columns: [
      { label: "SKU", render: (r) => `<span class="mono">${escapeHtml(r.sku)}</span>` },
      { label: "Producto", render: (r) => escapeHtml(r.name || "—") },
      { label: "Por bodega", render: (r) => Object.entries(r.by_warehouse).map(([w, q]) => `<span class="subtle">${escapeHtml(w)}</span> <span class="num">${fmt.int(q)}</span>`).join(" · ") },
      { label: "Disponible", num: true, render: (r) => `<strong>${fmt.int(r.total_quantity)}</strong>` },
      { label: "", render: (r) => (r.stale_warehouses.length ? `<span class="pill pill-warn">Puede estar desactualizado</span>` : "") },
    ],
    rows: data.items,
    empty: "Las bodegas todavía no han enviado inventario",
    rowAttrs: (r) => `data-clickable data-sku="${escapeHtml(r.sku)}"`,
  });
  el.querySelectorAll("[data-sku]").forEach((row) => row.addEventListener("click", () => showSku(el, row.dataset.sku)));
};

async function showSku(el, sku) {
  const data = await api(`/global/inventory/${encodeURIComponent(sku)}`);
  el.querySelector("[data-detail]").innerHTML = `<section class="panel stack">
      <div class="panel-head"><h2>${escapeHtml(data.product?.name || sku)} <span class="mono subtle">${escapeHtml(sku)}</span></h2>
        <span class="kpi"><span class="kpi-label">Disponible en total</span><span class="kpi-value" style="font-size:var(--text-2xl)">${fmt.int(data.total_quantity)}</span></span></div>
      ${data.warnings?.length ? `<p class="notice notice-warn">${data.warnings.map(escapeHtml).join(" ")}</p>` : ""}
      ${table({
        columns: [
          { label: "Bodega", render: (w) => `<span class="mono">${escapeHtml(w.warehouse_id)}</span>` },
          { label: "Disponible", num: true, render: (w) => fmt.int(w.quantity) },
          { label: "Reservado", num: true, render: (w) => fmt.int(w.reserved_quantity) },
          { label: "Cambió en la bodega", render: (w) => fmt.ago(w.changed_in_warehouse_at) },
          { label: "Lo supo la central", render: (w) => fmt.ago(w.known_by_central_at) },
          { label: "Estado", render: (w) => `<span class="status ${STATUS[w.warehouse_status]?.[1] || "status-idle"}">${STATUS[w.warehouse_status]?.[0] || escapeHtml(w.warehouse_status || "")}</span>` },
        ],
        rows: data.warehouses || [],
        empty: "Ninguna bodega tiene este SKU",
      })}</section>`;
}

// Catálogo universal
VIEWS.catalog = async (el) => {
  render(el, `${head("Catálogo universal", "Cada producto que se crea o se cambia aquí llega a todas las bodegas; cada una decide si lo maneja.",
    `<button class="btn btn-primary" data-new>${icon("plus")}Nuevo producto</button>`)}
    <div data-table>${loadingTable(["SKU", "Producto", "Categoría", "Precio"], 6)}</div>`);
  el.querySelector("[data-new]").addEventListener("click", () => productDialog(null, () => VIEWS.catalog(el)));
  const rows = await api("/catalog");
  el.querySelector("[data-table]").innerHTML = table({
    columns: [
      { label: "SKU", render: (p) => `<span class="mono">${escapeHtml(p.sku)}</span>` },
      { label: "Producto", render: (p) => `${escapeHtml(p.name)}${p.aliases.length ? `<div class="subtle">${escapeHtml(p.aliases.join(", "))}</div>` : ""}` },
      { label: "Categoría", render: (p) => escapeHtml(p.category || "—") },
      { label: "Proveedor", render: (p) => escapeHtml(p.supplier || "—") },
      { label: "Costo", num: true, render: (p) => fmt.money(p.supplier_cost) },
      { label: "Tienda", num: true, render: (p) => fmt.money(p.retail_price) },
      { label: "Mayorista", num: true, render: (p) => fmt.money(p.wholesale_price) },
      { label: "Versión", num: true, render: (p) => fmt.int(p.catalog_version) },
    ],
    rows,
    empty: "El catálogo está vacío",
    rowAttrs: (p) => `data-clickable data-sku="${escapeHtml(p.sku)}"`,
  });
  el.querySelectorAll("[data-sku]").forEach((row) => row.addEventListener("click", () => productDialog(rows.find((p) => p.sku === row.dataset.sku), () => VIEWS.catalog(el))));
};

function productFields(p = {}) {
  const v = (key) => escapeHtml(p[key] ?? "");
  return `
    <div class="field-row">
      <label class="field"><span>SKU</span><input name="sku" class="mono" pattern="[A-Za-z0-9._\\-]+" maxlength="64" value="${v("sku")}" ${p.sku && p.catalog_version ? "readonly" : "required"}></label>
      <label class="field"><span>Unidad</span><input name="unit" maxlength="30" value="${v("unit") || "UNIDAD"}" required></label>
    </div>
    <label class="field"><span>Nombre universal</span><input name="name" maxlength="200" value="${v("name")}" required></label>
    <label class="field"><span>Otros nombres</span><input name="aliases" value="${escapeHtml((p.aliases || []).join(", "))}"><small>Separados por coma</small></label>
    <div class="field-row">
      <label class="field"><span>Categoría</span><input name="category" maxlength="100" value="${v("category")}"></label>
      <label class="field"><span>Proveedor</span><input name="supplier" maxlength="200" value="${v("supplier")}"></label>
    </div>
    <div class="field-row">
      <label class="field"><span>Costo</span><input name="supplier_cost" type="number" min="0" step="0.01" value="${v("supplier_cost")}"></label>
      <label class="field"><span>Precio tienda</span><input name="retail_price" type="number" min="0" step="0.01" value="${v("retail_price")}"></label>
      <label class="field"><span>Mayorista</span><input name="wholesale_price" type="number" min="0" step="0.01" value="${v("wholesale_price")}"></label>
    </div>
    <label class="field"><span>Descripción</span><textarea name="description" maxlength="1000">${v("description")}</textarea></label>`;
}

function productBody(data) {
  const body = { ...data, aliases: (data.aliases || "").split(",").map((a) => a.trim()).filter(Boolean) };
  return body;
}

function productDialog(product, done) {
  openDialog({
    title: product ? `Editar ${product.name}` : "Nuevo producto universal",
    submitLabel: product ? "Guardar y enviar a las bodegas" : "Crear y enviar a las bodegas",
    body: productFields(product || {}),
    async onSubmit(data) {
      const body = productBody(data);
      if (product) {
        delete body.sku;
        await api(`/catalog/${encodeURIComponent(product.sku)}`, { method: "PUT", body });
      } else {
        await api("/catalog", { method: "POST", body });
      }
      toast("Catálogo actualizado; las bodegas lo reciben por su cola de comandos");
      done();
    },
  });
}

// Solicitudes de productos
VIEWS.requests = async (el) => {
  render(el, `${head("Productos que piden las bodegas", "Cada solicitud ya la aprobó la administración de su bodega. Al aprobarla aquí, el producto entra al catálogo universal.")}
    <div data-table>${loadingTable(["Solicitud", "Producto", "Bodega"], 4)}</div>`);
  const rows = await api("/product-requests?status=PENDING");
  setCount(root, "requests", rows.length);
  el.querySelector("[data-table]").innerHTML = table({
    columns: [
      { label: "Solicitud", render: (r) => `<span class="mono">${escapeHtml(r.request_code || r.request_id)}</span><div class="subtle">${fmt.ago(r.created_at)}</div>` },
      { label: "Producto", render: (r) => `<strong>${escapeHtml(r.product.name)}</strong><div class="subtle">${escapeHtml([r.product.category, r.product.unit, r.product.reason].filter(Boolean).join(" · "))}</div>` },
      { label: "Bodega", render: (r) => `${escapeHtml(r.warehouse_id)}<div class="subtle">${escapeHtml(r.requested_by || "")} → ${escapeHtml(r.approved_by_warehouse || "")}</div>` },
      { label: "", render: (r) => `<div class="cluster"><button class="btn btn-sm btn-primary" data-approve="${r.id}">Aprobar</button><button class="btn btn-sm btn-ghost" data-reject="${r.id}">Rechazar</button></div>` },
    ],
    rows,
    empty: "No hay solicitudes pendientes",
  });
  el.querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", () => {
    const r = rows.find((x) => String(x.id) === b.dataset.approve);
    openDialog({
      title: `Aprobar «${r.product.name}»`,
      submitLabel: "Aprobar y crear el producto",
      body: productFields({ ...r.product, aliases: [] }),
      async onSubmit(data) {
        await api(`/product-requests/${r.id}/approve`, { method: "POST", body: { product: productBody(data) } });
        toast(`${data.sku} creado y enviado a todas las bodegas`);
        VIEWS.requests(el);
      },
    });
  }));
  el.querySelectorAll("[data-reject]").forEach((b) => b.addEventListener("click", () => openDialog({
    title: "Rechazar la solicitud",
    submitLabel: "Rechazar",
    body: '<label class="field"><span>Motivo (lo verá la bodega)</span><textarea name="note" minlength="3" required></textarea></label>',
    async onSubmit(data) {
      await api(`/product-requests/${b.dataset.reject}/reject`, { method: "POST", body: data });
      toast("Solicitud rechazada");
      VIEWS.requests(el);
    },
  })));
};

// Usuarios
VIEWS.users = async (el) => {
  const identity = await api("/admin/settings");
  const source = identity.directory ? `Directorio ${identity.directory.base_dn}` : "Directorio local de la central";
  const note = identity.manages_users
    ? "Las contraseñas temporales se muestran una sola vez; el usuario debe cambiarla al entrar."
    : "Las credenciales se administran en el directorio corporativo; aquí se ven los perfiles y su último acceso.";
  render(el, `${head("Usuarios y credenciales", `${source}. ${note}`,
    identity.manages_users ? `<button class="btn btn-primary" data-new>${icon("plus")}Crear credenciales</button>` : "")}
    <div data-table>${loadingTable(["Usuario", "Rol", "Bodega", "Último acceso"], 6)}</div>`);
  el.querySelector("[data-new]")?.addEventListener("click", () => openDialog({
    title: "Crear credenciales",
    submitLabel: "Crear",
    body: `
      <div class="field-row">
        <label class="field"><span>Usuario</span><input name="username" class="mono" pattern="[a-z0-9._\\-]{3,60}" required placeholder="nombre.apellido"></label>
        <label class="field"><span>Rol</span><select name="role"><option value="LIDER">Líder</option><option value="ADMIN_BODEGA">Administración de bodega</option><option value="REGISTRADOR">Registro de entradas y salidas</option><option value="ADMIN_GLOBAL">Administración global</option></select></label>
      </div>
      <label class="field"><span>Nombre completo</span><input name="display_name" minlength="3" required></label>
      <label class="field"><span>Bodega</span><input name="warehouse_id" placeholder="Solo para roles de bodega: WH-001"></label>`,
    async onSubmit(data) {
      const created = await api("/admin/users", { method: "POST", body: data });
      openDialog({
        title: `Credenciales de ${created.username}`,
        submitLabel: "Listo",
        body: `<p class="muted">Entrégale esta contraseña temporal por un canal seguro. No se volverá a mostrar.</p><input class="mono input-lg" readonly value="${escapeHtml(created.temporary_password)}">`,
        onSubmit: async () => {},
      });
      VIEWS.users(el);
    },
  }));
  const users = await api("/admin/users");
  el.querySelector("[data-table]").innerHTML = table({
    columns: [
      { label: "Usuario", render: (u) => `<span class="mono">${escapeHtml(u.username)}</span><div class="subtle">${escapeHtml(u.display_name)}</div>` },
      { label: "Rol", render: (u) => escapeHtml(ROLE_NAMES[u.role] || u.role) },
      { label: "Bodega", render: (u) => escapeHtml(u.warehouse_id || "—") },
      { label: "Último acceso", render: (u) => fmt.ago(u.last_login_at) },
      { label: "Estado", render: (u) => (u.is_active ? (u.must_change_password ? '<span class="pill pill-warn">Contraseña temporal</span>' : '<span class="pill pill-ok">Activo</span>') : '<span class="pill">Inactivo</span>') },
      { label: "", render: (u) => (identity.manages_users ? `<button class="btn btn-sm btn-ghost" data-reset="${escapeHtml(u.username)}">Nueva contraseña</button>` : "") },
    ],
    rows: users,
  });
  el.querySelectorAll("[data-reset]").forEach((b) => b.addEventListener("click", async () => {
    const result = await busy(b, () => api(`/admin/users/${encodeURIComponent(b.dataset.reset)}/reset-password`, { method: "POST" }));
    openDialog({ title: `Contraseña temporal de ${result.username}`, submitLabel: "Listo", body: `<input class="mono input-lg" readonly value="${escapeHtml(result.temporary_password)}">`, onSubmit: async () => {} });
  }));
};

// Notificaciones
VIEWS.notifications = async (el) => {
  render(el, `${head("Notificaciones", "", `<button class="btn" data-all>${icon("check")}Marcar todas como leídas</button>`)}<div class="panel" data-list>${loadingTable(["Notificación"], 5)}</div>`);
  const draw = async () => {
    const data = await api("/notifications?limit=100");
    setCount(root, "notifications", data.unread);
    el.querySelector(".page-head h1").textContent = data.unread ? `Notificaciones: ${data.unread} sin leer` : "Notificaciones al día";
    el.querySelector("[data-list]").innerHTML = data.items.length
      ? `<div class="list">${data.items.map((n) => `
        <div class="list-item ${n.read_at ? "" : "unread"}">
          <span class="status ${SEVERITY[n.severity] || "status-info"}"></span>
          <div><div class="list-title">${escapeHtml(n.title)}</div>${n.body ? `<div class="muted">${escapeHtml(n.body)}</div>` : ""}<div class="subtle">${fmt.ago(n.created_at)}</div></div>
          ${n.read_at ? "<span></span>" : `<button class="btn btn-sm btn-ghost" data-read="${n.id}">Leída</button>`}
        </div>`).join("")}</div>`
      : emptyState("No hay notificaciones");
    el.querySelectorAll("[data-read]").forEach((b) => b.addEventListener("click", async () => {
      await busy(b, () => api(`/notifications/${b.dataset.read}/read`, { method: "POST" }));
      draw();
    }));
  };
  el.querySelector("[data-all]").addEventListener("click", async (event) => {
    await busy(event.currentTarget, () => api("/notifications/read-all", { method: "POST" }));
    draw();
  });
  await draw();
};

// Transferencias
VIEWS.transfers = async (el) => {
  const canCreate = isGlobal() || user()?.role === "ADMIN_BODEGA";
  render(el, `${head("Transferencias entre bodegas", "El origen aparta las unidades, el destino las recibe y, si un paso falla, la Saga deshace lo hecho.",
    canCreate ? `<button class="btn btn-primary" data-new>${icon("truck")}Nueva transferencia</button>` : "")}
    <div data-table>${loadingTable(["Transferencia", "Producto", "Ruta", "Estado"], 6)}</div>`);
  if (canCreate) el.querySelector("[data-new]").addEventListener("click", async () => {
    const warehouses = await api("/warehouses");
    const options = warehouses.map((w) => `<option value="${escapeHtml(w.warehouse_id)}">${escapeHtml(w.warehouse_id)}${w.name ? ` · ${escapeHtml(w.name)}` : ""}</option>`).join("");
    openDialog({
      title: "Nueva transferencia",
      submitLabel: "Solicitar",
      body: `
        <label class="field"><span>SKU</span><input name="sku" class="mono" required></label>
        <div class="field-row">
          <label class="field"><span>Desde</span><select name="source_warehouse_id">${options}</select></label>
          <label class="field"><span>Hacia</span><select name="destination_warehouse_id">${options}</select></label>
        </div>
        <label class="field"><span>Cantidad</span><input name="quantity" type="number" min="1" required></label>`,
      async onSubmit(data) {
        const transfer = await api("/transfers", { method: "POST", body: { ...data, quantity: Number(data.quantity) } });
        toast(`${transfer.transfer_id} en curso`);
        VIEWS.transfers(el);
      },
    });
  });
  const rows = await api("/transfers?limit=100");
  el.querySelector("[data-table]").innerHTML = table({
    columns: [
      { label: "Transferencia", render: (t) => `<span class="mono">${escapeHtml(t.transfer_id)}</span><div class="subtle">${fmt.ago(t.created_at)}${t.requested_by ? ` · ${escapeHtml(t.requested_by)}` : ""}</div>` },
      { label: "Producto", render: (t) => `<span class="mono">${escapeHtml(t.sku)}</span>` },
      { label: "Cantidad", num: true, render: (t) => fmt.int(t.quantity) },
      { label: "Ruta", render: (t) => `${escapeHtml(t.source_warehouse_id)} → ${escapeHtml(t.destination_warehouse_id)}` },
      { label: "Estado", render: (t) => `<span class="pill ${TRANSFER_PILL[t.status] || ""}">${escapeHtml(TRANSFER_STATE_NAMES[t.state] || t.state)}</span><div class="subtle">${escapeHtml(t.failure_reason || t.note || "")}</div>` },
    ],
    rows,
    empty: "No hay transferencias",
  });
};

// Reportes con Athena
VIEWS.athena = async (el) => {
  render(el, `${head("Reportes con Athena", "Consultas SQL sobre los datos exportados a S3; corren en AWS sin cargar la base de la central.")}<div data-body>${loadingTable(["Reporte", ""], 4)}</div><div data-result style="margin-top:var(--space-5)"></div>`);
  const info = await api("/reports/athena");
  el.querySelector("[data-body]").innerHTML = `${info.configured ? "" : `<p class="notice notice-warn" style="margin-bottom:var(--space-4)">${escapeHtml(info.note)}</p>`}
    ${table({
      columns: [
        { label: "Reporte", key: "title" },
        { label: "", render: (r) => `<div class="cluster" style="justify-content:flex-end"><a class="btn btn-sm btn-ghost" href="${escapeHtml(r.sql_url)}" target="_blank" rel="noopener">Ver SQL</a><button class="btn btn-sm btn-primary" data-run="${escapeHtml(r.name)}" ${info.configured ? "" : "disabled"}>Ejecutar</button></div>` },
      ],
      rows: info.reports,
    })}`;
  el.querySelectorAll("[data-run]").forEach((b) => b.addEventListener("click", () => busy(b, async () => {
    const started = await api(`/reports/athena/${b.dataset.run}?days=30`, { method: "POST" });
    for (let i = 0; i < 60; i += 1) {
      const status = await api(started.status_url);
      if (status.state === "SUCCEEDED") return showAthena(el, status);
      if (["FAILED", "CANCELLED"].includes(status.state)) throw new Error(status.reason || "La consulta falló");
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    throw new Error("La consulta sigue en curso; vuelve a intentarlo en un momento");
  }).catch((err) => toast(err.message, "bad"))));
};

function showAthena(el, status) {
  el.querySelector("[data-result]").innerHTML = `<section class="stack"><div class="panel-head"><h2>Resultado</h2><span class="subtle">${fmt.int(status.rows?.length || 0)} filas · ${fmt.int(status.data_scanned_bytes)} bytes leídos de S3</span></div>
    ${table({ columns: (status.columns || []).map((c, i) => ({ label: c, render: (row) => escapeHtml(row[i]) })), rows: status.rows || [] })}</section>`;
}

start();
