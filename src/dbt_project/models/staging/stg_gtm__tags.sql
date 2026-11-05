{%- set source_relation = source('raw_gtm', 'tags') -%}
{%- set relation_exists = adapter.get_relation(database=source_relation.database, schema=source_relation.schema, identifier=source_relation.identifier) is not none -%}

with source as (
    {%- if relation_exists %}
    select * from {{ source_relation }}
    {%- else %}
    select
        null::varchar as tag_id,
        null::varchar as container_id,
        null::varchar as type,
        null::varchar as name,
        null::json as firing_triggers,
        null::json as blocking_triggers,
        null::varchar as tag_manager_url
    where false
    {%- endif %}
),

renamed as (
    select
        tag_id::varchar                         as tag_id,
        container_id::varchar                   as container_id,
        type::varchar                           as type,
        name::varchar                           as name,
        firing_triggers::json                   as firing_triggers,
        blocking_triggers::json                 as blocking_triggers,
        tag_manager_url::varchar                as tag_manager_url
    from source
)

select * from renamed
