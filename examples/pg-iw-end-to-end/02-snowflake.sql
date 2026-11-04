/*
    Run on Snowflake
*/

USE WAREHOUSE COMPUTE_WH;
USE ROLE SYSADMIN;

/*
    Set permissions for mirroring to work
*/
GRANT APPLICATION ROLE snowflake.postgres_mirror_admin TO ROLE SYSADMIN;
GRANT USAGE ON POSTGRES INSTANCE "DEMOPG" TO APPLICATION snowflake;

/*
    Create mirror
 */
CALL snowflake.postgres.create_mirror(
    mirror_name         => 'iot_mirror',
    postgres_instance   => 'DEMOPG',
    postgres_database   => 'postgres',
    target_database     => 'DEMODB_PGMIRROR',
    postgres_tables     => ['demo.devices', 'demo.sensors', 'demo.readings'],
    postgres_schemas    => null,
    refresh_interval    => '10 seconds'
);

/*
    Check created mirror
*/
CALL SNOWFLAKE.POSTGRES.DESCRIBE_MIRROR('iot_mirror');
CALL SNOWFLAKE.POSTGRES.LIST_MIRRORED_TABLES('iot_mirror');

/*
    Allow to operate on created mirror database
    (as it is owned by the "snowflake" application)
*/
GRANT ALL PRIVILEGES ON DATABASE DEMODB_PGMIRROR TO ROLE SYSADMIN;
GRANT ALL PRIVILEGES ON SCHEMA DEMODB_PGMIRROR.DEMO TO ROLE SYSADMIN;

/*
    Now let's play with the demo database
*/
USE DATABASE DEMODB_PGMIRROR;
USE SCHEMA DEMO;

SELECT * FROM DEVICES;
SELECT * FROM SENSORS;

/*
    Create a semantic view on top of the mirrored tables
*/
CREATE OR REPLACE SEMANTIC VIEW IOT_SEMANTIC_VIEW
    TABLES (
            DEVICES PRIMARY KEY (DEVICE_ID),
            SENSORS PRIMARY KEY (SENSOR_ID),
            READINGS PRIMARY KEY (READING_ID)
    )
    RELATIONSHIPS (
            SENSORS_TO_DEVICES AS SENSORS(DEVICE_ID) REFERENCES DEVICES(DEVICE_ID),
            READINGS_TO_SENSORS AS READINGS(SENSOR_ID) REFERENCES SENSORS(SENSOR_ID)
    )
    FACTS (
            READINGS.VALUE AS VALUE
    )
    DIMENSIONS (
            DEVICES.DEVICE_ID AS DEVICE_ID,
            DEVICES.DEVICE_NAME AS DEVICE_NAME,
            DEVICES.LOCATION AS "LOCATION",
            DEVICES.CREATED_AT AS CREATED_AT,
            SENSORS.SENSOR_ID AS SENSOR_ID,
            SENSORS.DEVICE_ID AS DEVICE_ID,
            SENSORS.SENSOR_TYPE AS SENSOR_TYPE,
            SENSORS.UNIT AS UNIT,
            READINGS.READING_ID AS READING_ID,
            READINGS.SENSOR_ID AS SENSOR_ID,
            READINGS.TS AS TS
    )
    METRICS (
            READINGS.AVG_VALUE AS AVG(VALUE) COMMENT = 'Average sensor reading value',
            READINGS.READING_COUNT AS COUNT(READING_ID) COMMENT = 'Number of readings'
    )
    COMMENT = 'IoT device, sensor, and reading data for analyzing sensor readings by device and location.'
EXECUTE AS OWNER;

/*
    Query the semantic view
*/
SELECT *
FROM SEMANTIC_VIEW (
    DEMO.IOT_SEMANTIC_VIEW
    DIMENSIONS DEVICES.LOCATION
    METRICS READINGS.AVG_VALUE, READINGS.READING_COUNT
);

/*
    Now create an interactive warehouse
*/
CREATE OR REPLACE INTERACTIVE WAREHOUSE DEMO_IW
WAREHOUSE_SIZE = 'XSMALL',
FALLBACK_WAREHOUSE = 'COMPUTE_WH';

/*
    Attach the tables to help with cache warming
*/
ALTER WAREHOUSE DEMO_IW
    ADD TABLES (
        DEMO.DEVICES,
        DEMO.SENSORS,
        DEMO.READINGS
    );

/*
    Use the interactive warehouse
*/
USE WAREHOUSE DEMO_IW;

/*
    Query the semantic view
*/
SELECT *
FROM SEMANTIC_VIEW (
    DEMO.IOT_SEMANTIC_VIEW
    DIMENSIONS DEVICES.LOCATION
    METRICS READINGS.AVG_VALUE, READINGS.READING_COUNT
);

/*
    Now on postgres db add more devices, then come back here
    Use file 03-postgres.sql 
*/
