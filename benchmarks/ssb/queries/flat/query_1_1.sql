-- FLAT Q1.1: denormalized LINEORDER_FLAT, no joins.
SELECT SUM(lo_extendedprice * lo_discount) AS revenue
FROM lineorder_flat
WHERE lo_orderdate BETWEEN 19930101 AND 19931231
  AND lo_discount BETWEEN 1 AND 3
  AND lo_quantity < 25;
