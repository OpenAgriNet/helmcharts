# A separate file because the block beside it is rendered through envsubst and
# these $ names are nginx's own, not ours to substitute.
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
