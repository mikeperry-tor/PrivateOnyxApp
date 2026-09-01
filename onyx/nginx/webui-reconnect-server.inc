# This exact asset is wrapper-owned and never reaches an Onyx upstream.
location = /_private-onyx/webui-reconnect.js {
    alias /usr/share/private-onyx/webui-reconnect.js;
    default_type application/javascript;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Cache-Control "no-store" always;
}

# Keep misspelled or future wrapper paths from falling through to the WebUI.
location ^~ /_private-onyx/ {
    return 404;
}

sub_filter_once on;
sub_filter '</head>' $private_onyx_webui_head_replacement;
