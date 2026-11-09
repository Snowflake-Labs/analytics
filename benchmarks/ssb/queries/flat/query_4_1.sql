-- FLAT Q4.1: denormalized LINEORDER_FLAT, no joins.
SELECT FLOOR(lo_orderdate / 10000) AS d_year, c_nation, SUM(lo_revenue - lo_supplycost) AS profit
FROM lineorder_flat
WHERE c_region = 'AMERICA'
  AND s_region = 'AMERICA'
  AND p_mfgr IN ('MFGR#1', 'MFGR#2')
GROUP BY d_year, c_nation
ORDER BY d_year, c_nation;
