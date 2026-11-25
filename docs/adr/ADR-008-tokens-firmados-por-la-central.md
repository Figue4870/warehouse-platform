# ADR-008 — Tokens firmados por la central y verificados en cada bodega

## Contexto

Los cuatro roles del enunciado (administrador global, Líder, administrador de bodega y registrador)
entran a la central o a su bodega. La bodega debe seguir autorizando peticiones aunque la central esté
caída.

## Alternativas

- Sesiones en la central (cada petición de una bodega le preguntaría a la central).
- Un secreto compartido (HMAC) en todas las bodegas: cualquier bodega podría emitir tokens.
- Firma asimétrica: la central firma y las bodegas solo verifican.

## Decisión

JWT firmados con Ed25519 por la central (`POST /auth/login`), con rol, usuario y bodega. Cada bodega
descarga las claves públicas de `/auth/keys`, las guarda en su base (`trusted_keys`) y verifica
localmente. La central usa un token de servicio para llamar a `/sync` de las bodegas. Los roles de
bodega solo valen en su bodega.

## Consecuencias

- Con la central caída, las bodegas siguen aceptando los tokens vigentes (la clave está en su base);
  no se pueden iniciar sesiones nuevas hasta que vuelva.
- Una bodega comprometida no puede fabricar tokens.
- Revocar antes del vencimiento requiere rotar la clave (`kid`); la duración se limita con
  `AUTH_TOKEN_TTL_MINUTES`.
