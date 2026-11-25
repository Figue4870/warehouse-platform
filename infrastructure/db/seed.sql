
\if :{?wh}
\else
  \set wh WH-001
\endif

BEGIN;

WITH new_products AS (
    INSERT INTO products (sku, name, description, category, unit, supplier, lot,
                          supplier_cost, retail_price, wholesale_price)
    VALUES
        ('EPP-CASCO-001',  'Casco de seguridad blanco', 'Casco dieléctrico clase E', 'EPP', 'UNIDAD',
         'Seguridad Industrial del Valle', 'L-2026-10-A', 18000, 32000, 26000),
        ('EPP-GUANTE-002', 'Guantes de nitrilo (caja x100)', 'Talla M', 'EPP', 'CAJA',
         'Distribuciones Médicas SAS', 'L-2026-09-B', 21000, 38000, 31000),
        ('HER-TALADRO-003', 'Taladro percutor 1/2"', '750 W, con maletín', 'Herramientas', 'UNIDAD',
         'Ferretería Mayorista Andina', 'L-2026-08-C', 180000, 289000, 245000),
        ('LOG-CINTA-004', 'Cinta de embalaje transparente', '48 mm x 100 m', 'Logística', 'ROLLO',
         'Empaques del Pacífico', 'L-2026-10-D', 3500, 7000, 5200),
        ('LOG-ESTIBA-005', 'Estiba plástica 120x100', 'Carga dinámica 1.500 kg', 'Logística', 'UNIDAD',
         'Plásticos Industriales Cali', 'L-2026-07-E', 95000, 150000, 128000)
    ON CONFLICT (sku) DO NOTHING
    RETURNING id, sku, supplier_cost, supplier
),
initial_stock (sku, qty) AS (
    VALUES ('EPP-CASCO-001', 50), ('EPP-GUANTE-002', 120), ('HER-TALADRO-003', 8),
           ('LOG-CINTA-004', 300), ('LOG-ESTIBA-005', 0)
),
new_inventory AS (
    INSERT INTO inventory (product_id, warehouse_id, quantity, reserved_quantity)
    SELECT np.id, :'wh', s.qty, 0
    FROM new_products np
    JOIN initial_stock s USING (sku)
    RETURNING product_id, quantity
)
INSERT INTO inventory_movements (product_id, warehouse_id, type, quantity, quantity_after, reserved_after, reference,
                                 unit_price, counterparty, created_by)
SELECT ni.product_id, :'wh', 'RECEIPT', ni.quantity, ni.quantity, 0, 'SEED-INVENTARIO-INICIAL', np.supplier_cost, np.supplier, 'seed'
FROM new_inventory ni JOIN new_products np ON np.id = ni.product_id
WHERE ni.quantity > 0;

INSERT INTO inventory_lots (product_id, warehouse_id, lot, quantity)
SELECT p.id, :'wh', p.lot, i.quantity
FROM products p JOIN inventory i ON i.product_id = p.id
WHERE p.lot IS NOT NULL AND i.quantity > 0
ON CONFLICT (product_id, warehouse_id, lot) DO NOTHING;

-- Copia del catálogo universal (la misma que siembra infrastructure/db/central_seed.sql en la central)
INSERT INTO universal_products (sku, name, aliases, description, category, unit, supplier,
                                supplier_cost, retail_price, wholesale_price)
VALUES
    ('EPP-CASCO-001',  'Casco de seguridad blanco', 'Casco dieléctrico|Hard hat', 'Casco dieléctrico clase E', 'EPP',
     'UNIDAD', 'Seguridad Industrial del Valle', 18000, 32000, 26000),
    ('EPP-GUANTE-002', 'Guantes de nitrilo (caja x100)', 'Guantes desechables', 'Talla M', 'EPP',
     'CAJA', 'Distribuciones Médicas SAS', 21000, 38000, 31000),
    ('HER-TALADRO-003', 'Taladro percutor 1/2"', 'Taladro de impacto', '750 W, con maletín', 'Herramientas',
     'UNIDAD', 'Ferretería Mayorista Andina', 180000, 289000, 245000),
    ('LOG-CINTA-004', 'Cinta de embalaje transparente', 'Cinta adhesiva', '48 mm x 100 m', 'Logística',
     'ROLLO', 'Empaques del Pacífico', 3500, 7000, 5200),
    ('LOG-ESTIBA-005', 'Estiba plástica 120x100', 'Pallet plástico', 'Carga dinámica 1.500 kg', 'Logística',
     'UNIDAD', 'Plásticos Industriales Cali', 95000, 150000, 128000),
    ('EPP-GAFAS-006', 'Gafas de seguridad claras', 'Monogafas', 'Antiempañante, UV400', 'EPP',
     'UNIDAD', 'Seguridad Industrial del Valle', 6500, 12900, 10500)
ON CONFLICT (sku) DO NOTHING;

COMMIT;

SELECT p.id, p.sku, p.name, i.warehouse_id, i.quantity AS disponible, i.reserved_quantity AS reservado
FROM products p JOIN inventory i ON i.product_id = p.id
ORDER BY p.id;
