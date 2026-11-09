-- MODERN: d_yearmonthnum = 199401 -> key range on lo_orderdate.
SELECT SUM(lo_extendedprice * lo_discount) AS revenue
FROM lineorder
WHERE lo_orderdate BETWEEN 19940101 AND 19940131
  AND lo_discount BETWEEN 4 AND 6
  AND lo_quantity BETWEEN 26 AND 35;
