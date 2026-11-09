-- MODERN: d_yearmonth = 'Dec1997' -> key range on lo_orderdate (no DATES join).
SELECT c.c_city, s.s_city, FLOOR(lo.lo_orderdate / 10000) AS d_year, SUM(lo.lo_revenue) AS revenue
FROM lineorder lo
JOIN customer c ON c.c_custkey = lo.lo_custkey
JOIN supplier s ON s.s_suppkey = lo.lo_suppkey
WHERE c.c_city IN ('UNITED KI1', 'UNITED KI5')
  AND s.s_city IN ('UNITED KI1', 'UNITED KI5')
  AND lo.lo_orderdate BETWEEN 19971201 AND 19971231
GROUP BY c.c_city, s.s_city, d_year
ORDER BY d_year ASC, revenue DESC;
