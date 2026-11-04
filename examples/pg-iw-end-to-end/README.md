# Interactive Modern End-to-End Demo

This folder contains an end-to-end demo that walks through creating an IoT data pipeline from Postgres to Snowflake, then building analytics and an AI agent on top of the mirrored data. The scripts are meant to be run interactively in order, switching between Postgres and Snowflake at the indicated points.

## Prerequisites

This sample assumes you already have a Snowflake Postgres database correctly set up. If you need to create one, follow the [Getting Started with Snowflake Postgres](https://www.snowflake.com/en/developers/guides/getting-started-with-snowflake-postgres/) guide.

## Architecture

```mermaid
graph LR
    subgraph Snowflake Postgres
        PG_DB[(IoT Database)]
        PG_DB --- devices[devices]
        PG_DB --- sensors[sensors]
        PG_DB --- readings[readings]
        devices[devices] --- CDC[snowflake_cdc]
        sensors[sensors] --- CDC[snowflake_cdc]
        readings[readings] --- CDC[snowflake_cdc]
    end

    subgraph Snowflake
        Mirror[Mirror]
        subgraph Mirrored Tables
            M_devices[devices]
            M_sensors[sensors]
            M_readings[readings]
        end
        SV[Semantic View]
        IW[Interactive Warehouse]
        Agent[Cortex Agent]
    end

    CDC --> Mirror 
    Mirror --> M_devices
    Mirror --> M_sensors
    Mirror --> M_readings
    M_devices & M_sensors & M_readings --> SV

    SV --> IW
    IW --> Agent
```

## Files

- **`01-postgres.sql`** -- Run on a Snowflake Postgres instance. Creates the IoT schema (`devices`, `sensors`, `readings`), inserts sample data, and enables the `snowflake_cdc` extension for mirroring.

- **`02-snowflake.sql`** -- Run on Snowflake. Sets up mirroring from the Postgres instance, creates a semantic view over the mirrored tables, provisions an interactive warehouse with cache warming, and queries the semantic view.

- **`03-postgres.sql`** -- Run on Postgres. Inserts additional readings and creates new devices to demonstrate live data replication through the mirror.

- **`04-snowflake.sql`** -- Run on Snowflake. Queries the semantic view to verify the new data has been replicated, then creates a Cortex Agent that can answer natural-language questions about the IoT data.

- **`05-postgres-cleanup.sql`** -- Run on Postgres. Drops the demo tables.

- **`06-snowflake-cleanup.sql`** -- Run on Snowflake. Drops the agent, mirror, and optionally the demo database and interactive warehouse.

## Additional References

- [Mirror Postgres Data to Snowflake](https://www.snowflake.com/en/developers/guides/snowflake-postgres-mirror-to-snowflake/)
- [Getting Started with Interactive Analytics](https://www.snowflake.com/en/developers/guides/getting-started-with-interactive-analytics/)
