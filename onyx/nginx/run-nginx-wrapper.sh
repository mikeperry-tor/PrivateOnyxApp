#!/bin/sh

set -eu

template_dir=${PRIVATE_ONYX_NGINX_TEMPLATE_DIR:-/nginx-templates}
conf_dir=${PRIVATE_ONYX_NGINX_CONF_DIR:-/etc/nginx/conf.d}
server_include=${PRIVATE_ONYX_NGINX_SERVER_INCLUDE:-/etc/nginx/wrapper/webui-reconnect-server.inc}
asset=${PRIVATE_ONYX_NGINX_ASSET:-/usr/share/private-onyx/webui-reconnect.js}
nginx_bin=${PRIVATE_ONYX_NGINX_BIN:-nginx}
validate_only=${PRIVATE_ONYX_NGINX_VALIDATE_ONLY:-false}

fail() {
    echo "ERROR: private Onyx nginx reconnect setup: $*" >&2
    exit 1
}

require_file() {
    [ -f "$1" ] && [ -r "$1" ] || fail "required file is unreadable: $1"
}

count_exact() {
    awk -v expected="$2" '$0 == expected { count += 1 } END { print count + 0 }' "$1"
}

count_contains() {
    awk -v expected="$2" 'index($0, expected) { count += 1 } END { print count + 0 }' "$1"
}

require_file "$template_dir/app.conf.template"
require_file "$template_dir/run-nginx.sh"
require_file "$server_include"
require_file "$asset"

module_output=$("$nginx_bin" -V 2>&1) || fail "nginx -V failed"
case "$module_output" in
    *--with-http_sub_module*) ;;
    *) fail "selected nginx image lacks --with-http_sub_module" ;;
esac

rm -f "$conf_dir/default.conf"
cp -a "$template_dir/." "$conf_dir/"

template="$conf_dir/app.conf.template"
runner_source="$conf_dir/run-nginx.sh"
require_file "$template"
require_file "$runner_source"

[ "$(count_exact "$template" 'server {')" -eq 1 ] || fail "expected exactly one server insertion marker"
[ "$(count_exact "$template" '    location / {')" -eq 1 ] || fail "expected exactly one ordinary WebUI location marker"
[ "$(count_contains "$template" 'PRIVATE_ONYX_WEBUI_RECONNECT_')" -eq 0 ] || fail "source template already contains wrapper markers"

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM
transformed="$work_dir/app.conf.template"

awk '
    $0 == "server {" {
        print
        print "    # PRIVATE_ONYX_WEBUI_RECONNECT_SERVER_INCLUDE"
        print "    include /etc/nginx/wrapper/webui-reconnect-server.inc;"
        next
    }
    $0 == "    location / {" {
        print
        print "        # PRIVATE_ONYX_WEBUI_RECONNECT_HTML_ENCODING"
        print "        proxy_set_header Accept-Encoding $private_onyx_webui_accept_encoding;"
        next
    }
    { print }
' "$template" > "$transformed"
for marker in PRIVATE_ONYX_WEBUI_RECONNECT_SERVER_INCLUDE PRIVATE_ONYX_WEBUI_RECONNECT_HTML_ENCODING; do
    [ "$(count_contains "$transformed" "$marker")" -eq 1 ] || fail "transformed template has an invalid $marker count"
done
cp "$transformed" "$template"

runner="$work_dir/run-nginx.sh"
sed 's/\r$//' "$runner_source" > "$work_dir/run-nginx.normalized.sh"
start_marker='while :; do sleep 6h & wait; nginx -s reload; done & nginx -g "daemon off;"'
[ "$(count_exact "$work_dir/run-nginx.normalized.sh" "$start_marker")" -eq 1 ] || fail "expected exactly one pinned nginx start marker"

if [ "$validate_only" = "true" ]; then
    awk '{
        gsub("/etc/nginx/conf.d/", "${PRIVATE_ONYX_NGINX_CONF_DIR:-/etc/nginx/conf.d}/")
        print
    }' "$work_dir/run-nginx.normalized.sh" > "$work_dir/run-nginx.testable.sh"
else
    cp "$work_dir/run-nginx.normalized.sh" "$work_dir/run-nginx.testable.sh"
fi

awk -v marker="$start_marker" -v validate_only="$validate_only" '
    $0 == marker {
        print "# PRIVATE_ONYX_WEBUI_RECONNECT_NGINX_TEST"
        print "if ! nginx -t; then"
        print "    echo \"ERROR: private Onyx generated nginx configuration is invalid\" >&2"
        print "    exit 1"
        print "fi"
        if (validate_only == "true") {
            print "exit 0"
            next
        }
    }
    { print }
' "$work_dir/run-nginx.testable.sh" > "$runner"
[ "$(count_contains "$runner" 'PRIVATE_ONYX_WEBUI_RECONNECT_NGINX_TEST')" -eq 1 ] || fail "derived runner has an invalid nginx-test marker count"
chmod 0700 "$runner"

echo "private Onyx WebUI reconnect nginx integration prepared"
exec /bin/sh "$runner" app.conf.template
