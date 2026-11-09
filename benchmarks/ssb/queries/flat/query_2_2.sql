-- FLAT Q2.2: denormalized LINEORDER_FLAT, no joins.
SELECT SUM(lo_revenue) AS revenue, FLOOR(lo_orderdate / 10000) AS d_year, p_brand1
FROM lineorder_flat
WHERE p_brand1 BETWEEN 'MFGR#2221' AND 'MFGR#2228'
  AND s_region = 'ASIA'
GROUP BY d_year, p_brand1
ORDER BY d_year, p_brand1;
