-- MODERN: original scans customer twice (once for the avg threshold, once for
-- the outer query). Filter the country codes once in a CTE, derive the average
-- balance with AVG(...) OVER (), and keep the NOT EXISTS anti-join for orders.
WITH selected_customers AS (
    SELECT
        SUBSTR(c_phone, 1, 2) AS cntrycode,
        c_acctbal,
        c_custkey,
        AVG(CASE WHEN c_acctbal > 0.00 THEN c_acctbal END) OVER () AS avg_acctbal
    FROM customer
    WHERE SUBSTR(c_phone, 1, 2) IN ('13', '31', '23', '29', '30', '18', '17')
)
SELECT
    cntrycode,
    count(*) AS numcust,
    sum(c_acctbal) AS totacctbal
FROM selected_customers sc
WHERE c_acctbal > avg_acctbal
    AND NOT EXISTS (
        SELECT 1
        FROM orders
        WHERE o_custkey = sc.c_custkey
    )
GROUP BY cntrycode
ORDER BY cntrycode;
