-- FLAT Q4.3: denormalized LINEORDER_FLAT, no joins.
SELECT FLOOR(lo_orderdate / 10000) AS d_year, s_city, p_brand1, SUM(lo_revenue - lo_supplycost) AS profit
FROM lineorder_flat
WHERE c_region = 'AMERICA'
  AND s_nation = 'UNITED STATES'
  AND lo_orderdate BETWEEN 19970101 AND 19981231
  AND p_category = 'MFGR#14'
GROUP BY d_year, s_city, p_brand1
ORDER BY d_year, s_city, p_brand1;
