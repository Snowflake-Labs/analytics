-- MODERN: original scans lineitem twice -- once in the IN-subquery to find
-- orders with SUM(l_quantity) > 300, then again in the main join to re-sum
-- l_quantity. That per-order sum is the same value, so compute it once in a
-- CTE and reuse it (single lineitem scan, no outer GROUP BY needed).
WITH order_qty AS (
    SELECT
        l_orderkey,
        SUM(l_quantity) AS total_qty
    FROM lineitem
    GROUP BY l_orderkey
    HAVING SUM(l_quantity) > 300
)
SELECT
    c_name,
    c_custkey,
    o_orderkey,
    o_orderdate,
    o_totalprice,
    oq.total_qty AS sum_qty
FROM customer, orders, order_qty AS oq
WHERE (c_custkey = o_custkey)
    AND (o_orderkey = oq.l_orderkey)
ORDER BY
    o_totalprice DESC,
    o_orderdate
LIMIT 100;
