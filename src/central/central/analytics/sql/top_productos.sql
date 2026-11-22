WITH sales AS (
    SELECT DISTINCT event_id, sku, quantity
    FROM "${database}"."movements"
    WHERE type = 'SHIPMENT'
      AND dt >= CAST(date_add('day', -${days}, current_date) AS varchar)
)
SELECT sku, sum(quantity) AS unidades
FROM sales
GROUP BY sku
ORDER BY unidades DESC, sku
LIMIT 10
