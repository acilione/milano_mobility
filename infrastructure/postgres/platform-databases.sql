\set ON_ERROR_STOP on

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'airflow_user', :'airflow_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'airflow_user')\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'metabase_user', :'metabase_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'metabase_user')\gexec

SELECT format('CREATE DATABASE airflow OWNER %I', :'airflow_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'airflow')\gexec
SELECT format('CREATE DATABASE metabase OWNER %I', :'metabase_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'metabase')\gexec
