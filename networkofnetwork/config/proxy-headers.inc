# Shared by every proxied location.
#
# The signature is over the body and the created/expires pair, so the body must
# arrive byte-identical. Buffering is fine; rewriting is not.
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_request_buffering on;
