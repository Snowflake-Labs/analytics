-- FLAT Q1.3: denormalized LINEORDER_FLAT, no joins.
SELECT SUM(lo_extendedprice * lo_discount) AS revenue
FROM lineorder_flat
WHERE lo_orderdate BETWEEN 19940204 AND 19940210
  AND lo_discount BETWEEN 5 AND 7
  AND lo_quantity BETWEEN 26 AND 35;
