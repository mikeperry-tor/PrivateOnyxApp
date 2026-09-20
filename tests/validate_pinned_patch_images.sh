#!/bin/sh

set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
container_bin=${CONTAINER_BIN:-docker}
onyx_backend_image=${ONYX_BACKEND_IMAGE:?ONYX_BACKEND_IMAGE is required}
onyx_web_server_image=${ONYX_WEB_SERVER_IMAGE:?ONYX_WEB_SERVER_IMAGE is required}
nginx_image=${NGINX_IMAGE:?NGINX_IMAGE is required}
code_interpreter_image=${CODE_INTERPRETER_IMAGE:-}
python_executor_image=${PYTHON_EXECUTOR_IMAGE:-}
searxng_wrapper_image=${SEARXNG_WRAPPER_IMAGE:?SEARXNG_WRAPPER_IMAGE is required}

case "${container_bin##*/}" in
    *podman*) validate_code_interpreter=false ;;
    *) validate_code_interpreter=true ;;
esac

require_image() {
    image=$1
    guidance=$2
    if ! "$container_bin" image inspect "$image" >/dev/null 2>&1; then
        echo "ERROR: required pinned validation image is missing: $image" >&2
        echo "       $guidance" >&2
        exit 1
    fi
}

require_image "$onyx_backend_image" "Run 'make onyx-build' before 'make test-patch-images'."
require_image "$onyx_web_server_image" "Run 'make onyx-build' before 'make test-patch-images'."
require_image "$nginx_image" "Install the pinned nginx support image before 'make test-patch-images'."
if [ "$validate_code_interpreter" = true ]; then
    [ -n "$code_interpreter_image" ] || {
        echo "ERROR: CODE_INTERPRETER_IMAGE is required for Docker validation" >&2
        exit 1
    }
    require_image "$code_interpreter_image" "Run 'make code-interpreter-build' before 'make test-patch-images'."
    [ -n "$python_executor_image" ] || {
        echo "ERROR: PYTHON_EXECUTOR_IMAGE is required for Docker validation" >&2
        exit 1
    }
    require_image "$python_executor_image" "Run 'make executor-build' before 'make test-patch-images'."
fi
require_image "$searxng_wrapper_image" "Run 'make searxng-build' before 'make test-patch-images'."

tokenizer_tmp=$(mktemp -d)
trap 'rm -rf "$tokenizer_tmp"' EXIT HUP INT TERM
python3 "$repo_root/onyx/bootstrap_tokenizer_cache.py" \
    --container-bin "$container_bin" \
    --image "$onyx_backend_image" \
    --output "$tokenizer_tmp/tokenizer.json"

echo "Validating offline embedding tokenizer contract in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -e PYTHONPATH=/app \
    -e WRAPPER_PATCH_STRICT=true \
    -e ONYX_EMBEDDING_TOKENIZER_FILE=/offline-tokenizer/tokenizer.json \
    -v "$repo_root/onyx/patches/onyx_wrapper_patches:/app/onyx_wrapper_patches:ro" \
    -v "$tokenizer_tmp/tokenizer.json:/offline-tokenizer/tokenizer.json:ro" \
    "$onyx_backend_image" \
    -c "from onyx_wrapper_patches.shared import embedding_tokenizer as p; p.apply_embedding_tokenizer_alias_patch(); from onyx.natural_language_processing.utils import HuggingFaceTokenizer; tokenizers=[HuggingFaceTokenizer(name) for name in ('nomic-ai/nomic-embed-text-v1', 'nomic-ai/nomic-embed-text-v23')]; assert all(t.encoder.encode('offline tokenizer').tokens for t in tokenizers); print('PINNED_OFFLINE_TOKENIZER_CONTRACT_OK')"

