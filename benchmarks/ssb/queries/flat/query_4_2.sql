-- FLAT Q4.2: denormalized LINEORDER_FLAT, no joins.
SELECT FLOOR(lo_orderdate / 10000) AS d_year, s_nation, p_category, SUM(lo_revenue - lo_supplycost) AS profit
FROM lineorder_flat
WHERE c_region = 'AMERICA'
  AND s_region = 'AMERICA'
  AND lo_orderdate BETWEEN 19970101 AND 19981231
  AND p_mfgr IN ('MFGR#1', 'MFGR#2')
GROUP BY d_year, s_nation, p_category
ORDER BY d_year, s_nation, p_category;
