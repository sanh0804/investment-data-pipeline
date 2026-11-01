{% macro literal(value) -%}
'{{ (value | string).replace("'", "''") }}'
{%- endmacro %}

{% macro cutoff_date() -%}
  {% set value = var('as_of_date') | string %}
  {% set parsed = modules.datetime.date.fromisoformat(value) %}
  {{ literal(parsed.isoformat()) }}::date
{%- endmacro %}

{% macro key(columns) -%}
md5(jsonb_build_array({{ columns | join(', ') }})::text)
{%- endmacro %}

{% macro generate_schema_name(custom_schema_name=none, node=none) -%}
  {# Always use the validated target, including dbt's internal test audit schema.
     Model custom-schema config must never redirect writes into source schemas. #}
  {% if target.schema != 'analytics' and not target.schema.startswith('analytics_') %}
    {{ exceptions.raise_compiler_error('DBT_SCHEMA must be analytics or start with analytics_; source schemas are protected') }}
  {% endif %}
  {{ target.schema }}
{%- endmacro %}

{% test unique_key(model, columns) %}
select {{ columns | join(', ') }}, count(*)
from {{ model }}
group by {{ columns | join(', ') }} having count(*) > 1
{% endtest %}
