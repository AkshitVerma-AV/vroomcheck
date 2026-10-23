{# Use the custom schema name as-is (staging / marts / ref) instead of <target>_<custom>. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}{{ target.schema }}{%- else -%}{{ custom_schema_name | trim }}{%- endif -%}
{%- endmacro %}

{# Normalise free-text make / model names so complaints, recalls and TSBs join. #}
{% macro norm_name(col) -%}
    nullif(regexp_replace(upper(trim({{ col }})), '\s+', ' ', 'g'), '')
{%- endmacro %}

{# NHTSA dates are YYYYMMDD strings; anything unparseable or placeholder becomes null. #}
{% macro nhtsa_date(col) -%}
    case when {{ col }} is null or {{ col }} in ('', '00000000', '19000101') then null
         else try_strptime({{ col }}, '%Y%m%d')::date end
{%- endmacro %}

{% macro model_year(col) -%}
    case when try_cast({{ col }} as integer) between 1950 and 2100 then try_cast({{ col }} as integer) end
{%- endmacro %}
