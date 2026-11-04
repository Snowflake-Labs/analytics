# Examples

Sample projects demonstrating Snowflake Interactive Analytics capabilities.

## Available Examples

### [pg-iw-end-to-end](./pg-iw-end-to-end/)

An end-to-end IoT pipeline from Snowflake Postgres to Interactive Analytics with a Cortex Agent. The walkthrough covers:

- Creating an IoT data model (devices, sensors, readings) in **Snowflake Postgres**
- Replicating data into Snowflake via **Postgres Mirroring (CDC)** with the `snowflake_cdc` extension
- Building a **Semantic View** with relationships, facts, dimensions, and metrics
- Provisioning an **Interactive Warehouse** with cache warming for low-latency queries
- Creating a **Cortex Agent** that answers natural-language questions about the IoT data
