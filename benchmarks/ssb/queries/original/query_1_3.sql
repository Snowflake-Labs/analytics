-- Q1.3 (the paper's text says quantity 36-40, its SQL says 26-35; the SQL
-- form is the one used by every published implementation).
SELECT SUM(lo_extendedprice * lo_discount) AS revenue
FROM lineorder, dates
WHERE lo_orderdate = d_datekey
  AND d_weeknuminyear = 6
  AND d_year = 1994
  AND lo_discount BETWEEN 5 AND 7
  AND lo_quantity BETWEEN 26 AND 35;
