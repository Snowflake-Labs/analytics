-- Teardown standard warehouse (per scale factor)
USE ROLE SYSADMIN;
DROP WAREHOUSE IF EXISTS {{SOLUTION_NAME}}_BENCH_WH_STD_{{SCALE}};
