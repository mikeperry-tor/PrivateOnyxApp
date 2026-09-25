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
            print("WEBUI_LOGIN_HYDRATION_AND_CSP_OK")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
