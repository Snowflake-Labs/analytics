-- FLAT Q3.2: denormalized LINEORDER_FLAT, no joins.
SELECT c_city, s_city, FLOOR(lo_orderdate / 10000) AS d_year, SUM(lo_revenue) AS revenue
FROM lineorder_flat
WHERE c_nation = 'UNITED STATES'
  AND s_nation = 'UNITED STATES'
  AND lo_orderdate BETWEEN 19920101 AND 19971231
GROUP BY c_city, s_city, d_year
ORDER BY d_year ASC, revenue DESC;
