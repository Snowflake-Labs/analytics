-- MODERN: explicit joins, IN-lists, d_year derived from the order-date key.
SELECT FLOOR(lo.lo_orderdate / 10000) AS d_year, s.s_city, p.p_brand1, SUM(lo.lo_revenue - lo.lo_supplycost) AS profit
FROM lineorder lo
JOIN customer c ON c.c_custkey = lo.lo_custkey
JOIN supplier s ON s.s_suppkey = lo.lo_suppkey
JOIN part p ON p.p_partkey = lo.lo_partkey
WHERE c.c_region = 'AMERICA'
  AND s.s_nation = 'UNITED STATES'
  AND lo.lo_orderdate BETWEEN 19970101 AND 19981231
  AND p.p_category = 'MFGR#14'
GROUP BY d_year, s.s_city, p.p_brand1
ORDER BY d_year, s.s_city, p.p_brand1;
