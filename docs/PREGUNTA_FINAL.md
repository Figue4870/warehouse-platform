# Pregunta final: 100 bodegas con cargas distintas

> Si la plataforma tuviera que atender 100 bodegas y cada bodega tuviera una carga diferente, ¿qué
> componentes escalarían independientemente y por qué?

## Respuesta

Escala de forma independiente todo lo que **no comparte estado con otra bodega** y todo lo que **lee
de su propia cola**. La arquitectura se diseñó para eso: cada bodega es un stack propio (ADR-003), la
comunicación entre partes es por colas (ADR-002) y la central solo consolida (ADR-006).

| Componente | Escala con | Cómo | Por qué es independiente |
|---|---|---|---|
| API de cada bodega | Peticiones de esa bodega | Más instancias detrás de su balanceador (`api_nodes` en Terraform) | Sin estado: el saldo vive en su PostgreSQL y el token se verifica con la clave pública |
| PostgreSQL de cada bodega | Escrituras y tamaño de esa bodega | Máquina más grande o réplica de lectura | Ninguna otra bodega lo usa |
| Redis de cada bodega | Lecturas de esa bodega | Memoria | Cache-Aside: si no alcanza, se lee la base |
| RabbitMQ y workers de cada bodega | Eventos de esa bodega | Réplicas del worker cuya cola crece | Cada cola es una responsabilidad; la profundidad dice cuál escalar |
| `central_forwarder` | Eventos de esa bodega hacia la central | Uno por bodega (ya es así) | Guarda y reenvía su propia cola |
| Projector central | Eventos de **todas** las bodegas | Réplicas consumiendo `central.projection` | El bloqueo por bodega permite procesar bodegas distintas en paralelo |
| Saga y `command_relay` | Transferencias | Réplicas (los comandos se toman con `SKIP LOCKED`) | No depende del número de bodegas sino de las transferencias |
| Reconciler y heartbeat | Número de bodegas | Repartir las bodegas entre instancias | Una pregunta cada 15 s por bodega |
| API central y tablero | Usuarios de la central (pocos) | Instancias tras un balanceador | Lee el modelo de lectura |
| Batch | Volumen del día | Por bodega en paralelo si el día crece | Una ejecución por día, reejecutable |
| Lambda | Archivos subidos | La escala AWS | Por evento |
| Ingesta IoT y sus workers | Sensores | Un gateway por sitio; workers por cola | La telemetría no depende de las ventas |

## Qué dicen las mediciones

Fase 11 (`docs/fases/FASE_11.md`):

- Con **una instancia** de API, la latencia crece antes de que la base se sature: el primer límite de
  una bodega es la API. Sumar instancias es la primera palanca y no afecta a las demás bodegas.
- **Redis** baja la carga de la base en las lecturas; con muchas escrituras sobre el mismo producto la
  tasa de aciertos baja, pero el saldo nunca queda obsoleto.
- Las colas absorben picos: durante la carga el outbox y las colas crecen y se vacían solas después.

Por eso, con 100 bodegas de cargas diferentes:

- Una bodega de **20 req/s** cabe en una t3.small con una instancia de API.
- Una de **800 req/s** usa nodos de API adicionales tras un ALB (como WH-002).
- Una de **2000 req/s** suma instancias de API, una réplica de lectura de su base y más workers; las
  otras 99 no cambian.

## Qué no escala sin cambios

| Punto | Con 100 bodegas | Cambio |
|---|---|---|
| Una sola máquina para la central | 100 forwarders y 100 heartbeats | Separar projector, Saga y API en máquinas o contenedores distintos; varios projectors |
| Base central única | El modelo de lectura crece con todas las bodegas | Particionar `movement_history` por fecha; réplica de lectura para el tablero |
| Un RabbitMQ central | Tráfico de las 100 bodegas | Cluster de RabbitMQ o colas por grupo de bodegas |
| Una EC2 por bodega | 100 máquinas, fuera del Learner Lab | Varias bodegas pequeñas por máquina (el compose ya separa por proyecto y puerto) o un orquestador de contenedores |
| Heartbeat secuencial | 100 preguntas cada 15 s | Varias instancias del heartbeat con bodegas asignadas |

## En una frase

Cada bodega escala sola porque no comparte nada con las demás; la central escala por función porque
cada función lee de su propia cola; y la evidencia muestra que el primer recurso que se agota en una
bodega es su API, que se escala agregando instancias sin tocar a las otras 99.
