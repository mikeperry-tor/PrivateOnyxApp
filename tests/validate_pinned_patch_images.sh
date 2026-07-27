#!/bin/sh

set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
container_bin=${CONTAINER_BIN:-docker}
onyx_backend_image=${ONYX_BACKEND_IMAGE:?ONYX_BACKEND_IMAGE is required}
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
if [ "$validate_code_interpreter" = true ]; then
    [ -n "$code_interpreter_image" ] || {
        echo "ERROR: CODE_INTERPRETER_IMAGE is required for Docker validation" >&2
        exit 1
    }
    require_image "$code_interpreter_image" "Run 'make onyx-build' before 'make test-patch-images'."
    [ -n "$python_executor_image" ] || {
        echo "ERROR: PYTHON_EXECUTOR_IMAGE is required for Docker validation" >&2
        exit 1
    }
    require_image "$python_executor_image" "Run 'make executor-build' before 'make test-patch-images'."
fi
require_image "$searxng_wrapper_image" "Run 'make searxng-build' before 'make test-patch-images'."

echo "Validating API patch contracts in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -e PYTHONPATH=/app:/wrapper \
    -e WRAPPER_PATCH_STRICT=true \
    -e GEN_AI_MAX_TOKENS=131072 \
    -e ONYX_AGENT_USE_NATIVE_REASONING=true \
    -e ONYX_AGENT_PRESERVE_TOOL_RESULTS=true \
    -e ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT=4000 \
    -e ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS=16000 \
    -e ONYX_OPEN_URL_MAX_CHARS_PER_URL=4000 \
    -e ONYX_OPEN_URL_MAX_TOTAL_CHARS=16000 \
    -v "$repo_root/onyx/patches/shared:/wrapper:ro" \
    -v "$repo_root/tests/validate_pinned_api.py:/validation/validate_pinned_api.py:ro" \
    "$onyx_backend_image" \
    /validation/validate_pinned_api.py

echo "Validating stock open_url crawler patch in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -e PYTHONPATH=/obscura-client \
    -e ONYX_HELPER_HTTP_PROXY_URL=http://onyx-public-egress-bridge:3128 \
    -e EGRESS_ALLOW_HTTP_URLS=false \
    -e ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB=7 \
    -v "$repo_root/browser/obscura_client:/obscura-client:ro" \
    -v "$repo_root/onyx/patches/sitecustomize_api_server:/api-patches:ro" \
    "$onyx_backend_image" \
    -c "import importlib.util, sys; s=importlib.util.spec_from_file_location('stock_crawler_patch_validation', '/api-patches/onyx_crawler_egress_patch.py'); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); m.install(); from onyx.tools.tool_implementations.open_url.onyx_web_crawler import OnyxWebCrawler; c=OnyxWebCrawler(max_pdf_size_bytes=1, max_html_size_bytes=1); assert c._max_pdf_size_bytes == 7 * 1024 * 1024; assert c._max_html_size_bytes == 7 * 1024 * 1024; print('PINNED_STOCK_CRAWLER_PATCH_CONTRACT_OK')"

echo "Validating direct-Obscura open_url crawler patch in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -e PYTHONPATH=/obscura-client \
    -e ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB=7 \
    -e OBSCURA_BROWSER_WAIT_UNTIL_WEB=domcontentloaded \
    -v "$repo_root/browser/obscura_client:/obscura-client:ro" \
    -v "$repo_root/onyx/patches/sitecustomize_api_server:/api-patches:ro" \
    "$onyx_backend_image" \
    -c "import importlib.util, sys; s=importlib.util.spec_from_file_location('obscura_crawler_patch_validation', '/api-patches/obscura_crawler_patch.py'); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); m.install(); assert m.DOCUMENT_LIMIT_BYTES == 7 * 1024 * 1024; from onyx.tools.tool_implementations.open_url.onyx_web_crawler import OnyxWebCrawler; assert OnyxWebCrawler.contents.__module__ == 'obscura_crawler_patch_validation'; print('PINNED_OBSCURA_CRAWLER_PATCH_CONTRACT_OK')"

