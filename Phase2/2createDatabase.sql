CREATE DATABASE buechertausch
WITH OWNER = admin_user
ENCODING = 'UTF8'
ICU_LOCALE = 'de-DE'
LOCALE_PROVIDER = 'icu'
TEMPLATE = template0;

ALTER DATABASE "buechertausch" SET idle_in_transaction_session_timeout = '60s';




