"""Browser smoke on nginx's internal frontend route, without account credentials.

Run inside the API image with PYTHONPATH cleared for this test process so the
browser acts as an internal WebUI client, not a public-crawler helper. The
container must retain its internal-only application networks. Authenticated
chat, streaming recovery, and uploads require separate live validation.
"""
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page()
            errors = []
            external_requests = []
            def block_external(route):
                external_requests.append(route.request.url)
                route.abort()
            page.route("https://example.com/**", block_external)
            page.route("**/__private-onyx-csp-eval-test.js", lambda route: route.fulfill(
                content_type="application/javascript",
                body='try { eval("window.__cspEvalRan = true"); } catch { window.__cspEvalBlocked = true; }',
            ))
            page.on("pageerror", lambda error: errors.append(str(error)))
            response = page.goto("http://nginx/auth/login", wait_until="networkidle")
            assert response is not None and response.status == 200
            assert page.locator('input[name="email"]').count() == 1
            assert page.locator('input[type="password"]').count() == 1
            assert not errors, errors
            policies = response.headers["content-security-policy"]
            assert "script-src-attr 'none'" in policies
            assert "img-src 'self' blob: data:" in policies
            result = page.evaluate("""async () => {
                const violations = [];
                document.addEventListener('securitypolicyviolation', event => {
                    violations.push(event.effectiveDirective);
                });
                const image = new Image();
                const imageDone = new Promise(resolve => {
                    image.onload = () => resolve('loaded');
                    image.onerror = () => resolve('blocked');
                });
                image.src = 'https://example.com/private-onyx-csp-fixture.png';
                document.body.appendChild(image);
                let fetchBlocked = false;
                try { await fetch('https://example.com/private-onyx-csp-fixture'); }
                catch { fetchBlocked = true; }
                const imageResult = await imageDone;
                const sameOrigin = await fetch('/api/health');
                await new Promise(resolve => setTimeout(resolve, 100));
                image.remove();
                return {imageResult, fetchBlocked, violations, sameOrigin: sameOrigin.status};
            }""")
            assert result["imageResult"] == "blocked", result
            assert result["fetchBlocked"], result
            assert {"img-src", "connect-src"} <= set(result["violations"]), result
            assert result["sameOrigin"] == 200, result
            previews = page.evaluate("""async () => {
                const png = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='), c => c.charCodeAt(0));
                const imageURL = URL.createObjectURL(new Blob([png], {type:'image/png'}));
                const documentURL = URL.createObjectURL(new Blob(['local document'], {type:'application/octet-stream'}));
                const workerURL = URL.createObjectURL(new Blob(['postMessage("local worker")'], {type:'text/javascript'}));
                const frameURL = URL.createObjectURL(new Blob(['<!doctype html><p>local preview</p>'], {type:'text/html'}));
                async function image(src) {
                    const element = new Image();
                    const done = new Promise(resolve => {
                        element.onload = () => resolve(true);
                        element.onerror = () => resolve(false);
                    });
                    element.src = src; document.body.appendChild(element);
                    const result = await done; element.remove(); return result;
                }
                const blobImage = await image(imageURL);
                const dataImage = await image('data:image/png;base64,' + btoa(String.fromCharCode(...png)));
                const blobFetch = await (await fetch(documentURL)).text();
                const worker = new Worker(workerURL);
                const workerResult = await new Promise(resolve => {
                    worker.onmessage = event => resolve(event.data);
                    worker.onerror = () => resolve('failed');
                });
                worker.terminate();
                const frame = document.createElement('iframe');
                const frameResult = new Promise(resolve => {
                    frame.onload = () => resolve(frame.contentDocument.body.innerText);
                });
                frame.src = frameURL; document.body.appendChild(frame);
                const blobFrame = await frameResult; frame.remove();
                for (const url of [imageURL, documentURL, workerURL, frameURL]) URL.revokeObjectURL(url);
                return {blobImage, dataImage, blobFetch, workerResult, blobFrame};
            }""")
            assert previews == {
                "blobImage": True, "dataImage": True, "blobFetch": "local document",
                "workerResult": "local worker", "blobFrame": "local preview",
            }, previews
            blocked = page.evaluate("""async () => {
                const violations = [];
                const listener = event => violations.push(event.effectiveDirective);
                document.addEventListener('securitypolicyviolation', listener);
                const script = document.createElement('script');
                script.src = 'https://example.com/private-onyx-script.js';
                document.head.appendChild(script);
                const frame = document.createElement('iframe');
                frame.src = 'https://example.com/private-onyx-frame'; document.body.appendChild(frame);
                const audio = document.createElement('audio');
                audio.src = 'https://example.com/private-onyx-audio.mp3'; audio.load();
                const font = new FontFace('fixture', 'url(https://example.com/private-onyx-font.woff2)');
                await font.load().catch(() => {});
                const button = document.createElement('button');
                button.setAttribute('onclick', 'window.__cspEventRan = true');
                document.body.appendChild(button); button.click();
                // DevTools evaluation bypasses unsafe-eval enforcement. Exercise
                // eval from an ordinary page script instead.
                const evalScript = document.createElement('script');
                evalScript.src = '/__private-onyx-csp-eval-test.js';
                document.head.appendChild(evalScript);
                await new Promise(resolve => { evalScript.onload = resolve; evalScript.onerror = resolve; });
                evalScript.remove();
                await new Promise(resolve => setTimeout(resolve, 200));
                for (const element of [script, frame, audio, button]) element.remove();
                document.removeEventListener('securitypolicyviolation', listener);
                return {violations, evalBlocked: !!window.__cspEvalBlocked, eventRan: !!window.__cspEventRan, evalRan: !!window.__cspEvalRan};
            }""")
            assert {"script-src-elem", "frame-src", "media-src", "font-src", "script-src-attr", "script-src"} <= set(blocked['violations']), blocked
            assert blocked['evalBlocked'] and not blocked['eventRan'] and not blocked['evalRan'], blocked
            assert not external_requests, external_requests
            print("WEBUI_LOGIN_HYDRATION_AND_CSP_OK")
            print("WEBUI_LOCAL_PREVIEW_AND_ACTIVE_CONTENT_CSP_OK")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
