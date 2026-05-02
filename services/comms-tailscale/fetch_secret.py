import os, sys
from infisical_client import ClientSettings, GetSecretOptions, InfisicalClient

name = sys.argv[1]
ic = InfisicalClient(ClientSettings(
    client_id=os.environ["INFISICAL_CLIENT_ID"],
    client_secret=os.environ["INFISICAL_CLIENT_SECRET"],
    site_url=os.environ.get("INFISICAL_URL", "http://infisical:8080"),
))
val = ic.getSecret(GetSecretOptions(
    environment=os.environ.get("INFISICAL_ENVIRONMENT", "prod"),
    project_id=os.environ["INFISICAL_PROJECT_ID"],
    secret_name=name,
))
print(val.secret_value, end="")
