-- Teardown interactive tables (per scale factor)
USE ROLE SYSADMIN;
DROP SCHEMA IF EXISTS {{SOLUTION_NAME}}_BENCH_DB.{{SCHEMA_PREFIX}}{{SCALE}}_IT;
