WITH movements AS (
    SELECT DISTINCT event_id, warehouse_id, type, quantity
    FROM "${database}"."movements"
    WHERE dt >= CAST(date_add('day', -${days}, current_date) AS varchar)
)
SELECT warehouse_id, type, count(*) AS movimientos, sum(quantity) AS unidades
FROM movements
GROUP BY warehouse_id, type
ORDER BY warehouse_id, type
