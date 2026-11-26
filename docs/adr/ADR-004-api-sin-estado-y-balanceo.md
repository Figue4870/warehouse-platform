# ADR-004 — API sin estado detrás de un balanceador

## Contexto

Una sola instancia de la API no alcanza la carga de la bodega de alta demanda y es un punto único de
falla.

## Alternativas

- Escalar verticalmente la máquina.
- Varias instancias con sesión pegajosa.
- Varias instancias sin estado detrás de un balanceador.

## Decisión

API sin estado (el estado vive en PostgreSQL y Redis; el token se verifica con la clave pública). En
Docker, Nginx reparte entre tres instancias; en AWS, un ALB reparte entre el nodo principal y nodos de
API (`api_nodes`).

## Consecuencias

- Se agrega capacidad sumando instancias; la cabecera `X-Instance` muestra quién respondió.
- PostgreSQL de la bodega pasa a ser el siguiente cuello de botella (ver Fase 11).
- Los retos del heartbeat se recuerdan por instancia: una repetición que llega a otra instancia se
  responde. Se acepta porque la respuesta va cifrada con la clave de la bodega (quien repite no puede
  leerla) y el reto vence a los 60 s; compartir la memoria de retos en Redis cerraría ese caso.