echo "Validating WebUI privacy and streaming contracts in $onyx_web_server_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint node \
    -v "$repo_root/tests/validate_pinned_webui_settings.js:/validate-settings.js:ro" \
    "$onyx_web_server_image" \
    -e 'const fs=require("fs"),path=require("path"); for (const name of ["NEXT_PUBLIC_POSTHOG_KEY","NEXT_PUBLIC_POSTHOG_HOST","NEXT_PUBLIC_CLOUD_ENABLED","NEXT_PUBLIC_SENTRY_DSN","NEXT_PUBLIC_GTM_ENABLED","NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY","NEXT_PUBLIC_RECAPTCHA_SITE_KEY"]) { if (process.env[name]) throw new Error(`${name} is enabled in the pinned image`); } if (process.env.ONYX_VERSION !== "v4.6.9") throw new Error(`unexpected ONYX_VERSION=${process.env.ONYX_VERSION}`); const chunks=[]; const visit=(dir)=>{for(const entry of fs.readdirSync(dir,{withFileTypes:true})){const item=path.join(dir,entry.name); if(entry.isDirectory()) visit(item); else if(item.endsWith(".js")) chunks.push(fs.readFileSync(item,"utf8"));}}; visit("/app/.next"); const bundle=chunks.join("\n"); for(const marker of ["/api/chat/send-chat-message","/resume-stream?cursor=","chat_heartbeat","message_start","message_delta","reasoning_start","reasoning_delta","reasoning_done","stop_reason","Failed to resume in-flight run","Server did not honor the incognito request","Unknown packet:"]) { if(!bundle.includes(marker)) throw new Error(`missing WebUI streaming marker: ${marker}`); } console.log("PINNED_WEBUI_PRIVACY_CONTRACT_OK"); console.log("PINNED_WEBUI_STREAMING_CONTRACT_OK"); require("/validate-settings.js");'

echo "Validating wrapper WebUI reconnect companion in $onyx_web_server_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint node \
    -v "$repo_root:/workspace:ro" \
    -w /workspace \
    "$onyx_web_server_image" \
    tests/webui_reconnect_harness.js onyx/nginx/webui-reconnect.js

echo "Validating nginx reconnect injection in $nginx_image"
python3 "$repo_root/tests/validate_nginx_reconnect_image.py" \
    --container-bin "$container_bin" \
    --image "$nginx_image" \
    --client-image "$onyx_backend_image" \
    --repo-root "$repo_root"

echo "Validating inert canonical patch package imports in $onyx_backend_image"
"$container_bin" run --rm --network none --entrypoint python \
    -e PYTHONPATH=/app:/obscura-client \
    -v "$repo_root/onyx/patches/onyx_wrapper_patches:/app/onyx_wrapper_patches:ro" \
    -v "$repo_root/browser/obscura_client:/obscura-client:ro" \
    -v "$repo_root/tests/validate_patch_package.py:/validation/validate_patch_package.py:ro" \
    "$onyx_backend_image" /validation/validate_patch_package.py

