-- Privileges for app_user (least privilege).

-- Database: only app_user
REVOKE ALL ON DATABASE buechertausch FROM PUBLIC;
GRANT CONNECT ON DATABASE buechertausch TO app_user;

-- Schema: use existing objects, but not create new ones
GRANT USAGE ON SCHEMA public TO app_user;

-- Tables: data access only, no DDL. Identity columns need no extra sequence privileges.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;

-- Protocols is an audit log: append only
REVOKE UPDATE, DELETE ON "Protocols" FROM app_user;

-- Views are read only
REVOKE INSERT, UPDATE, DELETE ON available_books, overdue_lents, user_ranking FROM app_user;
