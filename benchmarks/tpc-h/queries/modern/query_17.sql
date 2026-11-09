-- MODERN: original scans lineitem twice -- once joined to part, and again in a
-- correlated subquery computing 0.2 * AVG(l_quantity) per part. Filter parts
-- first, then compute the per-part average in a single lineitem pass with
-- AVG(l_quantity) OVER (PARTITION BY l_partkey).
WITH target_parts AS (
    SELECT p_partkey
    FROM part
    WHERE p_brand = 'Brand#23'
      AND p_container = 'MED BOX'
),
lineitem_with_avg AS (
    SELECT
        l_extendedprice,
        l_quantity,
        AVG(l_quantity) OVER (PARTITION BY l_partkey) AS avg_qty
    FROM lineitem
    WHERE l_partkey IN (SELECT p_partkey FROM target_parts)
)
SELECT
    SUM(l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem_with_avg
WHERE l_quantity < 0.2 * avg_qty;
