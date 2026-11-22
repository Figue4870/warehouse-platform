WITH snapshots AS (
    SELECT DISTINCT snapshot_at, warehouse_id, sku, quantity, reserved_quantity, unit_supplier_cost
    FROM "${database}"."inventory"
    WHERE dt >= CAST(date_add('day', -${days}, current_date) AS varchar)
)
SELECT warehouse_id, sku, quantity, reserved_quantity,
       (quantity + reserved_quantity) * CAST(unit_supplier_cost AS decimal(14,2)) AS valor
FROM snapshots
WHERE snapshot_at = (SELECT max(snapshot_at) FROM snapshots)
ORDER BY warehouse_id, sku
