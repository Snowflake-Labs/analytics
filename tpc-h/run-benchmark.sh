#!/usr/bin/env bash
set -euo pipefail

# setup test

./iwtpch.sh setup --scale 1
./iwtpch.sh setup --scale 10
./iwtpch.sh setup --scale 100
./iwtpch.sh setup --scale 1000

# run tests

./iwtpch.sh run --warehouse-type standard --tables-type standard --workload original --scale 1 --repeats 5
./iwtpch.sh run --warehouse-type standard --tables-type interactive --workload original --scale 1 --repeats 5
./iwtpch.sh run --warehouse-type standard --tables-type iceberg --workload original --scale 1 --repeats 5
./iwtpch.sh run --warehouse-type interactive --tables-type interactive --workload original --scale 1 --repeats 5

./iwtpch.sh run --warehouse-type standard --tables-type standard --workload original --scale 10 --repeats 5
./iwtpch.sh run --warehouse-type standard --tables-type interactive --workload original --scale 10 --repeats 5
./iwtpch.sh run --warehouse-type standard --tables-type iceberg --workload original --scale 10 --repeats 5
./iwtpch.sh run --warehouse-type interactive --tables-type interactive --workload original --scale 10 --repeats 5

./iwtpch.sh run --warehouse-type standard --tables-type standard --workload original --scale 100 --repeats 5
./iwtpch.sh run --warehouse-type standard --tables-type interactive --workload original --scale 100 --repeats 5
./iwtpch.sh run --warehouse-type standard --tables-type iceberg --workload original --scale 100 --repeats 5
./iwtpch.sh run --warehouse-type interactive --tables-type interactive --workload original --scale 100 --repeats 5

./iwtpch.sh run --warehouse-type standard --tables-type standard --workload original --scale 1000 --repeats 5
./iwtpch.sh run --warehouse-type standard --tables-type interactive --workload original --scale 1000 --repeats 5
./iwtpch.sh run --warehouse-type standard --tables-type iceberg --workload original --scale 1000 --repeats 5
./iwtpch.sh run --warehouse-type interactive --tables-type interactive --workload original --scale 1000 --repeats 5

# tear down tests

./iwtpch.sh teardown --scale 1
./iwtpch.sh teardown --scale 10
./iwtpch.sh teardown --scale 100
./iwtpch.sh teardown --scale 1000
