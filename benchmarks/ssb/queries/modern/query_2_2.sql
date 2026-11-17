-- MODERN: explicit joins; d_year is derived from the YYYYMMDD order-date key
-- instead of joining DATES.
SELECT SUM(lo.lo_revenue) AS revenue, FLOOR(lo.lo_orderdate / 10000) AS d_year, p.p_brand1
FROM lineorder lo
JOIN part p ON p.p_partkey = lo.lo_partkey
JOIN supplier s ON s.s_suppkey = lo.lo_suppkey
WHERE p.p_brand1 BETWEEN 'MFGR#2221' AND 'MFGR#2228'
  AND s.s_region = 'ASIA'
GROUP BY d_year, p.p_brand1
ORDER BY d_year, p.p_brand1;
