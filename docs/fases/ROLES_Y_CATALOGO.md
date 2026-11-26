# Usuarios, roles, catálogo universal y operación de bodega

Requisitos funcionales del enunciado que atraviesan todas las fases: roles, catálogo universal,
solicitudes, lotes, historial de transacciones, alertas de incongruencias y configuración de bodegas
nuevas.

## Roles

| Rol | Entra por | Puede |
|---|---|---|
| Administrador global (`ADMIN_GLOBAL`) | Tablero central | Registrar bodegas, crear productos universales, aprobar productos que piden las bodegas, crear credenciales, ver notificaciones |
| Líder (`LIDER`) | Tablero central | Tablero, reportes, inventario global, contratos con proveedores, reporte diario |
| Administrador de bodega (`ADMIN_BODEGA`) | Página de su bodega | Catálogo de su bodega, aprobar solicitudes (ajustes y productos nuevos), atender alertas, pedir transferencias |
| Registrador (`REGISTRADOR`) | Página de su bodega | Entradas y salidas, solicitudes de producto nuevo y de ajuste |

Los roles de bodega solo valen en su bodega: un token de `ADMIN_BODEGA` de WH-001 recibe 403 en WH-002.

## Autenticación

```text
POST /auth/login (central) --> token firmado Ed25519 (rol, usuario, bodega, vence en 8 h)
        |
página de la bodega --Authorization: Bearer--> API de la bodega --verifica con la clave pública--> OK / 401 / 403
                                                   ^
                                   /auth/keys (se guarda en trusted_keys: sirve con la central caída)
```

- Contraseñas con scrypt; las credenciales nuevas son temporales y se cambian al entrar.
- Directorio: OpenLDAP con forma de Active Directory (`central_ldap`, ADR-010). El rol sale del grupo
  (`Administradores-Globales`, `Lideres`, `Administradores-WH-00X`, `Registradores-WH-00X`) y la
  contraseña se valida en el directorio; la central solo guarda el perfil. `IDENTITY_PROVIDER=local`
  usa la tabla `users` (pruebas unitarias o si el directorio no está disponible).
- `AUTH_ENABLED=false` solo existe para las pruebas unitarias.

## Catálogo universal y catálogo local

1. El administrador global crea o cambia un producto universal (`POST /catalog`). Se genera un
   `CATALOG_UPSERT` por bodega con versión.
2. Cada bodega guarda la copia (`universal_products`) y su administrador lo incorpora al catálogo
   local con sus precios y lote (`POST /catalog/local`).
3. Con `CATALOG_STRICT=true` una bodega no crea SKU fuera del catálogo universal.
4. Si falta un producto, el registrador pide `NEW_PRODUCT`; su administrador lo aprueba y la solicitud
   llega a la central; el administrador global lo crea y la respuesta vuelve a la bodega
   (`PRODUCT_REQUEST_RESOLVED`).

## Movimientos, lotes e historial

- `POST /inventory/movements`: `RECEIPT` (compra), `SHIPMENT` (venta), `ADJUSTMENT`, `RESERVATION`,
  `RELEASE`, `RETURN`. Las transferencias solo las registra la Saga.
- Con `lot`, el saldo del lote se verifica en la misma transacción: una salida de un lote inexistente o
  insuficiente se rechaza.
- Cada movimiento guarda precio unitario, contraparte (proveedor o cliente) y usuario.
- `GET /inventory/transactions`: historial de compras y ventas con totales; `GET /inventory/{id}/lots`.
- Agotados: `STOCK_DEPLETED` y su alerta (log de quiebres de stock).

## Desfases e incongruencias

| Origen | Flujo | Alerta |
|---|---|---|
| El registrador cuenta algo distinto | Solicitud `ADJUSTMENT` con el conteo → el administrador la aprueba y se aplica la diferencia | `DISCREPANCY` al administrador de bodega (evento `STOCK_DISCREPANCY`, asíncrono) |
| El saldo no cuadra con el historial | El audit worker recalcula con cada movimiento | `INCONSISTENCY` (evento `INVENTORY_INCONSISTENCY`) |

Un registrador no puede registrar un `ADJUSTMENT` directo: debe pasar por la solicitud.

## Configuración de una bodega nueva

`POST /admin/warehouses` (pantalla Bodegas → Registrar) con NIT, nombre, ciudad, dirección y capacidad:

1. Registra la bodega y su clave de heartbeat.
2. Encola el catálogo universal completo en su cola exclusiva `commands.WH-00N`.
3. Crea `admin.wh00n` y `registrador.wh00n` con contraseñas temporales.
4. Devuelve el `.env` de la bodega (puertos derivados del número) y la línea para `warehouses` en
   `terraform.tfvars`. Con eso se levantan su base, Redis, RabbitMQ y APIs (`manage start wh_00n` o
   `terraform apply`). Al primer heartbeat válido queda en línea.

## Pantallas

| Página | Rol | Secciones |
|---|---|---|
| Bodega (`:8081`, `:8082`…) | Registrador | Registrar movimiento (búsqueda por SKU o nombre, lote, precio, contraparte), solicitudes |
| | Administrador de bodega | Inventario, catálogo local, solicitudes por aprobar, alertas, transferencias |
| Central (`:8090/dashboard`) | Administrador global | Bodegas, tablero, catálogo universal, solicitudes, usuarios, notificaciones, transferencias, inventario global, reporte diario, contratos, sensores, Athena |
| | Líder | Tablero, contratos, reporte diario, inventario global, sensores, Athena, transferencias, notificaciones |

Ambas páginas comparten `services/web-ui` (estilos, componentes y cliente de la API).

Usuarios de desarrollo (contraseña `Bodegas2026!`, solo para entornos locales): `admin.global`,
`lider`, `admin.wh001`, `registrador.wh001` (y los equivalentes de WH-002 y WH-003).
