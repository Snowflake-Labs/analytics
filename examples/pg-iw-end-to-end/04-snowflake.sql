/*
    Run on Snowflake
*/

USE WAREHOUSE COMPUTE_WH;
USE ROLE SYSADMIN;

USE DATABASE DEMODB_PGMIRROR;
USE SCHEMA DEMO;

/*
    Query the semantic view, checking for the new devices
*/
SELECT *
FROM SEMANTIC_VIEW(
    DEMO.IOT_SEMANTIC_VIEW
    DIMENSIONS DEVICES.DEVICE_NAME, SENSORS.SENSOR_TYPE
    METRICS READINGS.AVG_VALUE
)
WHERE DEVICE_NAME ILIKE 'Demo-New%'
ORDER BY DEVICE_NAME;


/*
    Create an agent
*/
CREATE OR REPLACE AGENT DEMO.IOT_AGENT_DEMO
  COMMENT = 'Agent for querying IoT devices, sensors, and readings'
  PROFILE = '{
    "display_name": "IoT Data Assistant (Demo)",
    "color": "blue"
  }'
  FROM SPECIFICATION
  $$
  models:
    orchestration: auto

  instructions:
    response: |
      Answer questions about devices, sensors, and sensor readings.
      Use the IoT semantic view for structured data questions.
      Use AVG_VALUE for average sensor readings.
      Use READING_COUNT to count readings.
      Use READINGS.TS for time-based questions.

  tools:
    - tool_spec:
        type: cortex_analyst_text_to_sql
        name: IoTAnalyst
        description: >
          Answers structured questions about IoT devices, sensors,
          and sensor readings using the IoT semantic view.

  tool_resources:
    IoTAnalyst:
      semantic_view: DEMODB_PGMIRROR.DEMO.IOT_SEMANTIC_VIEW
      execution_environment:
        type: warehouse
        warehouse: DEMO_IW
      sql_gen_mode: strict
      query_timeout: 60
  $$;

DESCRIBE AGENT DEMO.IOT_AGENT_DEMO;

/*
    Query the agent from SQL using DATA_AGENT_RUN
*/
SELECT TRY_PARSE_JSON(
    SNOWFLAKE.CORTEX.DATA_AGENT_RUN(
        'DEMODB_PGMIRROR.DEMO.IOT_AGENT_DEMO',
        $${ "messages": [{ "role": "user", "content": [{ "type": "text", "text": "What is the average temperature reading for each device location?" }] }] }$$,
        TRUE
    )
) AS resp;

SELECT TRY_PARSE_JSON(
    SNOWFLAKE.CORTEX.DATA_AGENT_RUN(
        'DEMODB_PGMIRROR.DEMO.IOT_AGENT_DEMO',
        $${ "messages": [{ "role": "user", "content": [{ "type": "text", "text": "How many readings do we have for the new demo devices?" }] }] }$$,
        TRUE
    )
) AS resp;

/*
    You can also use the agent from the "Agent Studio"
*/