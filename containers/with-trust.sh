#!/bin/sh
# A temporary combined CA bundle may be provided as a build secret; never copy it.
set -eu
if [ -r /run/secrets/proxy_ca ]; then
    export SSL_CERT_FILE=/run/secrets/proxy_ca
    export REQUESTS_CA_BUNDLE=$SSL_CERT_FILE
    export CURL_CA_BUNDLE=$SSL_CERT_FILE
fi
exec "$@"