echo "Validating explicit open_url call limit in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -v "$repo_root/onyx/patches/sitecustomize_api_server:/api-patches:ro" \
    "$onyx_backend_image" \
    -c "import sys; sys.path.insert(0, '/api-patches'); import open_url_failure_reporting_patch as f, open_url_limit_patch as m; f.install(); m.install(); from onyx.tools.models import OpenURLToolOverrideKwargs, ToolCallException; from onyx.tools.tool_implementations.open_url.open_url_tool import OpenURLTool; t=object.__new__(OpenURLTool); d=t.tool_definition(); assert d['function']['parameters']['properties']['urls']['maxItems'] == 10; o=OpenURLToolOverrideKwargs(starting_citation_num=1, citation_mapping={}, url_snippet_map={}); u=[f'https://example.com/{i}' for i in range(11)]; caught=False; message=''; exec('try:\\n t.run(None, o, urls=u)\\nexcept ToolCallException as e:\\n caught=True\\n message=e.llm_facing_message'); assert caught and 'at most 10 URLs' in message and 'No URLs from this call were opened' in message; print('PINNED_OPEN_URL_LIMIT_CONTRACT_OK')"

echo "Validating background PDF freshness contracts in $onyx_backend_image"
"$container_bin" run --rm \
    --network none \
    --entrypoint python \
    -e PYTHONPATH=/app:/shared \
    -e WRAPPER_PATCH_STRICT=true \
    -e ENABLE_CRAFT=false \
    -e ONYX_DISABLE_VESPA=true \
    -e AUTO_LLM_CONFIG_URL= \
    -e LICENSE_ENFORCEMENT_ENABLED=false \
    -e ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=false \
    -e ONYX_AGENT_SLACK_BOT=false \
    -e ONYX_AGENT_DISCORD_BOT=false \
    -e ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED=false \
    -e ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS=doc-drop-web \
    -e ONYX_HELPER_HTTP_PROXY_URL=http://onyx-public-egress-bridge:3128 \
    -e ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL=http://onyx-host-egress-bridge:3128 \
    -e ONYX_CONFIGURED_INFERENCE_INTERNAL_BASE_URL=http://teep:8337/v1 \
    -e ONYX_WEB_CONNECTOR_PUBLIC_HTTP_PROXY_URL=http://onyx-public-egress-bridge:3128 \
    -e ONYX_WEB_CONNECTOR_HOST_HTTP_PROXY_URL=http://onyx-host-egress-bridge:3128 \
    -e ONYX_WEB_CONNECTOR_INTERNAL_BASE_URL=http://doc-drop-web:8091/ \
    -e ONYX_WEB_CONNECTOR_DISPLAY_BASE_URL=http://localhost:3000/doc-drop/ \
    -v "$repo_root/onyx/patches/shared:/shared:ro" \
    -v "$repo_root/onyx/patches/sitecustomize_background:/background:ro" \
    -v "$repo_root/onyx/background_entrypoint.py:/wrapper-background-entrypoint.py:ro" \
    -v "$repo_root/onyx/beat_liveness_watchdog.py:/wrapper-beat-liveness-watchdog.py:ro" \
    -v "$repo_root/tests/validate_pinned_background.py:/validation/validate_pinned_background.py:ro" \
    "$onyx_backend_image" \
    /validation/validate_pinned_background.py

if [ "$validate_code_interpreter" = true ]; then
    echo "Validating SymPy in $python_executor_image"
    "$container_bin" run --rm \
        --network none \
        --entrypoint python \
        "$python_executor_image" \
        -c "import sympy; x = sympy.symbols('x'); assert sympy.__version__ == '1.14.0'; assert sympy.solve(x**2 - 4, x) == [-2, 2]; print('PINNED_EXECUTOR_SYMPY_OK')"

    echo "Validating executor command contract in $code_interpreter_image"
    "$container_bin" run --rm \
        --network none \
        --entrypoint python \
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
