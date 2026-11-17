-- FLAT Q3.1: denormalized LINEORDER_FLAT, no joins.
SELECT c_nation, s_nation, FLOOR(lo_orderdate / 10000) AS d_year, SUM(lo_revenue) AS revenue
FROM lineorder_flat
WHERE c_region = 'ASIA'
  AND s_region = 'ASIA'
  AND lo_orderdate BETWEEN 19920101 AND 19971231
GROUP BY c_nation, s_nation, d_year
ORDER BY d_year ASC, revenue DESC;
