-- FLAT Q3.3: denormalized LINEORDER_FLAT, no joins.
SELECT c_city, s_city, FLOOR(lo_orderdate / 10000) AS d_year, SUM(lo_revenue) AS revenue
FROM lineorder_flat
WHERE c_city IN ('UNITED KI1', 'UNITED KI5')
  AND s_city IN ('UNITED KI1', 'UNITED KI5')
  AND lo_orderdate BETWEEN 19920101 AND 19971231
GROUP BY c_city, s_city, d_year
ORDER BY d_year ASC, revenue DESC;