mkdir -p "$tokenizer_tmp/shadow"
echo "Validating API patch contracts in $onyx_backend_image"
run_api_validation() {
"$container_bin" run --rm \
    --network none \
    -e PATCH_PROBE_BOOTSTRAP=/api-patches/sitecustomize.py \
    -e PATCH_PROBE_MODULES=onyx_wrapper_patches.shared.embedding_tokenizer:onyx_wrapper_patches.shared.playwright_proxy:onyx_wrapper_patches.shared.inference_proxy \
    -v "$repo_root/tests/patch_activation_probe.py:/app/patch_activation_probe.py:ro" \
    --entrypoint python \
    -e PYTHONPATH="${validation_pythonpath:-/api-patches:/obscura-client:/app}" \
    -v "$tokenizer_tmp/shadow:/shadow:ro" \
    -w /tmp \
    -e WRAPPER_PATCH_STRICT=true \
    -e ENABLE_CRAFT=false \
    -e IDP_PROFILE_ENRICHMENT_ENABLED=false \
    -e LICENSE_ENFORCEMENT_ENABLED=false \
    -e ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=false \
    -e LITELLM_LOCAL_MODEL_COST_MAP=true \
    -e LLM_FIRST_CHUNK_MAX_RETRIES=1 \
    -e ONYX_LLM_NATIVE_TOOL_CALLS_ONLY=true \
    -e GEN_AI_MAX_TOKENS=131072 \
    -e ONYX_AGENT_USE_NATIVE_REASONING=true \
    -e ONYX_AGENT_PRESERVE_TOOL_RESULTS=true \
    -e ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT=4000 \
    -e ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS=16000 \
    -e ONYX_OPEN_URL_MAX_CHARS_PER_URL=4000 \
    -e ONYX_OPEN_URL_MAX_TOTAL_CHARS=16000 \
    -e ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB=7 \
    -e CHAT_STREAM_BUFFER_TTL_S=14400 \
    -e CHAT_STREAM_BUFFER_DONE_TTL_S=3600 \
    -e CHAT_STREAM_BUFFER_MAX_BYTES=33554432 \
    -e ONYX_EMBEDDING_TOKENIZER_FILE=/offline-tokenizer/tokenizer.json \
    -e ONYX_AGENT_USE_OBSCURA_BROWSER="${validation_obscura:-true}" \
    -e ONYX_HELPER_HTTP_PROXY_URL=http://onyx-public-egress-bridge:3128 \
    -e ONYX_MCP_PUBLIC_HTTP_PROXY_URL=http://onyx-public-egress-bridge:3128 \
    -e ONYX_MCP_HOST_HTTP_PROXY_URL=http://onyx-host-egress-bridge:3128 \
    -e ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL=http://onyx-host-egress-bridge:3128 \
    -e ONYX_CONFIGURED_INFERENCE_INTERNAL_BASE_URL=http://teep:8337/v1 \
    -e ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true \
    -v "${validation_bootstrap_source:-$repo_root/onyx/patches/sitecustomize_api_server}:/api-patches:ro" \
    -v "${validation_package_source:-$repo_root/onyx/patches/onyx_wrapper_patches}:/app/onyx_wrapper_patches:ro" \
    -v "$repo_root/browser/obscura_client:/obscura-client:ro" \
    -v "$tokenizer_tmp/tokenizer.json:/offline-tokenizer/tokenizer.json:ro" \
    -v "$repo_root/tests/validate_pinned_api.py:/validation/validate_pinned_api.py:ro" \
    -v "$repo_root/tests/validate_prompt_stability.py:/validation/validate_prompt_stability.py:ro" \
    -v "$repo_root/tests/validate_reasoning_tool_availability.py:/validation/validate_reasoning_tool_availability.py:ro" \
    -v "$repo_root/tests/validate_native_bot_tools.py:/validation/validate_native_bot_tools.py:ro" \
    "$onyx_backend_image" \
    "$@"
}
run_api_validation /validation/validate_pinned_api.py
( validation_obscura=false run_api_validation /validation/validate_pinned_api.py )
run_api_validation /app/patch_activation_probe.py
# Missing discovery is a harness failure, not runtime enforcement.
mkdir -p "$tokenizer_tmp/empty" "$tokenizer_tmp/shadow"
printf '%s\n' '# inert wrong-origin bootstrap' > "$tokenizer_tmp/shadow/sitecustomize.py"
expect_startup_failure() {
    expected_status="$1"
    shift
    if "$@" > "$tokenizer_tmp/negative.stdout" 2> "$tokenizer_tmp/negative.stderr"; then
        echo "ERROR: negative startup unexpectedly succeeded" >&2
        return 1
    else
        actual_status="$?"
    fi
    if [ "$actual_status" -ne "$expected_status" ]; then
        cat "$tokenizer_tmp/negative.stderr" >&2
        echo "ERROR: startup status $actual_status, expected $expected_status" >&2
        return 1
    fi
    if grep -q APPLICATION_BODY_EXECUTED "$tokenizer_tmp/negative.stdout"; then
        echo "ERROR: required bootstrap failure reached application body" >&2
        return 1
    fi
}
( validation_bootstrap_source="$tokenizer_tmp/empty" expect_startup_failure 1 run_api_validation -c 'from patch_activation_probe import assert_activation; assert_activation()' )
( validation_pythonpath=/obscura-client:/app expect_startup_failure 1 run_api_validation -c 'from patch_activation_probe import assert_activation; assert_activation()' )
( validation_pythonpath=/shadow:/obscura-client:/app expect_startup_failure 1 run_api_validation -c 'from patch_activation_probe import assert_activation; assert_activation()' )
( validation_package_source="$tokenizer_tmp/empty" expect_startup_failure 78 run_api_validation -c 'print("APPLICATION_BODY_EXECUTED")' )
cp -R "$repo_root/onyx/patches/onyx_wrapper_patches" "$tokenizer_tmp/drift-package"
python3 - "$tokenizer_tmp/drift-package/api/model_limits.py" <<'PYDRIFT'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text()
old = 'if "model_configuration.max_input_tokens" not in configured_source:'
assert source.count(old) == 1
path.write_text(source.replace(old, 'if "controlled_nonexistent_source_marker" not in configured_source:'))
PYDRIFT
( validation_package_source="$tokenizer_tmp/drift-package" expect_startup_failure 78 run_api_validation -c 'print("APPLICATION_BODY_EXECUTED")' )
echo "PINNED_API_MISSING_DISCOVERY_AND_REQUIRED_PACKAGE_FAILURE_OK"


