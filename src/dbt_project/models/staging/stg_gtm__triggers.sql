{%- set source_relation = source('raw_gtm', 'triggers') -%}
{%- set relation_exists = adapter.get_relation(database=source_relation.database, schema=source_relation.schema, identifier=source_relation.identifier) is not none -%}

with source as (
    {%- if relation_exists %}
    select * from {{ source_relation }}
    {%- else %}
    select
        null::varchar as trigger_id,
        null::varchar as container_id,
        null::varchar as type,
        null::varchar as name,
        null::json as filter_json
    where false
    {%- endif %}
),

renamed as (
    select
        trigger_id::varchar                     as trigger_id,
        container_id::varchar                   as container_id,
        type::varchar                           as type,
        name::varchar                           as name,
        filter_json::json                       as filter_json
    from source
)

select * from renamed
