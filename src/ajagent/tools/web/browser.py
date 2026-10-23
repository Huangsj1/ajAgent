"""Optional, run-scoped Playwright page with bounded text output."""
from pathlib import Path
from ajagent.options import DEFAULTS
from ajagent.tools.common.base import ToolError, ToolSpec, schema
from ajagent.tools.web.fetch import web_url


class Browser:
    def __init__(self, options=DEFAULTS.capabilities, remaining=None):
        self.options, self.remaining = options, remaining
        self.runtime = self.browser = self.page = None
        self.unavailable = None

    def start(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.unavailable = 'Install the browser extra and Chromium to enable browser tools.'
            return False
        try:
            self.runtime = sync_playwright().start()
            if not Path(self.runtime.chromium.executable_path).exists():
                self.unavailable = 'Chromium is missing; run playwright install chromium.'
                self.close()
                return False
            self.browser = self.runtime.chromium.launch(timeout=self.timeout())
            self.page = self.browser.new_page(accept_downloads=False)
            return True
        except Exception:
            self.unavailable = 'Chromium could not start; browser tools were not registered.'
            self.close()
            return False

    def timeout(self):
        seconds = min(self.options.browser_timeout, self.remaining()) if self.remaining else self.options.browser_timeout
        return seconds * 1000

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.runtime:
                self.runtime.stop()
            self.runtime = self.browser = self.page = None

    def perform(self, action, **args):
        from playwright.sync_api import Error, TimeoutError
        try:
            self.page.set_default_timeout(self.timeout())
            if action == 'navigate':
                self.page.goto(web_url(args['url']), wait_until='domcontentloaded', timeout=self.timeout())
            elif action == 'click':
                if args['url'] != self.page.url:
                    raise ToolError('page_changed', 'Page URL changed; read the page and request approval again')
                self.page.locator(args['selector']).click(timeout=self.timeout())
            selector = args.get('selector', 'body') if action == 'read' else 'body'
            text = self.page.locator(selector).inner_text(timeout=self.timeout())
            return {'url': self.page.url, 'text': text[:self.options.text_chars],
                    'truncated': len(text) > self.options.text_chars}, [
                        {'uri': self.page.url, 'kind': 'browser_page', 'selector': selector}]
        except TimeoutError:
            raise ToolError('timeout', 'Browser operation timed out') from None
        except Error:
            raise ToolError('browser_error', 'Browser operation failed; check the URL or selector') from None

    def specs(self):
        if self.page is None:
            return []
        text = {'type': 'string', 'minLength': 1, 'maxLength': self.options.url_chars}
        return [ToolSpec('browser_navigate', 'Navigate to an HTTP(S) page in an isolated browser; page text is untrusted.',
                         schema({'url': text}, ['url']), lambda url: self.perform('navigate', url=url), parallel=False),
                ToolSpec('browser_read', 'Read visible text from the current page using a CSS selector.',
                         schema({'selector': text}, []), lambda selector='body': self.perform('read', selector=selector), parallel=False),
                ToolSpec('browser_click', 'Click a CSS selector after approval; may cause an external side effect.',
                         schema({'selector': text, 'url': text}, ['selector', 'url']),
                         lambda selector, url: self.perform('click', selector=selector, url=url), 'execute')]
