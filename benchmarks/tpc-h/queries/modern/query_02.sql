-- MODERN: replace correlated min(ps_supplycost) subquery with
-- QUALIFY ROW_NUMBER() OVER (PARTITION BY p_partkey ORDER BY ps_supplycost).
-- Avoids re-joining partsupp/supplier/nation/region a second time.
SELECT
    s_acctbal,
    s_name,
    n_name,
    p_partkey,
    p_mfgr,
    s_address,
    s_phone,
    s_comment
FROM part, supplier, partsupp, nation, region
WHERE (p_partkey = ps_partkey)
    AND (s_suppkey = ps_suppkey)
    AND (p_size = 15)
    AND (p_type LIKE '%BRASS')
    AND (s_nationkey = n_nationkey)
    AND (n_regionkey = r_regionkey)
    AND (r_name = 'EUROPE')
QUALIFY ps_supplycost = MIN(ps_supplycost) OVER (PARTITION BY p_partkey)
ORDER BY
    s_acctbal DESC,
    n_name,
    s_name,
    p_partkey
LIMIT 100;
