-- MODERN: d_weeknuminyear = 6 AND d_year = 1994 is day-of-year 35-41
-- (DAYNUMINYEAR / 7 + 1 = 6), i.e. 1994-02-04 .. 1994-02-10.
SELECT SUM(lo_extendedprice * lo_discount) AS revenue
FROM lineorder
WHERE lo_orderdate BETWEEN 19940204 AND 19940210
  AND lo_discount BETWEEN 5 AND 7
  AND lo_quantity BETWEEN 26 AND 35;
