# Evidencia: pruebas de carga (Fase 11)

## Condiciones

| Parámetro | Valor |
|---|---|
| Escenario | WH-001: 9 consultas de saldo (`GET /inventory/{id}`) por cada entrada (`POST /inventory/movements`), autenticadas con un token real |
| Productos | 12 (lecturas y entradas repartidas al azar) |
| Usuarios | 100, 500, 1.000 y 5.000 conexiones simultáneas que repiten sin pausa |
| Duración | 15 s por nivel, con pausa y espera de recuperación entre niveles |
| Instancias | 1 o 3 procesos de la API sobre la misma base, Redis y RabbitMQ |
| Límites por instancia | `UVICORN_LIMIT_CONCURRENCY=120`, pool de base 5 + 5, espera de conexión 5 s |
| Máquina | 2 vCPU compartidas entre el generador de carga, las APIs, PostgreSQL, Redis y RabbitMQ |

Las cifras absolutas dependen de esa máquina; lo que se compara son las tendencias entre configuraciones.
En AWS cada bodega corre en su propia t3.small y WH-002 tiene dos detrás de un ALB.

## Matriz

| Configuración | Usuarios | req/s | P50 ms | P95 ms | P99 ms | Errores | Hit ratio caché | Conexiones BD (máx.) | Esperas de bloqueo (máx.) | Recuperación |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 instancia, Redis ON | 100 | 169.9 | 378.9 | 1762.9 | 2492.3 | 2.0 % | 0.78 | 15 | 0 | 0.0 s |
| 1 instancia, Redis ON | 500 | 155.6 | 2172.5 | 7593.4 | 11200.7 | 14.0 % | 0.82 | 12 | 0 | 0.0 s |
| 1 instancia, Redis ON | 1000 | 159.7 | 4475.3 | 14011.6 | 17565.3 | 16.2 % | 0.82 | 10 | 0 | 0.0 s |
| 1 instancia, Redis ON | 5000 | 125.4 | 15925.7 | 31157.6 | 37278.5 | 0.0 % | 0.82 | 10 | 0 | 0.0 s |
| 3 instancias, Redis ON | 100 | 196.2 | 364.9 | 1408.8 | 2234.3 | 0.0 % | 0.81 | 20 | 0 | 0.0 s |
| 3 instancias, Redis ON | 500 | 203.6 | 1654.2 | 5952.0 | 8049.2 | 0.0 % | 0.83 | 20 | 0 | 0.0 s |
| 3 instancias, Redis ON | 1000 | 205.1 | 3284.7 | 10679.4 | 14420.5 | 0.0 % | 0.84 | 20 | 0 | 0.0 s |
| 3 instancias, Redis ON | 5000 | 137.7 | 19652.5 | 37250.1 | 40153.1 | 0.0 % | 0.83 | 20 | 0 | 0.0 s |
| 1 instancia, Redis OFF | 100 | 149.6 | 462.6 | 1874.6 | 2912.6 | 0.9 % | — | 15 | 0 | 0.0 s |
| 1 instancia, Redis OFF | 500 | 147.8 | 2359.9 | 7791.7 | 11346.4 | 18.5 % | — | 13 | 0 | 0.0 s |
| 1 instancia, Redis OFF | 1000 | 125.8 | 4679.6 | 13731.1 | 17544.6 | 5.4 % | — | 11 | 0 | 0.0 s |
| 1 instancia, Redis OFF | 5000 | 141.2 | 19572.5 | 43163.6 | 47608.8 | 5.0 % | — | 11 | 0 | 0.0 s |
| 3 instancias, Redis OFF | 100 | 192.7 | 362.6 | 1417.6 | 2262.4 | 0.0 % | — | 20 | 0 | 0.0 s |
| 3 instancias, Redis OFF | 500 | 123.8 | 2576.6 | 8022.5 | 11441.4 | 0.1 % | — | 27 | 0 | 0.0 s |
| 3 instancias, Redis OFF | 1000 | 209.3 | 3163.3 | 11176.3 | 14735.3 | 0.0 % | — | 20 | 0 | 0.0 s |
| 3 instancias, Redis OFF | 5000 | 109.9 | 31068.9 | 51433.1 | 54462.0 | 0.0 % | — | 20 | 0 | 0.0 s |

- 1 instancia, Redis ON, 100 usuarios: 503: 51, ReadError: 1
- 1 instancia, Redis ON, 500 usuarios: 503: 327
- 1 instancia, Redis ON, 1000 usuarios: 503: 387
- 1 instancia, Redis OFF, 100 usuarios: 503: 21
- 1 instancia, Redis OFF, 500 usuarios: RemoteProtocolError: 1, 503: 410
- 1 instancia, Redis OFF, 1000 usuarios: 503: 102
- 1 instancia, Redis OFF, 5000 usuarios: 503: 106
- 3 instancias, Redis OFF, 500 usuarios: RemoteProtocolError: 2

**Columnas.** *Errores*: respuestas 5xx, tiempos vencidos y conexiones cortadas sobre el total.
*Conexiones BD (máx.)*: pico de `pg_stat_activity` durante el nivel. *Esperas de bloqueo*: pico de
`pg_locks` sin conceder. *Recuperación*: lo que tardó `/health` en volver a responder al terminar el nivel.

## Hallazgo 1: la API no se recuperaba después de la carga