echo "Validating native coding-agent repository bound in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    "$onyx_backend_image" \
    -c "import inspect; from onyx.tools.fake_tools import coding_agent; assert coding_agent.CODING_AGENT_GITHUB_MAX_REPO_BYTES == 500 * 1024 * 1024; source=inspect.getsource(coding_agent._setup_session); assert source.count('max_size_bytes=CODING_AGENT_GITHUB_MAX_REPO_BYTES,') == 1; print('PINNED_NATIVE_CODING_AGENT_REPO_LIMIT_OK')"

echo "Validating complete URL identity in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -v "$repo_root/onyx/patches/onyx_wrapper_patches:/app/onyx_wrapper_patches:ro" \
    "$onyx_backend_image" \
    -c "from onyx_wrapper_patches.api import url_identity_preservation_patch as m; m.install(); from onyx.tools.tool_implementations.web_search.models import WebSearchResult; from onyx.tools.tool_implementations.open_url.models import WebContent; from onyx.tools.tool_implementations.open_url.url_normalization import normalize_url as normalize_open_url; from onyx.utils.url import normalize_url; u='https://news.ycombinator.com/item?id=46850588&ref=search#comments'; assert WebSearchResult(title='HN', link=u, snippet='test').link == u; assert WebContent(title='HN', link=u, full_content='test').link == u; assert normalize_url(u) == u; assert normalize_open_url(u) == u; print('PINNED_URL_IDENTITY_PRESERVATION_OK')"

echo "Validating stock open_url crawler patch in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -e PYTHONPATH=/obscura-client:/app \
    -e ONYX_HELPER_HTTP_PROXY_URL=http://onyx-public-egress-bridge:3128 \
    -e EGRESS_ALLOW_HTTP_URLS=false \
    -e ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB=7 \
    -v "$repo_root/browser/obscura_client:/obscura-client:ro" \
    -v "$repo_root/onyx/patches/onyx_wrapper_patches:/app/onyx_wrapper_patches:ro" \
    "$onyx_backend_image" \
    -c "from onyx_wrapper_patches.api import onyx_crawler_egress_patch as m; m.install(); from onyx.tools.tool_implementations.open_url.onyx_web_crawler import OnyxWebCrawler; c=OnyxWebCrawler(max_pdf_size_bytes=1, max_html_size_bytes=1); assert c._max_pdf_size_bytes == 7 * 1024 * 1024; assert c._max_html_size_bytes == 7 * 1024 * 1024; print('PINNED_STOCK_CRAWLER_PATCH_CONTRACT_OK')"

echo "Validating direct-Obscura open_url crawler patch in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -e PYTHONPATH=/obscura-client:/app \
    -e ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB=7 \
    -e OBSCURA_BROWSER_WAIT_UNTIL_WEB=domcontentloaded \
    -v "$repo_root/browser/obscura_client:/obscura-client:ro" \
    -v "$repo_root/onyx/patches/onyx_wrapper_patches:/app/onyx_wrapper_patches:ro" \
    "$onyx_backend_image" \
    -c "from onyx_wrapper_patches.api import obscura_crawler_patch as m; m.install(); assert m.DOCUMENT_LIMIT_BYTES == 7 * 1024 * 1024; from onyx.tools.tool_implementations.open_url.onyx_web_crawler import OnyxWebCrawler; assert OnyxWebCrawler.contents.__module__ == 'onyx_wrapper_patches.api.obscura_crawler_patch'; print('PINNED_OBSCURA_CRAWLER_PATCH_CONTRACT_OK')"

