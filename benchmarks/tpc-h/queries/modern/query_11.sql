-- MODERN: the original recomputes the grand total in a correlated HAVING
-- subquery (a second full join of partsupp/supplier/nation). Compute the
-- per-part value once, then compare against SUM(...) OVER () via QUALIFY.
WITH part_value AS (
    SELECT
        ps_partkey,
        SUM(ps_supplycost * ps_availqty) AS value
    FROM partsupp, supplier, nation
    WHERE (ps_suppkey = s_suppkey)
        AND (s_nationkey = n_nationkey)
        AND (n_name = 'GERMANY')
    GROUP BY ps_partkey
)
SELECT
    ps_partkey,
    value
FROM part_value
QUALIFY value > SUM(value) OVER () * 0.0001
ORDER BY value DESC;
