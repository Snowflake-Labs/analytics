-- FLAT Q2.3: denormalized LINEORDER_FLAT, no joins.
SELECT SUM(lo_revenue) AS revenue, FLOOR(lo_orderdate / 10000) AS d_year, p_brand1
FROM lineorder_flat
WHERE p_brand1 = 'MFGR#2239'
  AND s_region = 'EUROPE'
GROUP BY d_year, p_brand1
ORDER BY d_year, p_brand1;
