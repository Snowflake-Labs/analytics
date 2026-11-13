-- Q2.3 (the paper is inconsistent: 'MFGR#2339' in the text, 'MFGR#2221' in the
-- SQL; 'MFGR#2239' is the value used by every published implementation).
SELECT SUM(lo_revenue) AS revenue, d_year, p_brand1
FROM lineorder, dates, part, supplier
WHERE lo_orderdate = d_datekey
  AND lo_partkey = p_partkey
  AND lo_suppkey = s_suppkey
  AND p_brand1 = 'MFGR#2239'
  AND s_region = 'EUROPE'
GROUP BY d_year, p_brand1
ORDER BY d_year, p_brand1;
