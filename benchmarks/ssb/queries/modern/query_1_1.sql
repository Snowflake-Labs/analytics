-- MODERN: LO_ORDERDATE is a YYYYMMDD key, so the DATES join collapses into a
-- range predicate on the fact table's clustering key (enables partition pruning).
SELECT SUM(lo_extendedprice * lo_discount) AS revenue
FROM lineorder
WHERE lo_orderdate BETWEEN 19930101 AND 19931231
  AND lo_discount BETWEEN 1 AND 3
  AND lo_quantity < 25;