echo "Validating explicit open_url call limit in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -v "$repo_root/onyx/patches/onyx_wrapper_patches:/app/onyx_wrapper_patches:ro" \
    "$onyx_backend_image" \
    -c "from onyx_wrapper_patches.api import open_url as m; m.install_failure_reporting(); m.install_url_limit(); from onyx.tools.models import OpenURLToolOverrideKwargs, ToolCallException; from onyx.tools.tool_implementations.open_url.open_url_tool import OpenURLTool; t=object.__new__(OpenURLTool); t._web_fetch_disabled=False; d=t.tool_definition(); assert d['function']['parameters']['properties']['urls']['maxItems'] == 10; o=OpenURLToolOverrideKwargs(starting_citation_num=1, citation_mapping={}, url_snippet_map={}); u=[f'https://example.com/{i}' for i in range(11)]; caught=False; message=''; exec('try:\\n t.run(None, o, urls=u)\\nexcept ToolCallException as e:\\n caught=True\\n message=e.llm_facing_message'); assert caught and 'at most 10 URLs' in message and 'No URLs from this call were opened' in message; print('PINNED_OPEN_URL_LIMIT_CONTRACT_OK')"

echo "Validating background PDF freshness contracts in $onyx_backend_image"
run_background_validation() {
"$container_bin" run --rm \
    --network none \
    -e PATCH_PROBE_BOOTSTRAP=/app/sitecustomize.py \
    -e PATCH_PROBE_MODULES=onyx_wrapper_patches.shared.embedding_tokenizer:onyx_wrapper_patches.shared.playwright_proxy:onyx_wrapper_patches.shared.inference_proxy \
    -v "$repo_root/tests/patch_activation_probe.py:/app/patch_activation_probe.py:ro" \
    --entrypoint python \
    -e PYTHONPATH=/app \
    -e WRAPPER_PATCH_STRICT=true \
    -e ENABLE_CRAFT=false \
    -e DISABLE_TELEMETRY=true \
    -e LITELLM_LOCAL_MODEL_COST_MAP=true \
    -e ONYX_DISABLE_VESPA=true \
    -e AUTO_LLM_CONFIG_URL= \
    -e LICENSE_ENFORCEMENT_ENABLED=false \
    -e ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=false \
    -e CODE_INTERPRETER_BASE_URL= \
    -e ONYX_AGENT_SLACK_BOT=false \
    -e ONYX_AGENT_DISCORD_BOT=false \
    -e ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED="${validation_freshness:-false}" \
    -e ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS=doc-drop-web \
    -e ONYX_HELPER_HTTP_PROXY_URL=http://onyx-public-egress-bridge:3128 \
    -e ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL=http://onyx-host-egress-bridge:3128 \
    -e ONYX_CONFIGURED_INFERENCE_INTERNAL_BASE_URL=http://teep:8337/v1 \
    -e ONYX_WEB_CONNECTOR_PUBLIC_HTTP_PROXY_URL=http://onyx-public-egress-bridge:3128 \
    -e ONYX_WEB_CONNECTOR_HOST_HTTP_PROXY_URL=http://onyx-host-egress-bridge:3128 \
    -e ONYX_WEB_CONNECTOR_INTERNAL_BASE_URL=http://doc-drop-web:8091/ \
    -e ONYX_WEB_CONNECTOR_DISPLAY_BASE_URL=http://localhost:3000/doc-drop/ \
    -v "$repo_root/onyx/patches/sitecustomize_background/sitecustomize.py:/app/sitecustomize.py:ro" \
    -v "$repo_root/onyx/patches/onyx_wrapper_patches:/app/onyx_wrapper_patches:ro" \
    -v "$repo_root/onyx/background_entrypoint.py:/wrapper-background-entrypoint.py:ro" \
    -v "$repo_root/onyx/beat_liveness_watchdog.py:/wrapper-beat-liveness-watchdog.py:ro" \
    -v "$repo_root/tests/validate_pinned_background.py:/validation/validate_pinned_background.py:ro" \
    -v "$repo_root/tests/validate_native_bot_tools.py:/validation/validate_native_bot_tools.py:ro" \
    "$onyx_backend_image" \
    "$@"
}
run_background_validation /validation/validate_pinned_background.py
( validation_freshness=true run_background_validation /validation/validate_pinned_background.py )
run_background_validation /app/patch_activation_probe.py

