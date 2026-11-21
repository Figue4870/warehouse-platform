# Fases 4 y 5: bodegas independientes, balanceo de carga y Terraform

## Objetivo

- **Fase 4:** tres bodegas (WH-001, WH-002, WH-003), cada una con su API, PostgreSQL, Redis y
  RabbitMQ, sin compartir bases. Si una se cae, las demás siguen.
- **Fase 5:** varias instancias de la API detrás de un balanceador, con una API *stateless*.
- **Nube:** Terraform crea una EC2 por bodega en el AWS Learner Lab.

## Una bodega por dentro

```text
                 navegador / clientes
                         |
                  lb (Nginx :80)  -> round-robin, reintenta en otra instancia si una falla
              +----------+----------+
            api_1      api_2      api_3       (idénticas y stateless)
              +----------+----------+
                         |
          PostgreSQL + Redis + RabbitMQ + workers (Fase 3)
```

Todas las bodegas usan la misma plantilla `docker/composes/compose-warehouse.yml`; lo que cambia es
su `.env` (`docker/components/wh_00X/.env`). Cada una es un proyecto de Docker aparte (`-p wh-001`),
con su propia red y volúmenes.

## Cómo levantarlas

```powershell
.\manage.ps1 start all        # Linux / EC2: ./manage_warehouse.sh start all
.\manage.ps1 status
.\manage.ps1 stop wh_002      # apaga solo WH-002 (sin borrar datos)
.\manage.ps1 start wh_002
python scripts/new_warehouse.py WH-004   # agrega otra bodega copiando la última y moviendo sus puertos
```

| Bodega | Web | API (balanceador) | PostgreSQL | Redis | RabbitMQ |
|---|---|---|---|---|---|
| WH-001 | :8081 | :8001 | :5433 | :6380 | :15673 |
| WH-002 | :8082 | :8002 | :5434 | :6381 | :15674 |
| WH-003 | :8083 | :8003 | :5435 | :6382 | :15675 |

## Experimentos de la Fase 4

**WH-002 caída.** Con `tests/load/availability_probe.py` consultando las tres bodegas 10 veces por
segundo, se apagó WH-002 a los 6 s: WH-001 y WH-003 respondieron el 100 % de las peticiones; WH-002
solo mientras estuvo encendida.

```powershell
python tests/load/availability_probe.py --target WH-001=http://localhost:8001 --target WH-002=http://localhost:8002 --target WH-003=http://localhost:8003 --seconds 40
.\manage.ps1 stop wh_002      # en otra terminal, mientras corre la sonda
```

**Carga distinta por bodega** (`tests/load/multi_warehouse_load.py`, 30 s):

| Bodega | Pedido | Logrado | P95 | Errores |
|---|---:|---:|---:|---:|
| WH-001 | 50 req/s | 50,1 | 24 ms | 0 |
| WH-002 | 800 req/s | 596 | 78 ms | 0 |
| WH-003 | 20 req/s | 20,1 | 22 ms | 0 |

WH-002 no llegó a 800 req/s: es la que debe escalar, y se puede hacer sin tocar a las otras (por
ejemplo, en Terraform WH-002 usa una máquina más grande, `t3.medium`).

**¿Por qué no obligar a todas a escalar igual?** Se pagaría para WH-003 (20 req/s) la capacidad que
necesita WH-002 (800 req/s). Cada bodega escala según su carga.

## Experimentos de la Fase 5

| Experimento | Resultado |
|---|---|
| 1 instancia vs 3 (4.000 lecturas, 20 usuarios) | 628 req/s con 1 y 786 req/s con 3; el reparto fue parejo (1333/1333/1334). No se triplicó porque todo corría en una máquina de 2 núcleos |
| Apagar `api_2` con tráfico (20 req/s, 26 s) | 520 de 520 peticiones correctas; las otras dos absorbieron la carga y `api_2` volvió sola |
| 30 entradas con `api_3` apagada | 30 respuestas 201 y saldo final exacto de 30 |

```powershell
python tests/load/benchmark_cache.py --url http://localhost:8001 --label 3-instancias
docker stop warehouse_api_2_WH-001 warehouse_api_3_WH-001
python tests/load/benchmark_cache.py --url http://localhost:8001 --label 1-instancia
docker start warehouse_api_2_WH-001 warehouse_api_3_WH-001
```

## Preguntas de la guía

**¿Qué significa que la API sea stateless?** Ninguna instancia guarda en memoria algo que las demás
necesiten: inventario, reportes y outbox están en PostgreSQL, el caché en Redis y el trabajo pendiente
en RabbitMQ. Cualquier instancia puede atender cualquier petición.

**¿Qué pasa si Redis es local de cada instancia?** Una invalidación en `api_1` no borraría la copia de
`api_2` y `api_3`: el usuario vería saldos distintos según la instancia, habría más MISS y el triple de
memoria. Por eso hay un Redis por bodega compartido por sus APIs.

**¿Dónde vive el estado compartido?** En PostgreSQL (la verdad), Redis (copias rápidas) y RabbitMQ.

**¿Por qué una base por bodega?** Autonomía (cada una opera aunque otra esté caída), aislamiento y
escala independiente. Una base única sería un punto único de falla.

## Terraform (AWS Learner Lab)

`terraform/` crea un security group y una EC2 Ubuntu por bodega. Al arrancar, cada máquina instala
Docker, clona el repositorio y ejecuta `./manage_warehouse.sh start wh_00X`, lo mismo que se hizo a
mano en el laboratorio. PostgreSQL, Redis y AMQP no se abren a Internet. Usa la llave `vockey` y el
perfil `LabInstanceProfile` (el Learner Lab no deja crear roles). Pasos en `terraform/README.md`.

## Integración continua

`.github/workflows/ci.yml` corre en cada push: pruebas unitarias, validación de los compose, de Nginx
y de Terraform, y levanta WH-001 con Docker para las pruebas de integración.

## Pruebas

```powershell
pytest tests/unit                                   # sin Docker
pytest tests/integration                            # con WH-001 levantada (incluye el balanceador)
```
