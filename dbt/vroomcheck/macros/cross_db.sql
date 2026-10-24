{# Functions whose names or behaviour differ between DuckDB (dev target) and Snowflake.
   Each macro dispatches on the adapter: default__ is the DuckDB form, snowflake__ the
   Snowflake form. Regex patterns use POSIX classes ([[:space:]], [0-9]) rather than
   backslash escapes, which Snowflake string literals would swallow. #}

{# Trim leading / trailing space characters. DuckDB's trim() strips every Unicode space
   separator (e.g. the non-breaking space U+00A0 that some NHTSA text ends with);
   Snowflake's TRIM strips only U+0020 unless told otherwise, so pass the same set. #}
{% macro ws_trim(expr) -%}
    {{ return(adapter.dispatch('ws_trim', 'vroomcheck')(expr)) }}
{%- endmacro %}
{% macro default__ws_trim(expr) -%}
    trim({{ expr }})
{%- endmacro %}
{% macro snowflake__ws_trim(expr) -%}
    {%- set zs = [32, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8239, 8287, 12288] -%}
    trim({{ expr }}, {% for c in zs %}chr({{ c }}){% if not loop.last %} || {% endif %}{% endfor %})
{%- endmacro %}

{# Replace every match of a regex. #}
{% macro regex_replace_all(expr, pattern, replacement) -%}
    {{ return(adapter.dispatch('regex_replace_all', 'vroomcheck')(expr, pattern, replacement)) }}
{%- endmacro %}
{% macro default__regex_replace_all(expr, pattern, replacement) -%}
    regexp_replace({{ expr }}, '{{ pattern }}', '{{ replacement }}', 'g')
{%- endmacro %}
{% macro snowflake__regex_replace_all(expr, pattern, replacement) -%}
    regexp_replace({{ expr }}, '{{ pattern }}', '{{ replacement }}')
{%- endmacro %}

{# True when the expression contains a match. #}
{% macro regex_like(expr, pattern) -%}
    {{ return(adapter.dispatch('regex_like', 'vroomcheck')(expr, pattern)) }}
{%- endmacro %}
{% macro default__regex_like(expr, pattern) -%}
    regexp_matches({{ expr }}, '{{ pattern }}')
{%- endmacro %}
{% macro snowflake__regex_like(expr, pattern) -%}
    regexp_like({{ expr }}, '{{ pattern }}')
{%- endmacro %}

{# First match of a regex, or null when there is none. #}
{% macro regex_first_match(expr, pattern) -%}
    {{ return(adapter.dispatch('regex_first_match', 'vroomcheck')(expr, pattern)) }}
{%- endmacro %}
{% macro default__regex_first_match(expr, pattern) -%}
    nullif(regexp_extract({{ expr }}, '{{ pattern }}'), '')
{%- endmacro %}
{% macro snowflake__regex_first_match(expr, pattern) -%}
    regexp_substr({{ expr }}, '{{ pattern }}')
{%- endmacro %}

{# One row per distinct (id, match) for every regex match in a text column. #}
{% macro regex_match_rows(relation, id_col, text_col, pattern, match_alias) -%}
    {{ return(adapter.dispatch('regex_match_rows', 'vroomcheck')(relation, id_col, text_col, pattern, match_alias)) }}
{%- endmacro %}
{% macro default__regex_match_rows(relation, id_col, text_col, pattern, match_alias) -%}
    select distinct {{ id_col }}, unnest(regexp_extract_all({{ text_col }}, '{{ pattern }}')) as {{ match_alias }}
    from {{ relation }}
{%- endmacro %}
{% macro snowflake__regex_match_rows(relation, id_col, text_col, pattern, match_alias) -%}
    select distinct r.{{ id_col }}, f.value::varchar as {{ match_alias }}
    from {{ relation }} r,
         lateral flatten(input => regexp_substr_all(r.{{ text_col }}, '{{ pattern }}')) f
{%- endmacro %}

{# YYYYMMDD text to a date; null when it does not parse. #}
{% macro parse_yyyymmdd(expr) -%}
    {{ return(adapter.dispatch('parse_yyyymmdd', 'vroomcheck')(expr)) }}
{%- endmacro %}
{% macro default__parse_yyyymmdd(expr) -%}
    try_strptime({{ expr }}, '%Y%m%d')::date
{%- endmacro %}
{% macro snowflake__parse_yyyymmdd(expr) -%}
    try_to_date({{ expr }}, 'YYYYMMDD')
{%- endmacro %}

{# Distinct values joined with a separator, in sorted order; null when there are none. #}
{% macro string_agg_distinct(expr, sep) -%}
    {{ return(adapter.dispatch('string_agg_distinct', 'vroomcheck')(expr, sep)) }}
{%- endmacro %}
{% macro default__string_agg_distinct(expr, sep) -%}
    string_agg(distinct {{ expr }}, '{{ sep }}' order by {{ expr }})
{%- endmacro %}
{% macro snowflake__string_agg_distinct(expr, sep) -%}
    nullif(listagg(distinct {{ expr }}, '{{ sep }}') within group (order by {{ expr }}), '')
{%- endmacro %}

{# One row per day from start (inclusive) to end (exclusive), as column d. #}
{% macro date_spine_days(start_date, end_date) -%}
    {{ return(adapter.dispatch('date_spine_days', 'vroomcheck')(start_date, end_date)) }}
{%- endmacro %}
{% macro default__date_spine_days(start_date, end_date) -%}
    select d::date as d from range(date '{{ start_date }}', date '{{ end_date }}', interval 1 day) as t(d)
{%- endmacro %}
{% macro snowflake__date_spine_days(start_date, end_date) -%}
    {%- set n_days = (modules.datetime.date.fromisoformat(end_date) - modules.datetime.date.fromisoformat(start_date)).days -%}
    select dateadd(day, row_number() over (order by seq4()) - 1, '{{ start_date }}'::date) as d
    from table(generator(rowcount => {{ n_days }}))
{%- endmacro %}

{# Full English month name, e.g. 'January'. #}
{% macro month_name(expr) -%}
    {{ return(adapter.dispatch('month_name', 'vroomcheck')(expr)) }}
{%- endmacro %}
{% macro default__month_name(expr) -%}
    monthname({{ expr }})
{%- endmacro %}
{% macro snowflake__month_name(expr) -%}
    to_char({{ expr }}, 'MMMM')
{%- endmacro %}

{# ISO day of week: 1 = Monday ... 7 = Sunday. #}
{% macro iso_day_of_week(expr) -%}
    {{ return(adapter.dispatch('iso_day_of_week', 'vroomcheck')(expr)) }}
{%- endmacro %}
{% macro default__iso_day_of_week(expr) -%}
    isodow({{ expr }})
{%- endmacro %}
{% macro snowflake__iso_day_of_week(expr) -%}
    dayofweekiso({{ expr }})
{%- endmacro %}
