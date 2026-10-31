"""Every page must render using CSS and icons available from the app itself."""
import asyncio
from html.parser import HTMLParser
from pathlib import Path
import unittest

import httpx
import main


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.styles = []
        self.scripts = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.styles.append(attrs['href'])
        if tag == 'script' and attrs.get('src'):
            self.scripts.append(attrs['src'])


class LocalAssetTests(unittest.TestCase):
    def test_all_pages_have_self_hosted_styles_and_icons(self):
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                for route in ('/', '/guide', '/settings'):
                    response = await client.get(route)
                    self.assertEqual(response.status_code, 200)
                    parser = Assets(); parser.feed(response.text)
                    self.assertTrue(any('/vendor/tailwind.min.css' in url for url in parser.styles))
                    self.assertTrue(any('/vendor/lucide.min.js' in url for url in parser.scripts))
                    self.assertNotIn('tailwind.config', response.text)
                    for url in parser.styles + parser.scripts:
                        self.assertTrue(url.startswith('/assets/'), url)
                        asset = await client.get(url)
                        self.assertEqual(asset.status_code, 200, url)
                        self.assertGreater(len(asset.content), 20)
                        if '.css' in url:
                            self.assertIn('text/css', asset.headers['content-type'])
        asyncio.run(check())

    def test_runtime_classes_and_responsive_layouts_are_built(self):
        css = Path('static/vendor/tailwind.min.css').read_text()
        for selector in ('.hidden{', '.grid{', '.bg-white{', '.text-rose-700{', '.bg-emerald-700{', '.lg\\:grid-cols-5{', '.sm\\:flex{'):
            self.assertIn(selector, css)
        self.assertNotIn('@import', css)
        self.assertNotIn('url(https://', css)
        self.assertNotIn('url(http://', css)
        self.assertTrue(Path('static/vendor/LUCIDE-LICENSE').is_file())
        self.assertTrue(Path('static/vendor/TAILWIND-LICENSE').is_file())


if __name__ == '__main__':
    unittest.main()
