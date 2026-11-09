-- MODERN: original references the revenue0 CTE twice (once in the join, once
-- in a MAX subquery). Compute the max once with MAX(...) OVER () + QUALIFY so
-- the supplier revenue aggregation is scanned a single time.
WITH revenue0 AS (
    SELECT
        l_suppkey AS supplier_no,
        SUM(l_extendedprice * (1 - l_discount)) AS total_revenue
    FROM lineitem
    WHERE l_shipdate >= DATE '1996-01-01'
      AND l_shipdate < DATEADD(month, 3, DATE '1996-01-01')
    GROUP BY l_suppkey
)
SELECT
    s_suppkey,
    s_name,
    s_address,
    s_phone,
    total_revenue
FROM supplier, revenue0
WHERE s_suppkey = supplier_no
QUALIFY total_revenue = MAX(total_revenue) OVER ()
ORDER BY s_suppkey;
