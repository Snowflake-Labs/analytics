/*
    Run on Postgres, after mirroring is enabled
*/

/*
    Insert  additional readings with varied logic
*/
INSERT INTO demo.readings (sensor_id, value, ts)
SELECT
    s.sensor_id,
    CASE
        WHEN s.sensor_type = 'Temperature' THEN (20 + (random() * 15))::numeric(10,2) -- Temp: 20-35°C
        ELSE (40 + (random() * 50))::numeric(10,2)                                -- Humidity: 40-90%
    END as value,
    -- Spreads the data over the last 24 hours
    NOW() - (random() * (24 * 60) * '1 minute'::interval) as ts
FROM demo.sensors s
CROSS JOIN generate_series(1, 50) 
ORDER BY random();

/*
    Create a couple of new devices
*/
INSERT INTO demo.devices (device_name, location)
VALUES
    ('Demo-NewDevice-1', 'Demo-Lab'),
    ('Demo-NewDevice-2', 'Demo-Office');

INSERT INTO demo.sensors (device_id, sensor_type, unit)
SELECT d.device_id, s.sensor_type, s.unit
FROM demo.devices d
CROSS JOIN (
    VALUES
        ('Temperature', 'Celsius'),
        ('Humidity', 'Percent')
) AS s(sensor_type, unit)
WHERE d.device_name IN ('Demo-NewDevice-1', 'Demo-NewDevice-2');

INSERT INTO demo.readings (sensor_id, value, ts)
SELECT
    s.sensor_id,
    CASE
        WHEN s.sensor_type = 'Temperature' THEN round((20 + random() * 15)::numeric, 2)
        ELSE round((40 + random() * 50)::numeric, 2)
    END,
    CURRENT_TIMESTAMP - (n || ' minutes')::interval
FROM demo.sensors s
JOIN demo.devices d ON d.device_id = s.device_id
CROSS JOIN generate_series(1, 1000) AS n
WHERE d.device_name IN ('Demo-NewDevice-1', 'Demo-NewDevice-2');

/*
    Take a look at the new data
*/
SELECT * FROM demo.devices
WHERE device_name 
ILIKE 'Demo-NewDevice%';

SELECT * FROM demo.sensors s
INNER JOIN demo.devices d ON s.device_id = d.device_id
WHERE device_name ILIKE 'Demo-NewDevice%';

SELECT * FROM demo.readings r
INNER JOIN demo.sensors s ON s.sensor_id = r.sensor_id
INNER JOIN demo.devices d ON s.device_id = d.device_id
WHERE device_name ILIKE 'Demo-NewDevice%'
ORDER BY ts DESC 
LIMIT 100;
