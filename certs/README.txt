Optional. Only needed if your database provider gives you a CA certificate file and you want the server verified.
Save it here (e.g. ca.pem; it is a PUBLIC certificate, safe to commit) and set DB_SSL_CA=/app/certs/ca.pem.
For TiDB Cloud and most setups you do not need this: set DB_SSL_MODE=REQUIRED instead.
