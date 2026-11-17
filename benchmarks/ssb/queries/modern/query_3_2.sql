-- MODERN: explicit joins, IN-lists, and the d_year range pushed onto the
-- YYYYMMDD order-date key (no DATES join).
SELECT c.c_city, s.s_city, FLOOR(lo.lo_orderdate / 10000) AS d_year, SUM(lo.lo_revenue) AS revenue
FROM lineorder lo
JOIN customer c ON c.c_custkey = lo.lo_custkey
JOIN supplier s ON s.s_suppkey = lo.lo_suppkey
WHERE c.c_nation = 'UNITED STATES'
  AND s.s_nation = 'UNITED STATES'
  AND lo.lo_orderdate BETWEEN 19920101 AND 19971231
GROUP BY c.c_city, s.s_city, d_year
ORDER BY d_year ASC, revenue DESC;
