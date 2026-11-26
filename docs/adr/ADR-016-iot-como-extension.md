# ADR-016 — IoT como extensión independiente

## Contexto

La Fase 12 agrega sensores (temperatura, humedad, movimiento, apertura y energía) con duplicados,
desorden, pérdida de conexión y alto volumen. El principio 10 de la arquitectura de referencia pide que
IoT sea una extensión.

## Alternativas

- Recibir las lecturas en la API de cada bodega (mezcla telemetría de alto volumen con el inventario).
- Un servicio administrado (AWS IoT Core): otra dependencia de la nube que no se reproduce en local.
- Un stack propio: broker MQTT, gateway de borde, ingesta y workers, con su propia base.

## Decisión

RabbitMQ con el plugin MQTT como broker (una tecnología que el equipo ya opera). El gateway quita
duplicados por `sequence_number`, marca las lecturas atrasadas y guarda en un buffer en disco antes de
reenviar a la ingesta. La ingesta valida y publica en `iot.events`; tres colas (almacenamiento,
alertas, analítica) consumen la misma lectura. Las alertas llegan a la central con un aviso firmado.

## Consecuencias

- La telemetría escala y falla sin tocar el inventario; la central muestra los sensores si el servicio
  responde y sigue igual si no.
- En AWS es una EC2 opcional (`enable_iot`), dentro del límite de 9 instancias.
- Las reglas de alerta siguen el orden de las secuencias; una lectura atrasada se guarda pero no
  cambia una alerta.
