# ADR-019 — Central en AWS: base en RDS y dos nodos de API detrás de un ALB

## Contexto

La central era una sola máquina con todo adentro: si se reiniciaba, el tablero, el inicio de sesión y
las transferencias quedaban fuera de servicio, y su base vivía en el disco de esa máquina. El enunciado
y el PDF del equipo nombran RDS, y la pregunta de defensa 27 compara RDS con PostgreSQL local.

## Alternativas

- Todo en un contenedor de la máquina de la central (sin costo extra, un punto único de falla).
- Un RDS compartido por las bodegas y la central (lo que hizo otro equipo): más barato que uno por
  bodega, pero si cae, caen todas las bodegas, y contradice la autonomía (ADR-003).
- **RDS solo para la central** y un segundo nodo de la API detrás de un ALB.

## Decisión

- `aws_db_instance.central`: PostgreSQL 15, `db.t3.micro`, 20 GB gp3 cifrado, privado (solo desde las
  máquinas de la central), single-AZ y sin respaldos (laboratorio). La clave se deriva del secreto
  maestro; el nodo principal crea las tablas con `central_schema.sql` (idempotente) antes de arrancar.
- `module.central_api_node`: una t3.small que levanta solo la API (`start-api central`), con la misma
  configuración que el nodo principal y su RabbitMQ y su directorio por la red privada.
- `module.central_load_balancer`: ALB en el puerto 8090 con chequeo `/health` sobre los dos nodos. La
  Lambda avisa por el DNS del ALB, que no cambia al reiniciar una máquina.
- Las bodegas no cambian: cada una conserva su base en su máquina.
- `central_database = "contenedor"` y `central_api_nodes = 0` vuelven a la central de una sola máquina.

## Consecuencias

- Si cae una máquina de la API central, el ALB deja de enviarle tráfico y el tablero sigue.
- La clave de firma de los tokens vive en la base: los dos nodos firman con la misma.
- El nodo principal sigue siendo único para RabbitMQ, el directorio y los workers; si cae, el nodo de
  API sirve consultas e inicio de sesión ya emitido, pero no recibe eventos nuevos hasta que vuelva.
- Costo extra aproximado: 0,018 USD/h del RDS, 0,021 USD/h de la segunda máquina y ~0,03 USD/h del ALB.
- Sin respaldos: si el RDS se pierde, el modelo de lectura se reconstruye desde las bodegas (Fase 7);
  usuarios, contratos y transferencias se pierden (en producción: respaldos y Multi-AZ).
