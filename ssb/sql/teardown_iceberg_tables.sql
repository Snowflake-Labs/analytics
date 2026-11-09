-- Teardown iceberg tables (per scale factor)
USE ROLE SYSADMIN;
DROP SCHEMA IF EXISTS {{SOLUTION_NAME}}_BENCH_DB.SSB_SF{{SCALE}}_ICE;
