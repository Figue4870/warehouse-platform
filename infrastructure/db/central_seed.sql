-- Datos iniciales de la central (solo desarrollo): usuarios por rol, bodegas registradas y catálogo universal.
-- Contraseña de todos los usuarios de desarrollo: Bodegas2026!
BEGIN;

INSERT INTO users (username, display_name, role, warehouse_id, password_hash, created_by) VALUES
    ('admin.global',      'Administración global', 'ADMIN_GLOBAL', NULL,     'scrypt$imuQlnNkVXdTJs2EHnASPw==$fd7XB6ddnuRpheBdckaT0c+FaWF2uYnfpjMwenJ6B44=', 'seed'),
    ('lider',             'Líder de operaciones',  'LIDER',        NULL,     'scrypt$imuQlnNkVXdTJs2EHnASPw==$fd7XB6ddnuRpheBdckaT0c+FaWF2uYnfpjMwenJ6B44=', 'seed'),
    ('admin.wh001',       'Administrador WH-001',  'ADMIN_BODEGA', 'WH-001', 'scrypt$imuQlnNkVXdTJs2EHnASPw==$fd7XB6ddnuRpheBdckaT0c+FaWF2uYnfpjMwenJ6B44=', 'seed'),
    ('registrador.wh001', 'Registrador WH-001',    'REGISTRADOR',  'WH-001', 'scrypt$imuQlnNkVXdTJs2EHnASPw==$fd7XB6ddnuRpheBdckaT0c+FaWF2uYnfpjMwenJ6B44=', 'seed'),
    ('admin.wh002',       'Administrador WH-002',  'ADMIN_BODEGA', 'WH-002', 'scrypt$imuQlnNkVXdTJs2EHnASPw==$fd7XB6ddnuRpheBdckaT0c+FaWF2uYnfpjMwenJ6B44=', 'seed'),
    ('registrador.wh002', 'Registrador WH-002',    'REGISTRADOR',  'WH-002', 'scrypt$imuQlnNkVXdTJs2EHnASPw==$fd7XB6ddnuRpheBdckaT0c+FaWF2uYnfpjMwenJ6B44=', 'seed'),
    ('admin.wh003',       'Administrador WH-003',  'ADMIN_BODEGA', 'WH-003', 'scrypt$imuQlnNkVXdTJs2EHnASPw==$fd7XB6ddnuRpheBdckaT0c+FaWF2uYnfpjMwenJ6B44=', 'seed'),
    ('registrador.wh003', 'Registrador WH-003',    'REGISTRADOR',  'WH-003', 'scrypt$imuQlnNkVXdTJs2EHnASPw==$fd7XB6ddnuRpheBdckaT0c+FaWF2uYnfpjMwenJ6B44=', 'seed')
ON CONFLICT (username) DO NOTHING;

INSERT INTO warehouses (warehouse_id, nit, name, location, address, description, capacity_units, admin_username,
                        registered_by, registered_at) VALUES
    ('WH-001', '900100001-1', 'Bodega Norte',    'Cali',     'Calle 70 Norte # 2A-15',      'Distribución local y EPP', 12000, 'admin.wh001', 'seed', now()),
    ('WH-002', '900100002-2', 'Centro Logístico', 'Yumbo',    'Km 3 vía Cali-Yumbo, Bodega 7', 'Alta rotación (800 req/s)', 40000, 'admin.wh002', 'seed', now()),
    ('WH-003', '900100003-3', 'Bodega Sur',      'Jamundí',  'Carrera 10 # 5-40',           'Herramientas y repuestos', 8000, 'admin.wh003', 'seed', now())
ON CONFLICT (warehouse_id) DO NOTHING;

INSERT INTO universal_products (sku, name, aliases, description, category, unit, supplier,
                                supplier_cost, retail_price, wholesale_price, created_by) VALUES
    ('EPP-CASCO-001',  'Casco de seguridad blanco', '["Casco dieléctrico", "Hard hat"]', 'Casco dieléctrico clase E', 'EPP',
     'UNIDAD', 'Seguridad Industrial del Valle', 18000, 32000, 26000, 'seed'),
    ('EPP-GUANTE-002', 'Guantes de nitrilo (caja x100)', '["Guantes desechables"]', 'Talla M', 'EPP',
     'CAJA', 'Distribuciones Médicas SAS', 21000, 38000, 31000, 'seed'),
    ('HER-TALADRO-003', 'Taladro percutor 1/2"', '["Taladro de impacto"]', '750 W, con maletín', 'Herramientas',
     'UNIDAD', 'Ferretería Mayorista Andina', 180000, 289000, 245000, 'seed'),
    ('LOG-CINTA-004', 'Cinta de embalaje transparente', '["Cinta adhesiva"]', '48 mm x 100 m', 'Logística',
     'ROLLO', 'Empaques del Pacífico', 3500, 7000, 5200, 'seed'),
    ('LOG-ESTIBA-005', 'Estiba plástica 120x100', '["Pallet plástico"]', 'Carga dinámica 1.500 kg', 'Logística',
     'UNIDAD', 'Plásticos Industriales Cali', 95000, 150000, 128000, 'seed'),
    ('EPP-GAFAS-006', 'Gafas de seguridad claras', '["Monogafas"]', 'Antiempañante, UV400', 'EPP',
     'UNIDAD', 'Seguridad Industrial del Valle', 6500, 12900, 10500, 'seed')
ON CONFLICT (sku) DO NOTHING;

COMMIT;
