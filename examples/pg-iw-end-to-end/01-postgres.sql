/*
    Run on Postgres
    Sample assumes you have an instance named DEMOPG
*/

SELECT * FROM information_schema.tables
WHERE table_schema = 'demo';

CREATE SCHEMA IF NOT EXISTS demo;

/*
    Create devices tables
*/
CREATE TABLE demo.devices (
    device_id SERIAL PRIMARY KEY,
    device_name TEXT NOT NULL,
    location TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE demo.sensors (
    sensor_id SERIAL PRIMARY KEY,
    device_id INT REFERENCES demo.devices(device_id),
    sensor_type TEXT NOT NULL, -- e.g., 'Temperature', 'Humidity'
    unit TEXT
);

CREATE TABLE demo.readings (
    reading_id SERIAL PRIMARY KEY,
    sensor_id INT REFERENCES demo.sensors(sensor_id),
    value NUMERIC(10, 2),
    ts TIMESTAMP DEFAULT NOW()
);

/*
    Insert sample data for 10 devices,
    each one with 2 sensors
*/
INSERT INTO demo.devices (device_name, location)
SELECT
    'IoT-Gateway-' || i,
    CASE WHEN i % 2 = 0 THEN 'Warehouse-A' ELSE 'Loading-Dock' END
FROM generate_series(1, 10) AS i;

INSERT INTO demo.sensors (device_id, sensor_type, unit)
SELECT
    d.device_id,
    s.type,
    CASE WHEN s.type = 'Temperature' THEN 'Celsius' ELSE 'Percent' END
FROM demo.devices d
CROSS JOIN (SELECT unnest(ARRAY['Temperature', 'Humidity']) AS type) AS s;

INSERT INTO demo.readings (sensor_id, value, ts)
SELECT
    (sample_id % 20) + 1, -- Cycles through the 20 sensors
    (random() * 40 + 10)::numeric(10,2), -- Generates a value between 10 and 50
    NOW() - (sample_id || ' minutes')::interval -- Offsets time into the past
FROM generate_series(1, 1000) AS sample_id;

SELECT COUNT(*) FROM demo.devices;
SELECT COUNT(*) FROM demo.sensors;
SELECT COUNT(*) FROM demo.readings;

/*
    Enable mirroring
*/
CREATE EXTENSION IF NOT EXISTS snowflake_cdc CASCADE;

/*
    Now run the code on Snowflake, 
    using file 02-snowflake.sql
    then come back here
*/