| Escenario | 1 instancia, Redis ON |
|---|---|
| Síntoma | Con 500 usuarios, 94 % de las peticiones vencían y `/health` no respondía durante minutos después de la carga |
| Evidencia | `pg_stat_activity`: las conexiones del pool quedaban `idle in transaction` y la API casi sin CPU |
| Causa | La sesión se cerraba en el teardown de una dependencia síncrona, que necesita un hilo libre del threadpool; con todos los hilos esperando una conexión, nadie las devolvía |
| Corrección | `get_db` asíncrono, pool con espera corta y 503 con `Retry-After`, límite de peticiones en curso por instancia |

Las dos corridas usan un solo producto (todas las peticiones sobre la misma fila); la matriz de arriba
ya incluye este ajuste y el siguiente.

| Momento | Usuarios | req/s | P50 ms | P95 ms | Errores | Detalle de errores | Recuperación |
|---|---:|---:|---:|---:|---:|---|---:|
| Antes | 100 | 144.3 | 484.5 | 1911.2 | 0.0 % | — | 0.0 s |
| Antes | 500 | 51.3 | 10105.9 | 10651.0 | 93.6 % | timeout: 721 | 180.5 s |
| Antes | 1000 | 67.2 | 10396.9 | 10665.9 | 100.0 % | timeout: 1008 | 180.5 s |
| Después | 100 | 151.8 | 465.9 | 1790.9 | 0.0 % | — | 0.0 s |
| Después | 500 | 154.0 | 2416.2 | 7231.7 | 25.8 % | 503: 594, ReadError: 1 | 0.0 s |
| Después | 1000 | 122.3 | 5434.0 | 15950.1 | 15.3 % | 503: 280 | 0.0 s |

La recuperación "antes" es el tope de espera del generador (180 s): la API seguía sin responder.

## Hallazgo 2: sin Redis las conexiones quedaban tomadas sin uso

| Escenario | 1 instancia, Redis OFF, 100 usuarios |
|---|---|
| Síntoma | 36 % de errores (pool agotado) sin esperas de bloqueo en PostgreSQL |
| Causa | En un endpoint síncrono FastAPI serializa la respuesta en otro paso del threadpool y cierra la dependencia después; la conexión seguía tomada aunque la consulta ya había terminado |
| Corrección | La consulta de saldo termina su transacción apenas lee y `expire_on_commit=False` evita volver a la base después del commit |

| Momento | Usuarios | req/s | P50 ms | P95 ms | Errores | Detalle de errores | Recuperación |
|---|---:|---:|---:|---:|---:|---|---:|
| Antes | 100 | 65.8 | 255.9 | 10001.3 | 35.9 % | 503: 276, timeout: 78 | 0.0 s |
| Después | 100 | 136.9 | 539.3 | 1978.5 | 0.1 % | 503: 1, RemoteProtocolError: 1, ReadError: 1 | 0.0 s |

## Redis ON contra OFF: consultas a la base

Una instancia, 100 usuarios, 15 s. Las consultas salen de `db_query_duration_seconds` de
`/metrics/prometheus` antes y después de la carga.

| Redis | req/s | Hit ratio | Consultas a PostgreSQL | Respuestas correctas | Consultas por respuesta | Tiempo de base por respuesta (ms) |
|---|---:|---:|---:|---:|---:|---:|
| ON | 180.0 | 0.82 | 2248 | 2652 | 0.85 | 2.2 |
| OFF | 156.5 | — | 4240 | 2334 | 1.82 | 3.8 |

## Lectura de los resultados

- **1 contra 3 instancias.** Con Redis, a 500 usuarios el throughput pasa de 155,6 a 203,6 req/s y los
  errores de 14 % a 0 %; a 1.000 usuarios, de 159,7 a 205,1 req/s y de 16,2 % a 0 %. No se triplica
  porque las tres instancias, PostgreSQL, Redis, RabbitMQ y el generador comparten 2 vCPU.
- **Redis.** Entre el 78 % y el 84 % de las lecturas se respondieron sin tocar PostgreSQL. Cada
  respuesta necesitó la mitad de consultas a la base y menos tiempo de base (tabla anterior). Con una
  instancia el límite es la CPU de la API y la diferencia de throughput es pequeña. Con tres instancias
  la base es compartida y ahí pesa: a 500 usuarios, 203,6 req/s con Redis contra 123,8 y
  148,7 req/s sin Redis (dos corridas). A 1.000 usuarios no apareció la diferencia (205,1 contra
  209,3): en una máquina compartida las corridas varían.
- **Errores.** 503 rápidos (límite de peticiones en curso o pool agotado) y alguna conexión cortada
  aislada; ningún 500 ni tiempo vencido, y `/health` respondió de inmediato al terminar cada nivel
  (antes del primer ajuste no respondía en más de 3 minutos).
- **Bloqueos.** Sin esperas de bloqueo con las entradas repartidas en 12 productos.
  Las entradas de un mismo producto se serializan por `SELECT … FOR UPDATE`; es lo que protege el saldo.
- **5.000 usuarios.** Ninguna configuración los atiende en esta máquina; el generador tampoco alcanza a
  sostener esa concurrencia y la latencia mide sobre todo la espera. Lo importante es que el sistema no se
  cae y se recupera solo.
- **Cuello de botella.** Primero la CPU de cada proceso de la API (Python usa un núcleo por proceso); con
  varias instancias, la base compartida. Se escala con más instancias detrás del balanceador y con el
  caché, que es lo que hace WH-002 en AWS (dos t3.small detrás de un ALB).
