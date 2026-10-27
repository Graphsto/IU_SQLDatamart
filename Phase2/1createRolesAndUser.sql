--Create roles and user for the Book Exchange Application
--
--admin_user: owner of the database (types, tables, functions, views, dummy data).
--app_user:   login for the application / API.
--            gets only the privileges from 8.1grantPrivileges.sql.
DO $$ BEGIN
    CREATE ROLE admin_user LOGIN PASSWORD 'placeholder_admin_password';
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE ROLE app_user LOGIN PASSWORD 'placeholder_app_password';
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
