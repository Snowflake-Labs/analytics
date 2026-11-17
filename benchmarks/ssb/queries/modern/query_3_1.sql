-- MODERN: explicit joins, IN-lists, and the d_year range pushed onto the
-- YYYYMMDD order-date key (no DATES join).
SELECT c.c_nation, s.s_nation, FLOOR(lo.lo_orderdate / 10000) AS d_year, SUM(lo.lo_revenue) AS revenue
FROM lineorder lo
JOIN customer c ON c.c_custkey = lo.lo_custkey
JOIN supplier s ON s.s_suppkey = lo.lo_suppkey
WHERE c.c_region = 'ASIA'
  AND s.s_region = 'ASIA'
  AND lo.lo_orderdate BETWEEN 19920101 AND 19971231
GROUP BY c.c_nation, s.s_nation, d_year
ORDER BY d_year ASC, revenue DESC;