if [ "$validate_code_interpreter" = true ]; then
    echo "Validating executor package functionality in $python_executor_image"
    "$container_bin" run --rm \
        --network none \
        --entrypoint python \
        "$python_executor_image" \
        -c "from io import BytesIO; import sympy; from reportlab.graphics import renderPDF; from reportlab.pdfgen import canvas; from svglib.svglib import svg2rlg; x = sympy.symbols('x'); assert sympy.__version__ == '1.14.0'; assert sympy.solve(x**2 - 4, x) == [-2, 2]; pdf = BytesIO(); doc = canvas.Canvas(pdf); doc.drawString(10, 10, 'private-onyx'); doc.save(); assert pdf.getvalue().startswith(b'%PDF-'); drawing = svg2rlg(BytesIO(b'<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"12\" height=\"8\"><rect width=\"12\" height=\"8\"/></svg>')); assert drawing is not None and drawing.width > 0 and drawing.height > 0; assert renderPDF.drawToString(drawing).startswith(b'%PDF-'); print('PINNED_EXECUTOR_PACKAGES_OK')"

    echo "Validating executor command contract in $code_interpreter_image"
    "$container_bin" run --rm \
        --network none \
        --entrypoint python \
        -e PYTHON_EXECUTOR_DOCKER_IMAGE_WATCHDOG_INTERVAL_SEC=0 \
        -e PYTHON_EXECUTOR_DOCKER_NETWORK=onyx-code-interpreter-executor \
        -e 'PYTHON_EXECUTOR_DOCKER_RUN_ARGS=--env HTTP_PROXY=http://executor-egress-bridge:3128 --env HTTPS_PROXY=http://executor-egress-bridge:3128 --env ALL_PROXY=http://executor-egress-bridge:3128 --env NO_PROXY=127.0.0.1,localhost,::1 --env http_proxy=http://executor-egress-bridge:3128 --env https_proxy=http://executor-egress-bridge:3128 --env all_proxy=http://executor-egress-bridge:3128 --env no_proxy=127.0.0.1,localhost,::1' \
        -v "$repo_root/tests/validate_code_interpreter_executor_network.py:/app/validate_code_interpreter_executor_network.py:ro" \
        "$code_interpreter_image" \
        /app/validate_code_interpreter_executor_network.py
else
    echo "Skipping code-interpreter image contract: the supported Podman model omits this Docker-socket service."
fi

echo "Validating SearXNG runtime patches in $searxng_wrapper_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint /usr/local/searxng/.venv/bin/python \
    -e PYTHONPATH=/patches:/usr/local/lib:/usr/local/searxng \
    -e WRAPPER_PATCH_STRICT=true \
    -e SEARXNG_ROUND_ROBIN=true \
    -v "$repo_root/searxng/patches:/patches:ro" \
    "$searxng_wrapper_image" \
    -c "from searx.results import ResultContainer; assert getattr(ResultContainer, '_wrapper_last_resort_patch', False); print('PINNED_SEARXNG_PATCH_CONTRACTS_OK')"

echo "Running image-only SearXNG parser tests in $searxng_wrapper_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint /usr/local/searxng/.venv/bin/python \
    -e PYTHONPATH=/usr/local/lib \
    -v "$repo_root:/workspace:ro" \
    -w /workspace \
    "$searxng_wrapper_image" \
    -m unittest tests.test_searxng_obscura_engines -v

echo "Pinned-image patch validation passed."
