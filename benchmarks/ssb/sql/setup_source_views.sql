-- SSB source views (per scale factor)
--
-- SSB is defined as a transformation of the TPC-H schema (O'Neil et al., "Star
-- Schema Benchmark", rev. 3). Snowflake ships TPC-H (not SSB) in
-- SNOWFLAKE_SAMPLE_DATA, so these views derive the five SSB tables from
-- SNOWFLAKE_SAMPLE_DATA.TPCH_SF<scale> using the SSB cardinalities:
--   LINEORDER  SF * 6,000,000 (one row per TPC-H LINEITEM, joined to ORDERS)
--   CUSTOMER   SF * 30,000
--   SUPPLIER   SF * 2,000
--   PART       200,000 * FLOOR(1 + LOG2(SF))
--   DATES      7 years of days (1992-01-01 .. 1998-12-31)
-- The table setup scripts CTAS from these views into each table format.
USE ROLE SYSADMIN;

CREATE DATABASE IF NOT EXISTS {{SOLUTION_NAME}}_BENCH_DB;
USE DATABASE {{SOLUTION_NAME}}_BENCH_DB;

CREATE SCHEMA IF NOT EXISTS SSB_SF{{SCALE}}_SRC;
USE SCHEMA SSB_SF{{SCALE}}_SRC;

-- C_CITY / S_CITY: first 9 chars of the nation (blank padded) + a digit 0-9.
-- The digit is a deterministic hash of the key so every setup yields the same data.
CREATE OR REPLACE VIEW CUSTOMER AS
SELECT
  c.C_CUSTKEY::NUMBER(38,0)                                               AS C_CUSTKEY,
  c.C_NAME::VARCHAR(25)                                                   AS C_NAME,
  LEFT(c.C_ADDRESS, 25)::VARCHAR(25)                                      AS C_ADDRESS,
  (RPAD(LEFT(n.N_NAME, 9), 9, ' ') || MOD(ABS(HASH(c.C_CUSTKEY)), 10))::VARCHAR(10) AS C_CITY,
  n.N_NAME::VARCHAR(15)                                                   AS C_NATION,
  r.R_NAME::VARCHAR(12)                                                   AS C_REGION,
  c.C_PHONE::VARCHAR(15)                                                  AS C_PHONE,
  c.C_MKTSEGMENT::VARCHAR(10)                                             AS C_MKTSEGMENT
FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF{{SCALE}}.CUSTOMER c
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF{{SCALE}}.NATION n ON n.N_NATIONKEY = c.C_NATIONKEY
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF{{SCALE}}.REGION r ON r.R_REGIONKEY = n.N_REGIONKEY
WHERE c.C_CUSTKEY <= 30000 * {{SCALE}};

CREATE OR REPLACE VIEW SUPPLIER AS
SELECT
  s.S_SUPPKEY::NUMBER(38,0)                                               AS S_SUPPKEY,
  s.S_NAME::VARCHAR(25)                                                   AS S_NAME,
  LEFT(s.S_ADDRESS, 25)::VARCHAR(25)                                      AS S_ADDRESS,
  (RPAD(LEFT(n.N_NAME, 9), 9, ' ') || MOD(ABS(HASH(s.S_SUPPKEY)), 10))::VARCHAR(10) AS S_CITY,
  n.N_NAME::VARCHAR(15)                                                   AS S_NATION,
  r.R_NAME::VARCHAR(12)                                                   AS S_REGION,
  s.S_PHONE::VARCHAR(15)                                                  AS S_PHONE
FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF{{SCALE}}.SUPPLIER s
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF{{SCALE}}.NATION n ON n.N_NATIONKEY = s.S_NATIONKEY
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF{{SCALE}}.REGION r ON r.R_REGIONKEY = n.N_REGIONKEY
WHERE s.S_SUPPKEY <= 2000 * {{SCALE}};

-- P_NAME: first two of the five TPC-H colors; P_COLOR: the first color.
-- P_MFGR / P_CATEGORY reuse the TPC-H manufacturer and brand digits
-- ('Manufacturer#M', 'Brand#MN' -> 'MFGR#M', 'MFGR#MN'), so they keep the
-- SSB uniform distribution; P_BRAND1 adds a deterministic 1-40 suffix.
CREATE OR REPLACE VIEW PART AS
SELECT
  p.P_PARTKEY::NUMBER(38,0)                                               AS P_PARTKEY,
  (SPLIT_PART(p.P_NAME, ' ', 1) || ' ' || SPLIT_PART(p.P_NAME, ' ', 2))::VARCHAR(22) AS P_NAME,
  ('MFGR#' || SUBSTR(p.P_BRAND, 7, 1))::VARCHAR(6)                       AS P_MFGR,
  ('MFGR#' || SUBSTR(p.P_BRAND, 7, 2))::VARCHAR(7)                       AS P_CATEGORY,
  ('MFGR#' || SUBSTR(p.P_BRAND, 7, 2) || (1 + MOD(ABS(HASH(p.P_PARTKEY)), 40)))::VARCHAR(9) AS P_BRAND1,
  SPLIT_PART(p.P_NAME, ' ', 1)::VARCHAR(11)                               AS P_COLOR,
  p.P_TYPE::VARCHAR(25)                                                   AS P_TYPE,
  p.P_SIZE::NUMBER(38,0)                                                  AS P_SIZE,
  p.P_CONTAINER::VARCHAR(10)                                              AS P_CONTAINER
FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF{{SCALE}}.PART p
WHERE p.P_PARTKEY <= 200000 * FLOOR(1 + LOG(2, {{SCALE}}))::INT;

-- Column semantics follow the SSB paper (section 2.6). D_WEEKNUMINYEAR uses the
-- ssb-dbgen formula (DAYNUMINYEAR / 7 + 1), which is what defines Q1.3's week 6.
CREATE OR REPLACE VIEW DATES AS
WITH d AS (
  SELECT DATEADD(day, SEQ4(), DATE '1992-01-01') AS dt
  FROM TABLE(GENERATOR(ROWCOUNT => 2557))
), m AS (
  SELECT
    dt,
    DECODE(MONTH(dt), 1, 'January', 2, 'February', 3, 'March', 4, 'April', 5, 'May',
      6, 'June', 7, 'July', 8, 'August', 9, 'September', 10, 'October',
      11, 'November', 12, 'December') AS month_name,
    DECODE(DAYOFWEEKISO(dt), 7, 'Sunday', 1, 'Monday', 2, 'Tuesday', 3, 'Wednesday',
      4, 'Thursday', 5, 'Friday', 6, 'Saturday') AS day_name,
    MOD(DAYOFWEEKISO(dt), 7) + 1 AS day_in_week  -- Sunday = 1 .. Saturday = 7
  FROM d
)
SELECT
  (YEAR(dt) * 10000 + MONTH(dt) * 100 + DAY(dt))::NUMBER(38,0)            AS D_DATEKEY,
  (month_name || ' ' || DAY(dt) || ', ' || YEAR(dt))::VARCHAR(18)         AS D_DATE,
  day_name::VARCHAR(9)                                                    AS D_DAYOFWEEK,
  month_name::VARCHAR(9)                                                  AS D_MONTH,
  YEAR(dt)::NUMBER(38,0)                                                  AS D_YEAR,
  (YEAR(dt) * 100 + MONTH(dt))::NUMBER(38,0)                              AS D_YEARMONTHNUM,
  (LEFT(month_name, 3) || YEAR(dt))::VARCHAR(7)                           AS D_YEARMONTH,
  day_in_week::NUMBER(38,0)                                               AS D_DAYNUMINWEEK,
  DAY(dt)::NUMBER(38,0)                                                   AS D_DAYNUMINMONTH,
  DAYOFYEAR(dt)::NUMBER(38,0)                                             AS D_DAYNUMINYEAR,
  MONTH(dt)::NUMBER(38,0)                                                 AS D_MONTHNUMINYEAR,
  (FLOOR(DAYOFYEAR(dt) / 7) + 1)::NUMBER(38,0)                            AS D_WEEKNUMINYEAR,
  (CASE
    WHEN MONTH(dt) IN (11, 12) THEN 'Christmas'
    WHEN MONTH(dt) BETWEEN 5 AND 8 THEN 'Summer'
    WHEN MONTH(dt) BETWEEN 1 AND 3 THEN 'Winter'
    WHEN MONTH(dt) = 4 THEN 'Spring'
    ELSE 'Fall'
  END)::VARCHAR(12)                                                       AS D_SELLINGSEASON,
  IFF(day_in_week = 7, 1, 0)::NUMBER(1,0)                                 AS D_LASTDAYINWEEKFL,
  IFF(dt = LAST_DAY(dt), 1, 0)::NUMBER(1,0)                               AS D_LASTDAYINMONTHFL,
  IFF((MONTH(dt), DAY(dt)) IN ((12, 24), (1, 1), (2, 20), (4, 20), (5, 20), (7, 20),
      (8, 20), (9, 20), (10, 20), (11, 20)), 1, 0)::NUMBER(1,0)           AS D_HOLIDAYFL,
  IFF(day_in_week BETWEEN 2 AND 6, 1, 0)::NUMBER(1,0)                     AS D_WEEKDAYFL
FROM m;

-- LINEORDER = LINEITEM joined to ORDERS. Customer, supplier and part keys are
-- folded onto the smaller SSB dimensions with MOD so every fact row has a match.
-- Discount and tax become whole percents (0-10, 0-8); dates become YYYYMMDD keys
-- into DATES. LO_REVENUE = extendedprice * (100 - discount) / 100 and
-- LO_SUPPLYCOST = 60% of the unit retail price (extendedprice / quantity), as in
-- ssb-dbgen. Money columns keep TPC-H's NUMBER(15,2) dollar values.
CREATE OR REPLACE VIEW LINEORDER AS
SELECT
  l.L_ORDERKEY::NUMBER(38,0)                                              AS LO_ORDERKEY,
  l.L_LINENUMBER::NUMBER(38,0)                                            AS LO_LINENUMBER,
  (MOD(o.O_CUSTKEY - 1, 30000 * {{SCALE}}) + 1)::NUMBER(38,0)             AS LO_CUSTKEY,
  (MOD(l.L_PARTKEY - 1, 200000 * FLOOR(1 + LOG(2, {{SCALE}}))::INT) + 1)::NUMBER(38,0) AS LO_PARTKEY,
  (MOD(l.L_SUPPKEY - 1, 2000 * {{SCALE}}) + 1)::NUMBER(38,0)              AS LO_SUPPKEY,
  TO_NUMBER(TO_CHAR(o.O_ORDERDATE, 'YYYYMMDD'))::NUMBER(38,0)             AS LO_ORDERDATE,
  o.O_ORDERPRIORITY::VARCHAR(15)                                          AS LO_ORDERPRIORITY,
  o.O_SHIPPRIORITY::VARCHAR(1)                                            AS LO_SHIPPRIORITY,
  l.L_QUANTITY::NUMBER(38,0)                                              AS LO_QUANTITY,
  l.L_EXTENDEDPRICE::NUMBER(15,2)                                         AS LO_EXTENDEDPRICE,
  o.O_TOTALPRICE::NUMBER(15,2)                                            AS LO_ORDTOTALPRICE,
  ROUND(l.L_DISCOUNT * 100)::NUMBER(38,0)                                 AS LO_DISCOUNT,
  ROUND(l.L_EXTENDEDPRICE * (1 - l.L_DISCOUNT), 2)::NUMBER(15,2)          AS LO_REVENUE,
  ROUND(0.6 * l.L_EXTENDEDPRICE / l.L_QUANTITY, 2)::NUMBER(15,2)          AS LO_SUPPLYCOST,
  ROUND(l.L_TAX * 100)::NUMBER(38,0)                                      AS LO_TAX,
  TO_NUMBER(TO_CHAR(l.L_COMMITDATE, 'YYYYMMDD'))::NUMBER(38,0)            AS LO_COMMITDATE,
  l.L_SHIPMODE::VARCHAR(10)                                               AS LO_SHIPMODE
FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF{{SCALE}}.LINEITEM l
JOIN SNOWFLAKE_SAMPLE_DATA.TPCH_SF{{SCALE}}.ORDERS o ON o.O_ORDERKEY = l.L_ORDERKEY;

-- Denormalized fact table used by the "flat" workload (as in the Apache Doris
-- SSB-Flat benchmark): every LINEORDER row with its customer, supplier and part
-- attributes pre-joined.
CREATE OR REPLACE VIEW LINEORDER_FLAT AS
SELECT
  lo.*,
  c.C_NAME, c.C_ADDRESS, c.C_CITY, c.C_NATION, c.C_REGION, c.C_PHONE, c.C_MKTSEGMENT,
  s.S_NAME, s.S_ADDRESS, s.S_CITY, s.S_NATION, s.S_REGION, s.S_PHONE,
  p.P_NAME, p.P_MFGR, p.P_CATEGORY, p.P_BRAND1, p.P_COLOR, p.P_TYPE, p.P_SIZE, p.P_CONTAINER
FROM LINEORDER lo
JOIN CUSTOMER c ON c.C_CUSTKEY = lo.LO_CUSTKEY
JOIN SUPPLIER s ON s.S_SUPPKEY = lo.LO_SUPPKEY
JOIN PART p ON p.P_PARTKEY = lo.LO_PARTKEY;
